"""内容源适配器基类（开发文档 §4.4）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from daily_picks.config import SourceSection
from daily_picks.models import Article

SUMMARY_MAX_CHARS = 200


def to_local_datetime(value: int | str | None) -> datetime | None:
    """unix 秒（int 或数字字符串）→ 本地 naive datetime；缺失/非法返回 None。

    修订说明（2026-08-27，M1）：掘金真实 API 的 ctime 为字符串（实测），
    直接 fromtimestamp 会抛 TypeError 导致本源整体失败；统一走此容错转换。
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class SourceError(Exception):
    """适配器内部使用，不向上抛（由调用方隔离）。"""


class SourceAdapter(ABC):
    """内容源适配器抽象基类；name 与 config.sources.enabled 中的名字一致。"""

    name: str

    def __init__(self) -> None:
        self.source_errors = 0  # 本次 fetch 的源级错误计数（设计文档 §6.7），fetch 开始时清零

    @abstractmethod
    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        """拉取并清洗；任何异常自行捕获记日志，返回 []（或部分成功列表）。"""

    def _clean(self, title: str, summary: str | None, url: str) -> tuple[str, str | None, str]:
        """清洗：strip；空 title → SourceError；url 非 http(s) → SourceError；
        summary 去 HTML 标签（bs4）并截断 200 字。"""
        title = title.strip()
        if not title:
            raise SourceError("标题为空")
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise SourceError(f"非法 URL: {url!r}")
        if summary:
            text = BeautifulSoup(summary, "lxml").get_text(" ", strip=True)
            text = " ".join(text.split())
            if len(text) > SUMMARY_MAX_CHARS:
                text = text[:SUMMARY_MAX_CHARS]
            summary = text or None
        return title, summary, url
