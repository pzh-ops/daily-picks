"""M13 用例：权重演化（测试文档 docs/06 §5；LLM 无关，纯存储操作）。

注意：evolve_weights 在 Task 15 才实现，本文件头部暂不导入（Task 15 追加导入行）。
"""

from __future__ import annotations

import pytest

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
