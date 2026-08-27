"""InfoQ 适配器（设计文档 §6.6 / 开发文档 §4.9）：复用 RssAdapter，仅固定 name 与默认 URL。"""

from __future__ import annotations

from typing import ClassVar

import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources.rss import RssAdapter


class InfoQAdapter(RssAdapter):
    """InfoQ RSS 2.0。⚠️ 不要用 https://www.infoq.cn/rss（返回 HTML 页面，已实测）；默认走 /feed。"""

    name = "infoq"
    default_urls: ClassVar[list[str]] = ["https://www.infoq.cn/feed"]

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        """cfg.urls 为空时用 default_urls；其余行为与 RssAdapter 完全一致。"""
        urls = cfg.urls or self.default_urls
        cfg = cfg.model_copy(update={"urls": urls})
        return await super().fetch(cfg, client)
