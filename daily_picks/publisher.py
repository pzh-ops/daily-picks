"""推送渠道：企业微信 / Server酱 / Noop（设计文档 §9 / 开发文档 §4.15）。"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from daily_picks.config import PushConfig
from daily_picks.digest import WECOM_MAX_BYTES, truncate_bytes
from daily_picks.models import PushResult

logger = logging.getLogger("daily_picks.publisher")

# 企业微信机器人 webhook（设计文档 §9.1）
WECOM_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
# Server酱（设计文档 §9.2）
SERVERCHAN_URL = "https://sctapi.ftqq.com"
# 5xx/超时最大尝试次数（设计文档 §9.1：重试 3 次）
PUSH_RETRIES = 3

# `   链接：https://...` 独立行 → Server酱 markdown `[链接](url)`（设计文档 §8）
_LINK_LINE = re.compile(r"^(\s*)链接：(\S+)\s*$", re.MULTILINE)


def _to_markdown_links(content: str) -> str:
    """把独立成行的 `链接：URL` 转换为 `[链接](URL)`（Server酱支持 markdown 链接）。"""
    return _LINK_LINE.sub(r"\1[链接](\2)", content)


class Publisher(ABC):
    """推送接口（设计文档 §9.3）。"""

    @abstractmethod
    async def push(self, title: str, content: str) -> PushResult:
        """推送一条消息。预期失败（无 key/HTTP 错误/业务码非 0）返回 ok=False，不抛异常。"""


def _make_client(timeout_s: float) -> httpx.AsyncClient:
    """构造 AsyncClient；环境代理不可用（如 socks5 代理未装 socksio）时降级直连。"""
    try:
        return httpx.AsyncClient(timeout=timeout_s)
    except ImportError as e:
        logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
        return httpx.AsyncClient(timeout=timeout_s, trust_env=False)


class WecomPublisher(Publisher):
    """企业微信机器人 webhook：msgtype=text；errcode==0 成功；5xx/超时重试 3 次；4xx 不重试。"""

    def __init__(self, key: str, env_name: str = "WECOM_WEBHOOK_KEY", timeout_s: float = 10.0):
        self.key = key
        self.env_name = env_name
        self.timeout_s = timeout_s

    async def push(self, title: str, content: str) -> PushResult:
        if not self.key:
            return PushResult(
                ok=False, channel="wecom",
                detail=f"未配置 {self.env_name}，请在 .env 或 shell 环境中设置后重试",
            )
        if len(content.encode("utf-8")) > WECOM_MAX_BYTES:
            content = truncate_bytes(content, WECOM_MAX_BYTES)
        url = f"{WECOM_WEBHOOK_URL}?key={self.key}"
        body = {"msgtype": "text", "text": {"content": content}}
        try:
            resp = await self._post_with_retry(url, body)
        except httpx.HTTPStatusError as e:
            return PushResult(
                ok=False, channel="wecom",
                detail=f"HTTP {e.response.status_code}（重试 {PUSH_RETRIES} 次后仍失败）",
            )
        except httpx.TimeoutException:
            return PushResult(
                ok=False, channel="wecom", detail=f"请求超时（重试 {PUSH_RETRIES} 次后仍失败）"
            )
        except httpx.HTTPError as e:
            return PushResult(ok=False, channel="wecom", detail=f"网络错误: {e}")
        if resp.status_code >= 400:
            return PushResult(
                ok=False, channel="wecom",
                detail=f"HTTP {resp.status_code} {resp.text[:100]}（4xx 不重试）",
            )
        try:
            data = resp.json()
        except ValueError:
            return PushResult(ok=False, channel="wecom", detail=f"响应非 JSON: {resp.text[:100]}")
        errcode = data.get("errcode")
        errmsg = data.get("errmsg", "")
        if errcode == 0:
            return PushResult(ok=True, channel="wecom", detail=f"errcode=0 {errmsg}".strip())
        if errcode == 93000:
            return PushResult(
                ok=False, channel="wecom", detail=f"webhook key 无效（errcode=93000）: {errmsg}"
            )
        return PushResult(ok=False, channel="wecom", detail=f"errcode={errcode} {errmsg}".strip())

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(PUSH_RETRIES),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
        reraise=True,
    )
    async def _post_with_retry(self, url: str, body: dict) -> httpx.Response:
        """单次 POST；5xx 抛 HTTPStatusError 触发 tenacity 重试，其余状态原样返回（4xx 不重试）。"""
        async with _make_client(self.timeout_s) as client:
            resp = await client.post(url, json=body)
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp


class ServerChanPublisher(Publisher):
    """Server酱：POST sctapi.ftqq.com/{sendkey}.send，form: title/desp；code==0 成功。"""

    def __init__(self, sendkey: str, env_name: str = "SERVERCHAN_SENDKEY", timeout_s: float = 10.0):
        self.sendkey = sendkey
        self.env_name = env_name
        self.timeout_s = timeout_s

    async def push(self, title: str, content: str) -> PushResult:
        if not self.sendkey:
            return PushResult(
                ok=False, channel="serverchan",
                detail=f"未配置 {self.env_name}，请在 .env 或 shell 环境中设置后重试",
            )
        url = f"{SERVERCHAN_URL}/{self.sendkey}.send"
        form = {"title": title, "desp": _to_markdown_links(content)}
        try:
            async with _make_client(self.timeout_s) as client:
                resp = await client.post(url, data=form)
        except httpx.HTTPError as e:
            return PushResult(ok=False, channel="serverchan", detail=f"网络错误: {e}")
        if resp.status_code >= 400:
            return PushResult(
                ok=False, channel="serverchan", detail=f"HTTP {resp.status_code} {resp.text[:100]}"
            )
        try:
            data = resp.json()
        except ValueError:
            return PushResult(ok=False, channel="serverchan", detail=f"响应非 JSON: {resp.text[:100]}")
        code = data.get("code")
        message = data.get("message", "")
        if code == 0:
            return PushResult(ok=True, channel="serverchan", detail=f"code=0 {message}".strip())
        return PushResult(ok=False, channel="serverchan", detail=f"code={code} {message}".strip())


class NoopPublisher(Publisher):
    """只写本地文件，等价 dry-run（设计文档 §9.3）。content 写入 dry_run_file，目录自动创建。"""

    def __init__(self, dry_run_file: str):
        self.dry_run_file = dry_run_file

    async def push(self, title: str, content: str) -> PushResult:
        path = Path(self.dry_run_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return PushResult(ok=True, channel="noop", detail=f"已写入 {path}")


def create_publisher(cfg: PushConfig) -> Publisher:
    """按 cfg.push.provider 路由：wecom / serverchan / none→Noop。密钥从环境变量读取。"""
    if cfg.provider == "wecom":
        key = os.environ.get(cfg.wecom_webhook_key_env, "").strip()
        return WecomPublisher(key=key, env_name=cfg.wecom_webhook_key_env)
    if cfg.provider == "serverchan":
        sendkey = os.environ.get(cfg.serverchan_sendkey_env, "").strip()
        return ServerChanPublisher(sendkey=sendkey, env_name=cfg.serverchan_sendkey_env)
    return NoopPublisher(cfg.dry_run_file)
