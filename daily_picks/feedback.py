"""偏好反馈：like/dislike → 关键词权重更新（设计文档 §10 / 开发文档 §4.16）。"""

from __future__ import annotations

import logging

from daily_picks.storage import Storage

logger = logging.getLogger("daily_picks.feedback")

# 权重调整步长（设计文档 §10）
LIKE_DELTA = 0.1
DISLIKE_DELTA = -0.05


class FeedbackError(Exception):
    """反馈错误（非法 kind / 文章不存在），CLI 捕获后提示并退出码 1。"""


def hit_keywords(title: str | None, summary: str | None, weights: dict[str, float]) -> list[str]:
    """title+summary 中命中的关键词（大小写不敏感子串匹配，对齐 §7.1 keyword_score）。
    公开供 tracking.apply_click 复用（点击回写与 like 同口径取词）。"""
    text = f"{title or ''} {summary or ''}".lower()
    return [kw for kw in weights if kw and kw.lower() in text]


def apply_feedback(storage: Storage, article_id: int, kind: str,
                   extra_keyword: str | None = None) -> dict:
    """应用 like/dislike 反馈，返回 {'updated': [关键词...], 'article_state': 当前状态}。

    规则（设计文档 §10）：
    - like：命中关键词各 +0.1（上限 2.0）；无命中且给了 extra_keyword → 该词 +0.1 并入库。
    - dislike：命中关键词各 -0.05（下限 0.2）；文章 state='dismissed'。
    - 同文章重复反馈：只保留最后一次（storage.add_feedback 先删旧反馈再插入）。
    """
    if kind not in {"like", "dislike"}:
        raise FeedbackError(f"非法反馈类型: {kind!r}（可选 like | dislike）")

    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        raise FeedbackError(f"文章 id={article_id} 不存在，无法提交反馈")

    weights = storage.get_interest_weights()
    hits = hit_keywords(rows[0]["title"], rows[0]["summary"], weights)

    if kind == "like":
        if hits:
            updated = hits
            for kw in hits:
                storage.bump_keyword_weight(kw, LIKE_DELTA)
        elif extra_keyword:
            updated = [extra_keyword]
            storage.bump_keyword_weight(extra_keyword, LIKE_DELTA)
        else:
            updated = []
    else:  # dislike
        updated = hits
        for kw in hits:
            storage.bump_keyword_weight(kw, DISLIKE_DELTA)

    storage.add_feedback(article_id, kind)

    article_state = rows[0]["state"]
    if kind == "dislike":
        storage.set_state(article_id, "dismissed")
        article_state = "dismissed"

    logger.info("反馈已应用 article_id=%s kind=%s updated=%s state=%s",
                article_id, kind, updated, article_state)
    return {"updated": updated, "article_state": article_state}
