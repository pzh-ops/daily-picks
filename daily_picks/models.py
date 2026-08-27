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
