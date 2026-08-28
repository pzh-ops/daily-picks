"""公共数据结构（开发文档 §3）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Article:
    """一条采集到的内容条目。"""

    source: str                 # 'rss' | 'bilibili' | 'zhihu' | 'juejin' | 'hnews' | 'infoq'
    source_key: str             # 源内唯一 ID
    title: str
    url: str
    author: str | None = None
    summary: str | None = None
    published_at: datetime | None = None
    raw: str | None = None      # 源原始 JSON（调试用，可空）

    @property
    def content_hash(self) -> str:
        """跨源去重：sha256(title + '\\n' + url) 十六进制。"""
        payload = f"{self.title}\n{self.url}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Pick:
    """LLM/规则选中的一条精选。"""

    article_id: int
    rank: int
    reason: str


@dataclass(slots=True)
class RankResult:
    """LLM 精排结果。"""

    picks: list[Pick]
    ok: bool                    # LLM 输出是否有效（False → 调用方降级）
    tokens_in: int = 0
    tokens_out: int = 0
    raw_text: str = ""          # LLM 原始返回（调试/日志）


@dataclass(slots=True)
class PushResult:
    """推送结果。"""

    ok: bool
    channel: str                # 'wecom' | 'serverchan' | 'noop'
    detail: str


@dataclass(slots=True)
class ScoredArticle:
    """规则打分后的文章。"""

    article: Article
    score: float
    article_id: int | None = None  # DB 主键：LLM 候选 JSON 与降级 Pick 回指文章用（开发文档 §3，M2 修订）


@dataclass(slots=True)
class ClickEvent:
    """Worker 端聚合后的一条点击事件（每文章每天一条；count 为当日点击次数）。"""

    remote_id: int      # worker 端 clicks.id（同步幂等键）
    article_id: int     # 本地 articles.id（注册短链时由本地写入 worker）
    click_date: str     # 'YYYY-MM-DD'（UTC，仅展示/记录，不参与回写逻辑）
    count: int          # 该文章当日点击次数
