"""T-FBK 反馈模块用例（测试文档 §4.8）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from daily_picks.feedback import FeedbackError, apply_feedback
from daily_picks.models import Article
from daily_picks.ranker import rule_score


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    """插入一篇文章并返回 id（title/summary 可定制以控制关键词命中）。"""
    ids = storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])
    return ids[0]


def weight(storage, keyword: str) -> float | None:
    """读单个关键词权重（未入库返回 None）。"""
    return storage.get_interest_weights().get(keyword)


class TestApplyFeedback:
    def test_like_bumps_hit_keywords(self, tmp_db):  # T-FBK-01
        tmp_db.bump_keyword_weight("AI", 0)  # AI = 1.0
        aid = seed_article(tmp_db)
        result = apply_feedback(tmp_db, aid, "like")
        assert result["updated"] == ["AI"]
        assert result["article_state"] == "new"
        assert weight(tmp_db, "AI") == pytest.approx(1.1)
        assert tmp_db.get_feedback_kinds(aid) == ["like"]

    def test_dislike_lowers_and_dismisses(self, tmp_db):  # T-FBK-02
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        result = apply_feedback(tmp_db, aid, "dislike")
        assert result["updated"] == ["AI"]
        assert result["article_state"] == "dismissed"
        assert weight(tmp_db, "AI") == pytest.approx(0.95)
        assert tmp_db.get_articles_by_ids([aid])[0]["state"] == "dismissed"

    def test_like_clamped_at_upper_bound(self, tmp_db):  # T-FBK-03
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        for _ in range(20):
            apply_feedback(tmp_db, aid, "like")
            assert weight(tmp_db, "AI") <= 2.0  # 恒 ≤2.0
        assert weight(tmp_db, "AI") == 2.0

    def test_like_no_hit_with_extra_keyword(self, tmp_db):  # T-FBK-04
        tmp_db.bump_keyword_weight("AI", 0)  # 表内有词但文章未命中
        aid = seed_article(tmp_db, title="今天天气不错", summary="晴转多云")
        result = apply_feedback(tmp_db, aid, "like", extra_keyword="开源")
        assert result["updated"] == ["开源"]
        assert weight(tmp_db, "开源") == pytest.approx(1.1)  # 新词默认 1.0 + 0.1，已入库
        assert weight(tmp_db, "AI") == 1.0  # 未命中不动

    def test_repeat_feedback_keeps_last(self, tmp_db):  # T-FBK-05
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        apply_feedback(tmp_db, aid, "like")
        result = apply_feedback(tmp_db, aid, "dislike")
        assert result["article_state"] == "dismissed"
        assert tmp_db.get_feedback_kinds(aid) == ["dislike"]  # 最后状态为 dislike
        rows = tmp_db._conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        assert rows == 1  # 先删旧反馈再插入
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 1.0 + 0.1 - 0.05

    def test_feedback_affects_next_ranking(self, tmp_db):  # T-FBK-06
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, summary=None)  # 仅标题命中 1 次（"AI"）
        apply_feedback(tmp_db, aid, "like")
        row = tmp_db.get_articles_by_ids([aid])[0]
        article = Article(source=row["source"], source_key=row["source_key"],
                          title=row["title"], url=row["url"], summary=row["summary"])
        score = rule_score(article, tmp_db.get_interest_weights(), datetime.now(),
                           feedback_kinds=tmp_db.get_feedback_kinds(aid))
        assert score == pytest.approx(1.1 + 1.0)  # 关键词 1.1 + like bias 1.0（无时效/来源加成）


class TestApplyFeedbackEdges:
    """补充用例：坏路径与匹配边界（保证 feedback.py 覆盖率 ≥85%）。"""

    def test_unknown_article_raises(self, tmp_db):
        with pytest.raises(FeedbackError, match="不存在"):
            apply_feedback(tmp_db, 999, "like")

    def test_invalid_kind_raises(self, tmp_db):
        with pytest.raises(FeedbackError, match="like | dislike"):
            apply_feedback(tmp_db, 1, "meh")

    def test_like_no_hit_without_keyword_updates_nothing(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="今天天气不错", summary="晴转多云")
        result = apply_feedback(tmp_db, aid, "like")
        assert result["updated"] == []
        assert result["article_state"] == "new"
        assert weight(tmp_db, "AI") == 1.0
        assert tmp_db.get_feedback_kinds(aid) == ["like"]  # 反馈本身仍记录

    def test_dislike_no_hit_still_dismisses(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="今天天气不错", summary="晴转多云")
        result = apply_feedback(tmp_db, aid, "dislike")
        assert result["updated"] == []
        assert result["article_state"] == "dismissed"

    def test_hit_case_insensitive_and_in_summary(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="本周周报", summary="本周主要学习 ai 工程实践")
        result = apply_feedback(tmp_db, aid, "like")
        assert result["updated"] == ["AI"]  # 摘要命中 + 大小写不敏感
        assert weight(tmp_db, "AI") == pytest.approx(1.1)

    def test_like_with_hit_ignores_extra_keyword(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        result = apply_feedback(tmp_db, aid, "like", extra_keyword="开源")
        assert result["updated"] == ["AI"]  # 有命中时 extra_keyword 不参与
        assert weight(tmp_db, "开源") is None


def test_hit_keywords_public_helper():  # T-FBK-HIT
    """hit_keywords 关键词命中口径参考实现：大小写不敏感、返回权重表插入序。"""
    from daily_picks.feedback import hit_keywords
    assert hit_keywords("AI 编程工具", "大模型实战", {"AI": 1.0, "大模型": 1.5}) == ["AI", "大模型"]
    assert hit_keywords("Rust 入门", None, {"AI": 1.0, "rust": 1.0}) == ["rust"]
    assert hit_keywords("前端", "后端", {}) == []
