"""知乎热榜适配器（设计文档 §6.3 / 开发文档 §4.7）。"""

from __future__ import annotations

import json
import logging

import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources import UA
from daily_picks.sources.base import SourceAdapter, SourceError, to_local_datetime

logger = logging.getLogger(__name__)

_HOT_LISTS_URL = "https://api.zhihu.com/topstory/hot-lists/total?limit={limit}"


class ZhihuAdapter(SourceAdapter):
    """知乎热榜：hot-lists API；url 替换 api.zhihu.com → www.zhihu.com（移动端可正常打开）。"""

    name = "zhihu"

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        self.source_errors = 0
        articles: list[Article] = []
        try:
            r = await client.get(
                _HOT_LISTS_URL.format(limit=cfg.limit),
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data") or []
            if not items:
                # 429/403 已由 raise_for_status 抛错；data 为空视为本次失败（§6.3）
                raise SourceError("zhihu data 为空（限流或接口变更）")
            for item in items[:cfg.max_items_per_source]:
                t = item.get("target") or {}
                if not t.get("id") or not t.get("title"):
                    logger.warning("zhihu 条目缺 target.id/title，跳过")
                    continue
                url = (t.get("url") or "").replace("api.zhihu.com", "www.zhihu.com")
                try:
                    title, summary, url = self._clean(t["title"], t.get("excerpt"), url)
                except SourceError as e:
                    logger.warning("zhihu 条目清洗失败: %s", e)
                    continue
                articles.append(Article(
                    source=self.name,
                    source_key=str(t["id"]),
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=to_local_datetime(t.get("created")),
                    raw=json.dumps(item, ensure_ascii=False),  # answer_count 等不进库字段保留在 raw
                ))
        except Exception as e:  # noqa: BLE001 —— 失败隔离：本源失败返回 []（§6.7）
            self.source_errors += 1
            logger.warning("zhihu 采集失败: %s", e)
            return []
        return articles
