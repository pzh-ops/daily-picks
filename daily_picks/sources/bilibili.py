"""B站热门适配器（设计文档 §6.2 / 开发文档 §4.6）。"""

from __future__ import annotations

import logging

import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources import UA
from daily_picks.sources.base import SourceAdapter, SourceError, to_local_datetime

logger = logging.getLogger(__name__)

_POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular?ps={ps}&pn=1"


class BilibiliAdapter(SourceAdapter):
    """B站热门榜：popular API，code==0 为成功；缺 aid/bvid/title 的条目跳过并计数。"""

    name = "bilibili"

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        self.source_errors = 0
        articles: list[Article] = []
        try:
            r = await client.get(
                _POPULAR_URL.format(ps=cfg.ps), headers={"User-Agent": UA}
            )  # 无 UA 会被拒（§6.2）
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise SourceError(f"bilibili api code={data.get('code')}")
            for it in (data.get("data") or {}).get("list", [])[:cfg.max_items_per_source]:
                if not it.get("aid") or not it.get("bvid") or not it.get("title"):
                    logger.warning("bilibili 条目缺 aid/bvid/title，跳过")
                    continue
                try:
                    title, summary, url = self._clean(
                        it["title"], it.get("desc"), f"https://www.bilibili.com/video/{it['bvid']}"
                    )
                except SourceError as e:
                    logger.warning("bilibili 条目清洗失败: %s", e)
                    continue
                articles.append(Article(
                    source=self.name,
                    source_key=str(it["aid"]),
                    title=title,
                    url=url,
                    author=(it.get("owner") or {}).get("name"),
                    summary=summary,
                    published_at=to_local_datetime(it.get("pubdate")),
                ))
        except Exception as e:  # noqa: BLE001 —— 失败隔离：本源失败返回 []（§6.7）
            self.source_errors += 1
            logger.warning("bilibili 采集失败: %s", e)
            return []
        return articles
