"""Hacker News 适配器（Algolia API，设计文档 §6.5 / 开发文档 §4.8）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources import UA
from daily_picks.sources.base import SourceAdapter, SourceError

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={hits_per_page}"
_ITEM_URL = "https://news.ycombinator.com/item?id={object_id}"


def parse_iso_z(s: str | None) -> datetime | None:
    """ISO 8601（含 Z）→ 本地 naive datetime；缺失/解析失败返回 None。

    Python 3.11 的 fromisoformat 原生支持 Z 后缀，无需 replace（ruff FURB162）。
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


class HNewsAdapter(SourceAdapter):
    """Hacker News 首页：Algolia 单请求即可，无需两步取 item；url 为空（Ask HN）回退 item 页。"""

    name = "hnews"

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        self.source_errors = 0
        articles: list[Article] = []
        try:
            r = await client.get(
                _SEARCH_URL.format(hits_per_page=cfg.hits_per_page), headers={"User-Agent": UA}
            )
            r.raise_for_status()
            data = r.json()
            for h in (data.get("hits") or [])[:cfg.max_items_per_source]:
                if not h.get("objectID") or not h.get("title"):
                    logger.warning("hnews 条目缺 objectID/title，跳过")
                    continue
                url = h.get("url") or _ITEM_URL.format(object_id=h["objectID"])
                try:
                    title, _summary, url = self._clean(h["title"], None, url)
                except SourceError as e:
                    logger.warning("hnews 条目清洗失败: %s", e)
                    continue
                articles.append(Article(
                    source=self.name,
                    source_key=str(h["objectID"]),
                    title=title,
                    url=url,
                    author=h.get("author"),
                    published_at=parse_iso_z(h.get("created_at")),
                    raw=json.dumps(h, ensure_ascii=False),  # points 等不进库字段保留在 raw
                ))
        except Exception as e:  # noqa: BLE001 —— 失败隔离：本源失败返回 []（§6.7）
            self.source_errors += 1
            logger.warning("hnews 采集失败: %s", e)
            return []
        return articles
