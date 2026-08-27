"""T-DB 存储模块用例（测试文档 §4.2）。"""

from __future__ import annotations

from daily_picks.models import Article


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
