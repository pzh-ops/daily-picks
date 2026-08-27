"""规则打分 + 候选筛选 + LLM 精排编排（设计文档 §7 / 开发文档 §4.13）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from daily_picks.llm import LLMClient, build_candidates_json
from daily_picks.models import Article, Pick, ScoredArticle

logger = logging.getLogger("daily_picks.ranker")


def rule_score(article: Article, weights: dict[str, float], now: datetime,
               source_weight: float = 0.0,
               feedback_kinds: list[str] | None = None) -> float:
    """设计文档 §7.1 公式：keyword_score + source_weight + recency_bonus + feedback_bias。

    关键词在 title+summary 中做子串匹配（大小写不敏感），每关键词单篇最多计 3 次命中；
    时效：距今 ≤24h +2.0，≤48h +1.0，否则 0；feedback_kinds 含 like → +1.0，dislike → -1.0。
    """
    text = f"{article.title or ''}\n{article.summary or ''}".lower()
    keyword_score = 0.0
    for keyword, weight in weights.items():
        if not keyword:
            continue
        hits = min(text.count(keyword.lower()), 3)
        keyword_score += weight * hits

    recency_bonus = 0.0
    if article.published_at is not None:
        age = now - article.published_at
        if age <= timedelta(hours=24):
            recency_bonus = 2.0
        elif age <= timedelta(hours=48):
            recency_bonus = 1.0

    feedback_bias = 0.0
    for kind in feedback_kinds or []:
        if kind == "like":
            feedback_bias += 1.0
        elif kind == "dislike":
            feedback_bias -= 1.0

    return keyword_score + source_weight + recency_bonus + feedback_bias


def select_candidates(articles: list[ScoredArticle], max_candidates: int) -> list[ScoredArticle]:
    """降序取前 max_candidates；若全部 0 分 → 每源最高分 1 条作为候选（保底策略）。"""
    ordered = sorted(articles, key=lambda sa: sa.score, reverse=True)
    if not ordered:
        return []
    if all(sa.score == 0.0 for sa in ordered):
        # 保底：无关键词命中时，每源取分数最高的 1 条，保证 LLM 有材料可选
        best_per_source: dict[str, ScoredArticle] = {}
        for sa in ordered:
            current = best_per_source.get(sa.article.source)
            if current is None or sa.score > current.score:
                best_per_source[sa.article.source] = sa
        return sorted(best_per_source.values(), key=lambda sa: sa.score, reverse=True)[:max_candidates]
    return ordered[:max_candidates]


async def rank_and_pick(candidates: list[ScoredArticle], llm: LLMClient, weights: dict[str, float],
                        top_n: int, max_input_chars: int) -> tuple[list[Pick], bool]:
    """LLM 精排；ok=False 降级为规则分 top_n。返回 (picks, fallback_used)。"""
    if not candidates:
        return [], False
    profile = LLMClient.build_profile_json(weights)
    # 与 LLMClient.rank 内部同口径的输入预裁剪（开发文档 §4.13，M2 修订）
    _text, sent, _ids = build_candidates_json(candidates, max_input_chars)
    result = await llm.rank(sent, profile, top_n)
    if result.ok:
        return result.picks, False
    logger.warning("LLM 精排失败，降级为规则分排序")
    ordered = sorted(candidates, key=lambda sa: sa.score, reverse=True)
    picks = [
        Pick(
            article_id=sa.article_id if sa.article_id is not None else i + 1,
            rank=i + 1,
            reason=f"规则分 {sa.score:.1f}",
        )
        for i, sa in enumerate(ordered[:top_n])
    ]
    return picks, True
