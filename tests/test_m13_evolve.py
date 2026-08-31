"""M13 用例：权重演化（测试文档 docs/06 §5；LLM 无关，纯存储操作）。

注意：evolve_weights 在 Task 15 才实现，本文件头部暂不导入（Task 15 追加导入行）。
"""

from __future__ import annotations

import pytest

from daily_picks.feedback import CLICK_CURSOR_KEY, FEEDBACK_CURSOR_KEY, evolve_weights
from daily_picks.models import Article
from daily_picks.weights import _bump_keywords


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    ids = storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])
    return ids[0]


def weight(storage, keyword: str) -> float | None:
    return storage.get_interest_weights().get(keyword)


class TestBumpKeywords:
    """补充用例：公共函数（tracking.apply_click 与 evolve_weights 共用）。"""

    def test_hits_and_bumps(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        tmp_db.bump_keyword_weight("大模型", 0)
        hits = _bump_keywords("本周 AI 与 大模型 实践", 0.05, tmp_db)
        assert hits == ["AI", "大模型"]
        assert weight(tmp_db, "AI") == pytest.approx(1.05)

    def test_case_insensitive_and_no_hit(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        assert _bump_keywords("本周学习 ai 工程", 0.05, tmp_db) == ["AI"]
        assert _bump_keywords("今天天气不错", 0.05, tmp_db) == []
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 未命中不动


class TestEvolveWeights:
    def _click(self, tmp_db, aid: int, remote_id: int) -> None:
        tmp_db.record_click(article_id=aid, click_date="2026-08-27", remote_id=remote_id, count=1)

    # T-EV-01 点击演化
    def test_click_evolve_bumps_keywords(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        self._click(tmp_db, aid, remote_id=1)
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)

    # T-EV-02 游标幂等
    def test_evolve_twice_no_double_bump(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        self._click(tmp_db, aid, remote_id=1)
        evolve_weights(tmp_db)
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 第二次不重复
        assert tmp_db.get_meta(CLICK_CURSOR_KEY) == "1"

    # T-EV-03 标签演化
    def test_expand_feedback_tag_evolves(self, tmp_db):
        tmp_db.add_feedback_text(raw_text="多推点AI硬件", intent="expand", article_id=None,
                                 extracted_tags=["AI硬件"], keywords=[])
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI硬件") == pytest.approx(1.1)  # 新词默认 1.0 + 0.1
        assert tmp_db.get_meta(FEEDBACK_CURSOR_KEY) == "1"

    def test_non_expand_feedback_not_evolved(self, tmp_db):
        tmp_db.add_feedback_text(raw_text="今天天气不错", intent="none", article_id=None,
                                 extracted_tags=[], keywords=[])
        evolve_weights(tmp_db)
        assert tmp_db.get_interest_weights() == {}

    # T-EV-04 权重 clamp
    def test_evolve_clamped_at_2(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        for i in range(30):
            self._click(tmp_db, aid, remote_id=i + 1)
            evolve_weights(tmp_db)
            assert weight(tmp_db, "AI") <= 2.0
        assert weight(tmp_db, "AI") == 2.0

    # 补充：sync 游标协作（docs/05 §4.1 修订）
    async def test_sync_clicks_advances_evolve_cursor(self, tmp_db):
        from daily_picks.models import ClickEvent
        from daily_picks.tracking import sync_clicks

        class FakeTrackingClient:
            async def fetch_clicks(self, after: int):
                return ([], False) if after else (
                    [ClickEvent(remote_id=1, article_id=aid, click_date="2026-08-27", count=1)],
                    False)

        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        await sync_clicks(tmp_db, FakeTrackingClient(), 0.05)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # sync 回写
        evolve_weights(tmp_db)  # 演化应跳过已回写行
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 无双重回写
        assert int(tmp_db.get_meta(CLICK_CURSOR_KEY)) >= 1
