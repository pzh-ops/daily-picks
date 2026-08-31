"""点击追踪：短链注册、点击拉取、偏好回写（设计文档 §15 / 开发文档 §4.19）。

追踪服务（Cloudflare Worker，契约见设计文档 §15.3）：
- GET  /c/{code}         → 302 重定向原始 URL，同时记录点击（公开）
- POST /api/links        → 注册 {code, url, article_id}（Bearer 鉴权）
- GET  /api/clicks?after=N → 返回 id>N 的点击事件（Bearer 鉴权）
"""

from __future__ import annotations

import logging
import secrets

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from daily_picks.feedback import CLICK_CURSOR_KEY
from daily_picks.models import ClickEvent
from daily_picks.storage import Storage
from daily_picks.weights import _bump_keywords

logger = logging.getLogger("daily_picks.tracking")

# 短码字符表（62 进制字母数字）与长度（设计文档 §15.2）
CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CODE_LENGTH = 8
# 与 publisher 一致的网络重试（设计文档 N-003）
TRACK_RETRIES = 3
# 单页最多拉取事件数（worker 端同样限制，设计文档 §15.3）
MAX_CLICKS_PER_PAGE = 1000


class TrackingError(Exception):
    """追踪服务调用失败（HTTP 错误/响应非法/网络错误）。调用方应 fail-open（设计文档 §15.1）。"""


def gen_code(length: int = CODE_LENGTH) -> str:
    """生成随机短码（secrets 加密级随机，62 字符表）。"""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def build_tracking_url(base_url: str, code: str) -> str:
    """短链：{base 去尾斜杠}/c/{code}。"""
    return f"{base_url.rstrip('/')}/c/{code}"


class TrackingClient:
    """点击追踪服务 HTTP 客户端（契约见设计文档 §15.3）。5xx/超时重试 3 次（tenacity）。"""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(TRACK_RETRIES),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
        reraise=True,
    )
    async def _post(self, url: str, body: dict) -> httpx.Response:
        """POST JSON；5xx 抛 HTTPStatusError 触发重试，其余状态原样返回。"""
        try:
            client = httpx.AsyncClient(timeout=self.timeout_s)
        except ImportError as e:
            # 环境代理不可用（如 socks5 代理但未装 socksio）：降级为直连（对齐 publisher/_make_client、cli._collect）
            logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
            client = httpx.AsyncClient(timeout=self.timeout_s, trust_env=False)
        async with client:
            resp = await client.post(url, json=body, headers=self._auth_headers())
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp

    async def register_links(self, links: list[tuple[int, str]]) -> dict[int, str]:
        """为 (article_id, url) 列表注册短链；返回 {article_id: tracking_url}（仅成功项）。

        每条独立注册：失败（4xx/网络异常）记 WARNING 并跳过，不影响其余条目——
        调用方对缺失的 article_id 保留原始 URL（fail-open，设计文档 §15.1）。
        """
        registered: dict[int, str] = {}
        for article_id, url in links:
            code = gen_code()
            try:
                resp = await self._post(
                    f"{self.base_url}/api/links",
                    {"code": code, "url": url, "article_id": article_id},
                )
            except httpx.HTTPError as e:
                logger.warning("短链注册失败 article_id=%s: %s", article_id, e)
                continue
            if resp.status_code == 200:
                registered[article_id] = build_tracking_url(self.base_url, code)
            else:
                logger.warning("短链注册被拒绝 article_id=%s: HTTP %s %s",
                               article_id, resp.status_code, resp.text[:100])
        return registered

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(TRACK_RETRIES),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        """GET；5xx 抛 HTTPStatusError 触发重试，其余状态原样返回。"""
        try:
            client = httpx.AsyncClient(timeout=self.timeout_s)
        except ImportError as e:
            # 环境代理不可用（如 socks5 代理但未装 socksio）：降级为直连（对齐 publisher/_make_client、cli._collect）
            logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
            client = httpx.AsyncClient(timeout=self.timeout_s, trust_env=False)
        async with client:
            resp = await client.get(url, headers=self._auth_headers())
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp

    async def fetch_clicks(self, after: int) -> tuple[list[ClickEvent], bool]:
        """拉取 id > after 的点击事件；返回 (events, has_more)。失败抛 TrackingError。"""
        try:
            resp = await self._get(f"{self.base_url}/api/clicks?after={after}")
        except httpx.HTTPError as e:
            raise TrackingError(f"拉取点击失败: {e}") from e
        if resp.status_code != 200:
            raise TrackingError(f"拉取点击失败: HTTP {resp.status_code} {resp.text[:100]}")
        try:
            data = resp.json()
            events = [
                ClickEvent(remote_id=item["id"], article_id=item["article_id"],
                           click_date=item["click_date"], count=item["count"])
                for item in data["clicks"]
            ]
            has_more = bool(data["has_more"])
        except (ValueError, KeyError, TypeError) as e:
            raise TrackingError(f"点击响应非法: {e}") from e
        return events, has_more


def apply_click(storage: Storage, article_id: int, delta: float) -> dict:
    """点击 → 弱 like：文章 title+summary 命中关键词各 +delta（bump_keyword_weight 钳制 [0.2, 2.0]）。

    与 apply_feedback 的差异（设计文档 §10/§15.5）：
    - 不写 feedback 表（点击不是显式反馈）；步长默认 0.05（点击信号弱于主动 like 0.1）。
    - 文章不存在或未命中关键词 → 权重不变（不抛异常，同步游标照常推进）。
    返回 {'updated': [关键词...], 'missing': 文章是否不存在}。
    """
    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        return {"updated": [], "missing": True}
    hits = _bump_keywords(f"{rows[0]['title'] or ''} {rows[0]['summary'] or ''}", delta, storage)
    logger.info("点击回写 article_id=%s 命中关键词=%s delta=%s", article_id, hits, delta)
    return {"updated": hits, "missing": False}


async def sync_clicks(storage: Storage, client: TrackingClient,
                      delta: float = 0.05) -> dict:
    """游标式同步点击并回写权重（幂等，可反复调用，设计文档 §15.5）。

    流程：读 meta 表 last_click_sync_id 游标（默认 0）→ 循环 fetch_clicks(after) 直到
    has_more=False → 每个事件 record_click 幂等落库，仅新事件 apply_click →
    最后推进游标到已处理的最大 remote_id。
    网络/响应错误抛 TrackingError（调用方 fail-open）。
    返回 {'synced': 本次处理事件数, 'applied': 实际回写权重的文章数}。
    """
    cursor = storage.get_click_cursor()
    after = cursor
    synced = 0
    applied = 0
    while True:
        events, has_more = await client.fetch_clicks(after)
        for ev in events:
            if storage.record_click(article_id=ev.article_id, click_date=ev.click_date,
                                    remote_id=ev.remote_id, count=ev.count):
                result = apply_click(storage, ev.article_id, delta)
                if result["updated"]:
                    applied += 1
            synced += 1
            after = max(after, ev.remote_id)
        # 游标协作（docs/05 §4.1 修订）：同步时已回写权重，推进演化游标避免双重回写。
        # 逐页推进：若后续页 fetch_clicks 失败（部分失败），本页已回写行仍受演化游标保护，
        # 不会在下次同步时被 evolve_weights 重复 +delta。
        storage.set_meta(CLICK_CURSOR_KEY, str(storage.get_max_click_id()))
        if not has_more:
            break
        if not events:
            # 防御：worker 返回 has_more=True 但本页为空，终止避免死循环
            logger.warning("点击同步：has_more=True 但本页无事件，终止同步")
            break
    if after > cursor:
        storage.set_click_cursor(after)
    logger.info("点击同步完成 cursor=%s synced=%s applied=%s", after, synced, applied)
    return {"synced": synced, "applied": applied}
