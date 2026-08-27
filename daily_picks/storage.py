"""SQLite 存储：schema 初始化与全部读写（设计文档 §5 / 开发文档 §4.3）。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from daily_picks.models import Article, Pick


class StorageError(Exception):
    """存储层错误（读写失败统一抛出）。"""


# 设计文档 §5 全部 DDL（幂等）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,                -- 'rss'|'bilibili'|'zhihu'|'juejin'|'hnews'|'infoq'
    source_key   TEXT    NOT NULL,                -- 源内唯一 ID（aid/question_id/article_id/guid）
    title        TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    author       TEXT,
    summary      TEXT,                            -- 一句话摘要（截断 ≤200 字）
    content_hash TEXT    NOT NULL UNIQUE,         -- sha256(title + url)，跨源去重
    published_at DATETIME,                        -- 源发布时间（本地时区）
    fetched_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    score        REAL    NOT NULL DEFAULT 0,      -- 最近一次规则分
    state        TEXT    NOT NULL DEFAULT 'new',  -- new | dismissed
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);

CREATE TABLE IF NOT EXISTS digest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL UNIQUE,         -- 'YYYY-MM-DD'，幂等键
    candidate_count INTEGER NOT NULL DEFAULT 0,
    picked_count    INTEGER NOT NULL DEFAULT 0,
    pushed          INTEGER NOT NULL DEFAULT 0,   -- 0/1
    channel         TEXT,                         -- 'wecom'|'serverchan'|'dry-run'
    llm_tokens_in   INTEGER NOT NULL DEFAULT 0,
    llm_tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0,
    fallback_used   INTEGER NOT NULL DEFAULT 0,   -- 是否因 LLM 异常降级
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS digest_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_id   INTEGER NOT NULL REFERENCES digest_runs(id),
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    rank        INTEGER NOT NULL,
    llm_reason  TEXT,
    UNIQUE(digest_id, article_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    kind        TEXT NOT NULL CHECK (kind IN ('like','dislike')),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interest_weights (
    keyword    TEXT PRIMARY KEY,
    weight     REAL NOT NULL DEFAULT 1.0,         -- 范围 [0.2, 2.0]
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Storage:
    """SQLite 全部读写。线程安全：单连接 + threading.RLock（check_same_thread=False）。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.parent.is_dir():
            raise StorageError(f"数据库目录不存在: {self.db_path.parent}")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ---- 内部工具 ----

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行并提交；失败回滚并抛 StorageError。调用方必须持有 _lock。"""
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur
        except sqlite3.Error as e:
            self._conn.rollback()
            raise StorageError(f"数据库操作失败: {e}") from e

    # ---- 公共 API ----

    def init_schema(self) -> None:
        """执行 §5 全部 DDL（幂等）。"""
        with self._lock:
            try:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            except sqlite3.Error as e:
                raise StorageError(f"初始化 schema 失败: {e}") from e

    def upsert_articles(self, articles: list[Article]) -> list[int]:
        """INSERT OR IGNORE 按 (source, source_key) 与 content_hash 去重；返回【新插入】的 article id 列表。"""
        new_ids: list[int] = []
        with self._lock:
            try:
                for a in articles:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO articles"
                        " (source, source_key, title, url, author, summary, content_hash, published_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (a.source, a.source_key, a.title, a.url, a.author,
                         a.summary, a.content_hash, a.published_at),
                    )
                    if cur.rowcount == 1:  # rowcount==1 为新增（开发文档 §4.3）
                        new_ids.append(cur.lastrowid)
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                raise StorageError(f"写入 articles 失败: {e}") from e
        return new_ids

    def get_articles_by_ids(self, ids: list[int]) -> list[dict]:
        """按 id 读取文章，dict 含全部列（按 id 升序）。"""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT * FROM articles WHERE id IN ({marks}) ORDER BY id", tuple(ids)
                ).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 articles 失败: {e}") from e
        return [dict(r) for r in rows]

    def update_score(self, article_id: int, score: float) -> None:
        """更新最近一次规则分。"""
        with self._lock:
            self._execute("UPDATE articles SET score=? WHERE id=?", (score, article_id))

    def set_state(self, article_id: int, state: str) -> None:
        """state 仅允许 {'new','dismissed'}，否则抛 StorageError。"""
        if state not in {"new", "dismissed"}:
            raise StorageError(f"非法 state: {state!r}")
        with self._lock:
            self._execute("UPDATE articles SET state=? WHERE id=?", (state, article_id))

    def start_digest_run(self, run_date: str, candidate_count: int) -> int:
        """当日已有 run → 返回其 id（幂等）；否则插入并返回新 id。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT id FROM digest_runs WHERE run_date=?", (run_date,)
                ).fetchone()
                if row is not None:
                    return row["id"]
                cur = self._conn.execute(
                    "INSERT INTO digest_runs (run_date, candidate_count) VALUES (?, ?)",
                    (run_date, candidate_count),
                )
                self._conn.commit()
                return cur.lastrowid
            except sqlite3.Error as e:
                self._conn.rollback()
                raise StorageError(f"创建 digest_run 失败: {e}") from e

    def get_digest_run(self, run_id: int) -> dict | None:
        """读单条 digest_run（全部列）；不存在返回 None。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM digest_runs WHERE id=?", (run_id,)
                ).fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"读取 digest_run 失败: {e}") from e
        return dict(row) if row is not None else None

    def finish_digest_run(self, run_id: int, *, picked_count: int, pushed: int,
                          channel: str | None, tokens_in: int, tokens_out: int,
                          cost_usd: float, fallback_used: bool) -> None:
        """记账：精选数/推送/渠道/token/成本/降级标记。"""
        with self._lock:
            self._execute(
                "UPDATE digest_runs SET picked_count=?, pushed=?, channel=?,"
                " llm_tokens_in=?, llm_tokens_out=?, cost_usd=?, fallback_used=? WHERE id=?",
                (picked_count, pushed, channel, tokens_in, tokens_out,
                 cost_usd, int(fallback_used), run_id),
            )

    def add_digest_items(self, digest_id: int, picks: list[Pick]) -> None:
        """写入精选条目（UNIQUE(digest_id, article_id)，重复忽略）。"""
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO digest_items (digest_id, article_id, rank, llm_reason)"
                    " VALUES (?, ?, ?, ?)",
                    [(digest_id, p.article_id, p.rank, p.reason) for p in picks],
                )
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                raise StorageError(f"写入 digest_items 失败: {e}") from e

    def add_feedback(self, article_id: int, kind: str) -> None:
        """同文章先删旧反馈再插入（只保留最后一次反馈）。"""
        if kind not in {"like", "dislike"}:
            raise StorageError(f"非法 feedback kind: {kind!r}")
        with self._lock:
            try:
                self._conn.execute("DELETE FROM feedback WHERE article_id=?", (article_id,))
                self._conn.execute(
                    "INSERT INTO feedback (article_id, kind) VALUES (?, ?)", (article_id, kind)
                )
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                raise StorageError(f"写入 feedback 失败: {e}") from e

    def get_feedback_kinds(self, article_id: int) -> list[str]:
        """该文章的历史反馈种类（先删后插，通常最多 1 条）。"""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT kind FROM feedback WHERE article_id=?", (article_id,)
                ).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 feedback 失败: {e}") from e
        return [r["kind"] for r in rows]

    def get_interest_weights(self) -> dict[str, float]:
        """读 interest_weights 全表。"""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT keyword, weight FROM interest_weights"
                ).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 interest_weights 失败: {e}") from e
        return {r["keyword"]: r["weight"] for r in rows}

    def bump_keyword_weight(self, keyword: str, delta: float) -> None:
        """调整关键词权重，钳制 [0.2, 2.0]，upsert。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT weight FROM interest_weights WHERE keyword=?", (keyword,)
                ).fetchone()
                current = row["weight"] if row is not None else 1.0
                new_weight = max(0.2, min(2.0, current + delta))
                self._conn.execute(
                    "INSERT INTO interest_weights (keyword, weight, updated_at)"
                    " VALUES (?, ?, CURRENT_TIMESTAMP)"
                    " ON CONFLICT(keyword) DO UPDATE SET weight=excluded.weight,"
                    " updated_at=CURRENT_TIMESTAMP",
                    (keyword, new_weight),
                )
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                raise StorageError(f"更新 interest_weights 失败: {e}") from e

    def get_stats(self, days: int) -> dict:
        """返回 {'runs': n, 'pushed': n, 'tokens_in': n, 'tokens_out': n, 'cost_usd': x}（近 days 天）。"""
        if days < 1:
            raise StorageError(f"days 必须 >= 1，实际为 {days}")
        cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS runs,"
                    " COALESCE(SUM(pushed),0) AS pushed,"
                    " COALESCE(SUM(llm_tokens_in),0) AS tokens_in,"
                    " COALESCE(SUM(llm_tokens_out),0) AS tokens_out,"
                    " COALESCE(SUM(cost_usd),0) AS cost_usd"
                    " FROM digest_runs WHERE run_date >= ?",
                    (cutoff,),
                ).fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"统计 digest_runs 失败: {e}") from e
        return {
            "runs": row["runs"],
            "pushed": row["pushed"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "cost_usd": row["cost_usd"],
        }
