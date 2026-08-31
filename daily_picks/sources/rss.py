"""通用 RSS/Atom 适配器（设计文档 §6.1 / 开发文档 §4.5）。"""

from __future__ import annotations

import logging
from calendar import timegm
from datetime import datetime

import feedparser
import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources import UA
from daily_picks.sources.base import SourceAdapter, SourceError

logger = logging.getLogger(__name__)


class RssAdapter(SourceAdapter):
    """通用 RSS/Atom 适配器：feedparser 解析 + 字段清洗 + 单 URL 失败隔离。"""

    name = "rss"

    def __init__(self, extra_urls: list[str] | None = None):
        """extra_urls：source_registry 注册的 rss 源（v3 setup 向导产物），
        与 config.yaml rss.urls 并集采集（docs/04 §6.5）。"""
        self.extra_urls = extra_urls or []

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        """逐 URL 拉取；单个 URL 失败 continue（§4.5）；条目级清洗失败只跳过该条。"""
        self.source_errors = 0
        articles: list[Article] = []
        urls = list(dict.fromkeys(list(cfg.urls) + self.extra_urls))  # 并集去重保序
        for url in urls:
            try:
                r = await client.get(url, headers={"User-Agent": UA})
                r.raise_for_status()
                # 同步解析：RSS 量小，无需 to_thread（开发文档 §6）
                feed = feedparser.parse(r.content)
                for entry in feed.entries[:cfg.max_items_per_source]:
                    try:
                        articles.append(self._parse_entry(entry))
                    except SourceError as e:
                        logger.warning("rss 条目解析失败 url=%s: %s", url, e)
            except Exception as e:  # noqa: BLE001 —— 失败隔离：单 URL 失败不影响其余（§6.7）
                self.source_errors += 1
                logger.warning("rss 源失败 url=%s: %s", url, e)
                continue
        # 设计文档 §6.7：每源 max_items_per_source 截断（多 URL 汇总后再截一次）
        return articles[:cfg.max_items_per_source]

    def _parse_entry(self, entry) -> Article:
        """字段映射见设计文档 §6.1；字段缺失 → SourceError（调用方跳过该条）。"""
        source_key = entry.get("id") or entry.get("link")
        if not source_key:
            raise SourceError("条目缺少 id/link")
        title, summary, url = self._clean(
            entry.get("title", ""),
            entry.get("summary"),
            entry.get("link", ""),
        )
        return Article(
            source=self.name,
            source_key=source_key,
            title=title,
            url=url,
            author=entry.get("author"),
            summary=summary,
            published_at=self._parse_published(entry.get("published_parsed")),
        )

    @staticmethod
    def _parse_published(published_parsed) -> datetime | None:
        """time.struct_time（UTC）→ 本地 naive datetime；缺失/解析失败用当前时间（§6.1）。"""
        if published_parsed is None:
            return datetime.now()
        try:
            return datetime.fromtimestamp(timegm(published_parsed))
        except (ValueError, OverflowError):
            return datetime.now()
