"""T-DB 存储模块用例（测试文档 §4.2）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from daily_picks.models import Article, Pick
from daily_picks.storage import Storage, StorageError


def make_article(source: str = "rss", source_key: str = "k1",
                 title: str = "测试标题", url: str = "https://example.com/a", **kw) -> Article:
    """构造测试 Article，默认同 title+url（便于跨源去重用例复用）。"""
    return Article(source=source, source_key=source_key, title=title, url=url, **kw)


def count_rows(storage, table: str = "articles") -> int:
    """直接查行数（测试辅助）。"""
    return storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestStorage:
    def test_init_schema_idempotent(self, tmp_db):  # T-DB-01
        tmp_db.init_schema()
        tmp_db.init_schema()  # 连续两次，无异常即通过

    def test_upsert_dedup_same_source_key(self, tmp_db):  # T-DB-02
        a = make_article()
        assert tmp_db.upsert_articles([a]) == [1]
        assert tmp_db.upsert_articles([a]) == []
        assert count_rows(tmp_db) == 1

    def test_upsert_dedup_cross_source_by_content_hash(self, tmp_db):  # T-DB-03
        a1 = make_article(source="rss")
        a2 = make_article(source="bilibili")  # title+url 相同 → content_hash 冲突
        assert len(tmp_db.upsert_articles([a1, a2])) == 1
        assert count_rows(tmp_db) == 1

    def test_upsert_returns_new_ids_only(self, tmp_db):  # T-DB-04
        a1 = make_article(source_key="1")
        a2 = make_article(source_key="2", title="第二篇", url="https://example.com/b")
        a3 = make_article(source_key="1")  # 与 a1 重复
        ids = tmp_db.upsert_articles([a1, a2, a3])
        assert sorted(ids) == [1, 2]
        assert count_rows(tmp_db) == 2

    def test_start_digest_run_idempotent(self, tmp_db):  # T-DB-05
        id1 = tmp_db.start_digest_run("2026-08-27", candidate_count=5)
        id2 = tmp_db.start_digest_run("2026-08-27", candidate_count=9)
        assert id1 == id2
        assert count_rows(tmp_db, "digest_runs") == 1

    def test_finish_digest_run_records(self, tmp_db):  # T-DB-06
        rid = tmp_db.start_digest_run("2026-08-27", candidate_count=10)
        tmp_db.finish_digest_run(
            rid, picked_count=8, pushed=1, channel="dry-run",
            tokens_in=1000, tokens_out=80, cost_usd=0.0008184, fallback_used=True,
        )
        row = tmp_db._conn.execute("SELECT * FROM digest_runs WHERE id=?", (rid,)).fetchone()
        assert row["picked_count"] == 8
        assert row["pushed"] == 1
        assert row["channel"] == "dry-run"
        assert row["llm_tokens_in"] == 1000
        assert row["llm_tokens_out"] == 80
        assert row["cost_usd"] == pytest.approx(0.0008184)
        assert row["fallback_used"] == 1

    def test_get_digest_run(self, tmp_db):  # M3 补充：幂等跳过推送需读取 pushed/channel 状态
        rid = tmp_db.start_digest_run("2026-08-27", candidate_count=3)
        assert tmp_db.get_digest_run(rid)["pushed"] == 0
        assert tmp_db.get_digest_run(rid)["channel"] is None
        tmp_db.finish_digest_run(
            rid, picked_count=3, pushed=1, channel="wecom",
            tokens_in=0, tokens_out=0, cost_usd=0.0, fallback_used=False,
        )
        row = tmp_db.get_digest_run(rid)
        assert row["pushed"] == 1
        assert row["channel"] == "wecom"
        assert tmp_db.get_digest_run(9999) is None  # 不存在 → None

    def test_add_digest_items_unique(self, tmp_db):  # T-DB-07
        rid = tmp_db.start_digest_run("2026-08-27", candidate_count=3)
        picks = [Pick(article_id=i, rank=i, reason=f"理由{i}") for i in (1, 2, 3)]
        tmp_db.add_digest_items(rid, picks)
        assert count_rows(tmp_db, "digest_items") == 3
        tmp_db.add_digest_items(rid, [Pick(article_id=1, rank=1, reason="重复")])
        assert count_rows(tmp_db, "digest_items") == 3  # UNIQUE(digest_id, article_id) 生效

    def test_feedback_dedup_keep_last(self, tmp_db):  # T-DB-08
        tmp_db.add_feedback(1, "like")
        tmp_db.add_feedback(1, "like")  # 同文章重复反馈：先删后插
        assert count_rows(tmp_db, "feedback") == 1
        assert tmp_db.get_feedback_kinds(1) == ["like"]
        tmp_db.add_feedback(1, "dislike")
        assert tmp_db.get_feedback_kinds(1) == ["dislike"]
        assert count_rows(tmp_db, "feedback") == 1

    def test_bump_keyword_weight_clamped(self, tmp_db):  # T-DB-09
        tmp_db.bump_keyword_weight("AI", +5)   # 1.0+5=6 → 钳制到 2.0
        assert tmp_db.get_interest_weights()["AI"] == 2.0
        tmp_db.bump_keyword_weight("AI", -10)  # 2.0-10 → 钳制到 0.2
        assert tmp_db.get_interest_weights()["AI"] == 0.2

    def test_get_stats_aggregates(self, tmp_db):  # T-DB-10
        d1 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        d2 = datetime.now().strftime("%Y-%m-%d")
        d_old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        r1 = tmp_db.start_digest_run(d1, candidate_count=0)
        r2 = tmp_db.start_digest_run(d2, candidate_count=0)
        r3 = tmp_db.start_digest_run(d_old, candidate_count=0)
        tmp_db.finish_digest_run(r1, picked_count=5, pushed=1, channel="wecom",
                                 tokens_in=1000, tokens_out=100, cost_usd=0.001, fallback_used=False)
        tmp_db.finish_digest_run(r2, picked_count=3, pushed=0, channel="dry-run",
                                 tokens_in=500, tokens_out=50, cost_usd=0.0005, fallback_used=True)
        tmp_db.finish_digest_run(r3, picked_count=9, pushed=1, channel="wecom",
                                 tokens_in=999, tokens_out=99, cost_usd=0.9, fallback_used=False)
        stats = tmp_db.get_stats(7)
        assert stats["runs"] == 2  # 30 天前的 run 不在窗口内
        assert stats["pushed"] == 1
        assert stats["tokens_in"] == 1500
        assert stats["tokens_out"] == 150
        assert stats["cost_usd"] == pytest.approx(0.0015)

    def test_bad_path_raises_storage_error(self, tmp_path):  # T-DB-11
        missing = tmp_path / "no_such_dir" / "x.db"
        with pytest.raises(StorageError):
            Storage(missing).init_schema()


class TestStorageErrorPaths:
    """补充用例：StorageError 坏路径分支（保证 storage.py 覆盖率 ≥85%）。"""

    def test_get_articles_empty_ids(self, tmp_db):
        assert tmp_db.get_articles_by_ids([]) == []

    def test_update_score_and_set_state(self, tmp_db):
        tmp_db.upsert_articles([make_article()])
        tmp_db.update_score(1, 2.5)
        tmp_db.set_state(1, "dismissed")
        row = tmp_db._conn.execute("SELECT score, state FROM articles WHERE id=1").fetchone()
        assert row["score"] == 2.5
        assert row["state"] == "dismissed"
        with pytest.raises(StorageError):
            tmp_db.set_state(1, "bogus")  # state 仅允许 new/dismissed

    def test_invalid_feedback_kind(self, tmp_db):
        with pytest.raises(StorageError):
            tmp_db.add_feedback(1, "meh")

    def test_get_stats_invalid_days(self, tmp_db):
        with pytest.raises(StorageError):
            tmp_db.get_stats(0)

    def test_init_schema_on_closed_connection(self, tmp_db):
        tmp_db._conn.close()
        with pytest.raises(StorageError):
            tmp_db.init_schema()

    def test_write_on_missing_articles_table(self, tmp_db):
        tmp_db._conn.execute("DROP TABLE articles")
        tmp_db._conn.commit()
        with pytest.raises(StorageError):
            tmp_db.upsert_articles([make_article()])
        with pytest.raises(StorageError):
            tmp_db.get_articles_by_ids([1])
        with pytest.raises(StorageError):
            tmp_db.update_score(1, 1.0)

    def test_digest_runs_on_missing_table(self, tmp_db):
        tmp_db._conn.execute("DROP TABLE digest_runs")
        tmp_db._conn.commit()
        with pytest.raises(StorageError):
            tmp_db.start_digest_run("2026-08-27", 0)
        with pytest.raises(StorageError):
            tmp_db.finish_digest_run(1, picked_count=0, pushed=0, channel=None,
                                     tokens_in=0, tokens_out=0, cost_usd=0.0, fallback_used=False)
        with pytest.raises(StorageError):
            tmp_db.get_stats(7)

    def test_digest_items_on_missing_table(self, tmp_db):
        tmp_db._conn.execute("DROP TABLE digest_items")
        tmp_db._conn.commit()
        with pytest.raises(StorageError):
            tmp_db.add_digest_items(1, [Pick(article_id=1, rank=1, reason="r")])

    def test_feedback_on_missing_table(self, tmp_db):
        tmp_db._conn.execute("DROP TABLE feedback")
        tmp_db._conn.commit()
        with pytest.raises(StorageError):
            tmp_db.add_feedback(1, "like")
        with pytest.raises(StorageError):
            tmp_db.get_feedback_kinds(1)

    def test_weights_on_missing_table(self, tmp_db):
        tmp_db._conn.execute("DROP TABLE interest_weights")
        tmp_db._conn.commit()
        with pytest.raises(StorageError):
            tmp_db.get_interest_weights()
        with pytest.raises(StorageError):
            tmp_db.bump_keyword_weight("AI", 0.1)
