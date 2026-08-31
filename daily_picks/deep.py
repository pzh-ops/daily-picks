"""深度评分 + 关键词提取（docs/04 §6.2 / docs/05 §2.1，M11 核心）。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from daily_picks.llm import LLMClient, LLMError
from daily_picks.models import Article, ScoredArticle

logger = logging.getLogger("daily_picks.deep")

DEEP_SCORE_MIN: float = 0.0    # LLM 深度评分（0-100）输出下限校验
KEYWORDS_MIN: int = 3
KEYWORDS_MAX: int = 5
DEEP_TIMEOUT_S: float = 30.0   # 单篇分析超时秒数（docs/05 §2.1）
DEEP_MIN_COUNT: int = 5        # 过滤后不足该数触发降阈值重试（对齐 profile.top_n 默认值，docs/04 §6.2 修订）

# 推荐理由禁用词（docs/04 §7，写死在代码侧校验）
BANNED_REASONS = ("深入浅出", "受益匪浅", "干货满满", "值得一读", "不容错过")

# 深度分析 system prompt（docs/05 §5.1 模板，原样使用）
DEEP_SYSTEM_PROMPT = (
    "你是资深内容编辑，评估文章思考深度。评分标准：观点原创性(40%)、论证扎实度(30%)、"
    "信息密度(20%)、对读者的启发价值(10%)。推荐理由必须引用文章的具体观点/数据/场景，"
    "禁止使用任何套话。"
)


@dataclass
class DeepResult:
    article_id: int
    deep_score: int             # 0-100
    keywords: list[str]         # 3-5 个
    reason: str                 # 具体推荐理由（引用文章细节，禁止空泛）
    ok: bool                    # LLM 输出有效？


async def deep_analyze(article: Article, llm: LLMClient,
                       weights: dict[str, float]) -> DeepResult:
    """LLM 深度分析：输入 title+summary+url+用户兴趣，输出 {deep_score, keywords, reason}。

    解析失败/输出非法 → ok=False（不抛异常，fail-open 依据，docs/05 §2.1）。
    article_id 由调用方（deep_filter）回填——本函数签名无 id 参数。
    """
    keywords_text = "、".join(sorted(weights, key=weights.get, reverse=True)) or "（暂无）"
    user = (
        f"标题：{article.title}\n摘要：{article.summary or ''}\nURL：{article.url}\n"
        f"用户兴趣关键词：{keywords_text}\n"
        '输出 JSON：{"deep_score": <0-100整数>, "keywords": [3-5个名词短语], '
        '"reason": "2-3句，含具体引用，禁止\'深入浅出/受益匪浅/干货满满/值得一读/不容错过\'"}'
    )
    try:
        text = await llm.chat(DEEP_SYSTEM_PROMPT, user, json_mode=True)
        data = json.loads(text or "{}")
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        logger.warning("deep_analyze 失败（fail-open）: %s", e)
        return DeepResult(article_id=0, deep_score=0, keywords=[], reason="", ok=False)
    if not isinstance(data, dict):
        return DeepResult(article_id=0, deep_score=0, keywords=[], reason="", ok=False)

    ok = True
    score_raw = data.get("deep_score")
    if not (isinstance(score_raw, int) and not isinstance(score_raw, bool)
            and DEEP_SCORE_MIN <= score_raw <= 100):
        ok = False
    keywords_raw = data.get("keywords")
    if not isinstance(keywords_raw, list) or len(keywords_raw) < KEYWORDS_MIN:
        ok = False
    keywords = [str(k) for k in keywords_raw[:KEYWORDS_MAX]] if isinstance(keywords_raw, list) else []

    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = f"文章围绕 {article.title} 展开"   # docs/05 §2.1 回退文案
    elif any(word in reason for word in BANNED_REASONS):
        ok = False

    return DeepResult(article_id=0, deep_score=score_raw if isinstance(score_raw, int) else 0,
                      keywords=keywords, reason=reason, ok=ok)


async def deep_filter(candidates: list[ScoredArticle], llm: LLMClient,
                      threshold: int,
                      weights: dict[str, float] | None = None) -> tuple[list[ScoredArticle], list[DeepResult]]:
    """批量 deep_analyze（并发 ≤3，单篇 DEEP_TIMEOUT_S 超时），保留 deep_score >= threshold 的候选。

    ok=False（LLM 失败/超时/输出非法）的文章**保留**（fail-open，不因 deep 故障丢候选）；
    过滤后不足 DEEP_MIN_COUNT 条且 threshold-10 >= 0 时，按首轮结果降阈值重过滤一次
    （不重复调用 LLM，docs/04 §10 降级表）。返回 (过滤后候选, 全量 DeepResult 列表)。
    """
    if not candidates:
        return [], []
    weights = weights or {}
    sem = asyncio.Semaphore(3)

    async def _analyze_one(sa: ScoredArticle) -> DeepResult:
        async with sem:
            try:
                result = await asyncio.wait_for(
                    deep_analyze(sa.article, llm, weights), timeout=DEEP_TIMEOUT_S)
            except TimeoutError:
                logger.warning("deep 分析超时 article_id=%s（fail-open 保留）", sa.article_id)
                return DeepResult(article_id=sa.article_id, deep_score=0,
                                  keywords=[], reason="", ok=False)
            result.article_id = sa.article_id if sa.article_id is not None else 0
            return result

    results = await asyncio.gather(*(_analyze_one(sa) for sa in candidates))

    def _keep(r: DeepResult, th: int) -> bool:
        return (not r.ok) or r.deep_score >= th

    filtered = [sa for sa, r in zip(candidates, results) if _keep(r, threshold)]
    if len(filtered) < DEEP_MIN_COUNT and threshold - 10 >= 0:
        logger.info("deep 过滤后不足 %d 条，降阈值 %d 重试一次", DEEP_MIN_COUNT, threshold - 10)
        filtered = [sa for sa, r in zip(candidates, results) if _keep(r, threshold - 10)]
    return filtered, results


def format_keywords(keywords: list[str]) -> str:
    """'k1、k2、k3' 顿号拼接，超 5 个截断（docs/04 §6.2）。"""
    return "、".join(keywords[:KEYWORDS_MAX])
