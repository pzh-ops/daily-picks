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

from daily_picks.models import ClickEvent

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
