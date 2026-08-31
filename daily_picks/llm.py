"""DeepSeek 客户端：重试、JSON 解析、token/成本统计（设计文档 §7.2 / 开发文档 §4.12）。"""

from __future__ import annotations

import json
import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from daily_picks.config import LLMConfig
from daily_picks.models import Pick, RankResult, ScoredArticle

logger = logging.getLogger("daily_picks.llm")

INPUT_USD_PER_1M = 0.66  # deepseek-v4-pro 输入（缓存未命中/闲时）
OUTPUT_USD_PER_1M = 1.98

# _chat 对 5xx/429/超时的最大尝试次数（设计文档 §7.2：重试 3 次）
LLM_RETRIES = 3

# System prompt（设计文档 §7.2 固定模板，原样使用；{top_n}/{profile_json} 运行时替换）
SYSTEM_PROMPT = """你是一个个人内容精选助手。用户每天会收到你挑选的 {top_n} 条内容。
候选列表用 JSON 数组给出，每个元素含 article_id/title/summary/source。
用户兴趣画像（关键词权重，数值越大越重要）：
{profile_json}

要求：
1. 从候选中选出恰好 {top_n} 条最符合用户兴趣的内容（候选不足则全选）。
2. 排除：标题党、广告、重复讨论同一事件的内容、纯口水/无信息量内容。
3. 多样性优先：尽量覆盖不同来源与话题，不要全选同一来源。
4. 只输出 JSON，不要任何其他文字，格式：
{"picks":[{"article_id": <整数>, "rank": <1..N>, "reason": "<30字以内中文理由>"}]}"""


class LLMError(Exception):
    """LLM API 调用失败（HTTP 错误/超时/重试耗尽）。"""


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """成本估算：tokens_in/1e6*0.66 + tokens_out/1e6*1.98（deepseek-v4-pro）。"""
    return tokens_in / 1e6 * INPUT_USD_PER_1M + tokens_out / 1e6 * OUTPUT_USD_PER_1M


def build_candidates_json(candidates: list[ScoredArticle],
                          max_input_chars: int) -> tuple[str, list[ScoredArticle], list[int]]:
    """按候选顺序拼接候选 JSON（{"candidates":[...]}），超 max_input_chars 截断（保留靠前候选）。

    返回 (JSON 文本, 实际发送的候选, 对应 article_id 列表)。rank() 与 rank_and_pick() 共用（同口径裁剪）。
    """
    sent: list[ScoredArticle] = []
    ids: list[int] = []
    pieces: list[str] = []
    for i, sa in enumerate(candidates, start=1):
        article_id = sa.article_id if sa.article_id is not None else i
        item = json.dumps(
            {
                "article_id": article_id,
                "title": sa.article.title or "",
                "summary": sa.article.summary or "",
                "source": sa.article.source,
            },
            ensure_ascii=False,
        )
        tentative = '{"candidates":[' + ",".join([*pieces, item]) + "]}"
        if len(tentative) > max_input_chars:
            break
        pieces.append(item)
        sent.append(sa)
        ids.append(article_id)
    return '{"candidates":[' + ",".join(pieces) + "]}", sent, ids


def _system_prompt(top_n: int, profile: str) -> str:
    """设计文档 §7.2 模板的占位替换（用 replace 避免模板内 JSON 花括号被 format 误解析）。"""
    return SYSTEM_PROMPT.replace("{top_n}", str(top_n)).replace("{profile_json}", profile)


def _strip_fences(text: str) -> str:
    """剥离可能的 ```json 围栏（首行与末行）。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _is_int(value) -> bool:
    """int 且非 bool（json.loads 会产出这两种类型）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _extract_content(data) -> str:
    """从 chat/completions 响应取 choices[0].message.content；结构不符返回 ''。"""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def _extract_usage(data) -> tuple[int, int]:
    """取 usage（顶层 usage；兼容测试 fixtures 的 choices[0].usage 写法）。"""
    if not isinstance(data, dict):
        return 0, 0
    usage = data.get("usage")
    if not isinstance(usage, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            usage = choices[0].get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)


class LLMClient:
    """DeepSeek（OpenAI 兼容接口）客户端。网络/解析异常都在 rank() 内降级，不向上抛。"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        # 最近一次 rank() 的 usage（供 run_once 记账；开发文档 §4.12，M2 修订）
        self.last_tokens_in = 0
        self.last_tokens_out = 0

    async def rank(self, candidates: list[ScoredArticle], profile: str, top_n: int) -> RankResult:
        """1) 拼候选 JSON → 2) chat() → 3) parse_response()。

        网络/解析异常都返回 RankResult(ok=False)，不抛（设计文档 §7.3 降级）。
        """
        user_content, _sent, sent_ids = build_candidates_json(candidates, self.cfg.max_input_chars)
        messages = [
            {"role": "system", "content": _system_prompt(top_n, profile)},
            {"role": "user", "content": user_content},
        ]
        try:
            data = await self._chat(messages)
        except Exception as e:  # noqa: BLE001 —— 网络/密钥异常统一降级，不向上抛
            logger.warning("LLM 请求失败，将降级为规则分排序: %s", e)
            return RankResult(picks=[], ok=False)
        tokens_in, tokens_out = _extract_usage(data)
        self.last_tokens_in = tokens_in
        self.last_tokens_out = tokens_out
        result = self.parse_response(_extract_content(data), valid_ids=set(sent_ids), top_n=top_n)
        result.tokens_in = tokens_in
        result.tokens_out = tokens_out
        if not result.ok:
            # 诊断：校验失败时记录原始输出前 300 字，便于定位（如 thinking 耗尽 max_tokens 致 content 为空）
            logger.warning("LLM 输出校验失败（tokens_in=%d tokens_out=%d），将降级为规则分。原始输出: %s",
                           tokens_in, tokens_out, (result.raw_text or "")[:300])
        return result

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        """通用单轮 chat（docs/05 §0）：返回 choices[0].message.content；
        json_mode 时剥离 ```json 围栏。网络/密钥异常抛 LLMError（调用方 fail-open）。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = await self._chat(messages)
        content = _extract_content(data)
        return _strip_fences(content) if json_mode else content

    async def _chat(self, messages: list[dict], **kw) -> dict:
        """POST {base_url}/chat/completions；5xx/429/超时重试 3 次；4xx 不重试直接抛 LLMError。

        返回完整响应 JSON（含 usage）。api_key 缺失抛 ConfigError。
        """
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "response_format": {"type": "json_object"},
            **kw,
        }
        try:
            return await self._post_with_retry(url, body)
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM API 返回 HTTP {e.response.status_code}（重试 {LLM_RETRIES} 次后仍失败）") from e
        except httpx.TimeoutException as e:
            raise LLMError(f"LLM API 请求超时（重试 {LLM_RETRIES} 次后仍失败）") from e

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(LLM_RETRIES),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
        reraise=True,
    )
    async def _post_with_retry(self, url: str, body: dict) -> dict:
        """单次 POST（tenacity 指数退避重试）；5xx/429 抛 HTTPStatusError 触发重试，4xx 抛 LLMError 不重试。"""
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        try:
            client = httpx.AsyncClient(timeout=self.cfg.timeout_s)
        except ImportError as e:
            # 环境代理不可用（如 socks5 代理但未装 socksio）：降级为直连（对齐 cli._collect 的处理）
            logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
            client = httpx.AsyncClient(timeout=self.cfg.timeout_s, trust_env=False)
        async with client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                resp.raise_for_status()
            if resp.status_code >= 400:
                raise LLMError(f"LLM API 请求失败: HTTP {resp.status_code} {resp.text[:200]}")
            return resp.json()

    @staticmethod
    def parse_response(text: str, valid_ids: set[int], top_n: int) -> RankResult:
        """剥离 ```json 围栏 → json.loads → 校验（设计文档 §7.2）→ RankResult(ok=True/False)。"""
        stripped = _strip_fences(text)
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return RankResult(picks=[], ok=False, raw_text=text)
        if not isinstance(data, dict):
            return RankResult(picks=[], ok=False, raw_text=text)
        picks_raw = data.get("picks")
        if not isinstance(picks_raw, list):
            return RankResult(picks=[], ok=False, raw_text=text)
        picks: list[Pick] = []
        ranks: set[int] = set()
        for item in picks_raw:
            if not isinstance(item, dict):
                return RankResult(picks=[], ok=False, raw_text=text)
            article_id = item.get("article_id")
            rank = item.get("rank")
            reason = item.get("reason")
            if not _is_int(article_id) or not _is_int(rank) or not isinstance(reason, str):
                return RankResult(picks=[], ok=False, raw_text=text)
            if article_id not in valid_ids or rank in ranks:
                return RankResult(picks=[], ok=False, raw_text=text)
            ranks.add(rank)
            picks.append(Pick(article_id=article_id, rank=rank, reason=reason))
        if len(picks) > top_n:
            return RankResult(picks=[], ok=False, raw_text=text)
        return RankResult(picks=picks, ok=True, raw_text=text)

    @staticmethod
    def build_profile_json(weights: dict[str, float]) -> str:
        """兴趣画像 JSON（关键词→权重），数值越大越重要（设计文档 §7.2）。"""
        return json.dumps({k: round(v, 3) for k, v in weights.items()}, ensure_ascii=False)
