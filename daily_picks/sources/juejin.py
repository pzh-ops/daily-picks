"""掘金推荐适配器（设计文档 §6.4 / 开发文档 §4.10）。"""

from __future__ import annotations

import logging

import httpx

from daily_picks.config import SourceSection
from daily_picks.models import Article
from daily_picks.sources import UA
from daily_picks.sources.base import SourceAdapter, SourceError, to_local_datetime

logger = logging.getLogger(__name__)

_RECOMMEND_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
_HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/json",
    "Origin": "https://juejin.cn",
}


class JuejinAdapter(SourceAdapter):
    """掘金推荐流：recommend_all_feed POST；err_no==0 为成功。"""

    name = "juejin"

    async def fetch(self, cfg: SourceSection, client: httpx.AsyncClient) -> list[Article]:
        self.source_errors = 0
        articles: list[Article] = []
        try:
            r = await client.post(
                _RECOMMEND_URL,
                json={"id_type": 2, "sort_type": 200, "cursor": "0", "limit": cfg.limit},
                headers=_HEADERS,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("err_no") != 0:
                raise SourceError(f"juejin api err_no={data.get('err_no')}")
            for item in (data.get("data") or [])[:cfg.max_items_per_source]:
                info = item.get("item_info") or {}
                ai = info.get("article_info") or {}
                if not ai.get("article_id") or not ai.get("title"):
                    logger.warning("juejin 条目缺 article_id/title，跳过")
                    continue
                try:
                    title, summary, url = self._clean(
                        ai["title"], ai.get("brief_content"), f"https://juejin.cn/post/{ai['article_id']}"
                    )
                except SourceError as e:
                    logger.warning("juejin 条目清洗失败: %s", e)
                    continue
                articles.append(Article(
                    source=self.name,
                    source_key=str(ai["article_id"]),
                    title=title,
                    url=url,
                    author=(info.get("author_user_info") or {}).get("user_name"),
                    summary=summary,
                    published_at=to_local_datetime(ai.get("ctime")),  # 实测 ctime 为字符串，容错转换
                ))
        except Exception as e:  # noqa: BLE001 —— 失败隔离：本源失败返回 []（§6.7）
            self.source_errors += 1
            logger.warning("juejin 采集失败: %s", e)
            return []
        return articles
