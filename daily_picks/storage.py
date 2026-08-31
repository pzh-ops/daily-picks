"""SQLite 存储：schema 初始化与全部读写（设计文档 §5 / 开发文档 §4.3）。"""

from __future__ import annotations

import json
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

CREATE TABLE IF NOT EXISTS clicks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    click_date  TEXT NOT NULL,               -- worker 端 click_date（UTC 'YYYY-MM-DD'）
    remote_id   INTEGER NOT NULL UNIQUE,     -- worker 端 clicks.id，幂等键
    count       INTEGER NOT NULL DEFAULT 1,  -- 该文章当日点击次数（worker 端聚合）
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- v3（docs/04 §4）
CREATE TABLE IF NOT EXISTS user_profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),   -- 强制单行
    tags        TEXT NOT NULL DEFAULT '[]',           -- JSON 数组，如 ["AI大模型","创业"]
    sources     TEXT NOT NULL DEFAULT '[]',           -- JSON 数组，源 key 列表（含自定义）
    top_n       INTEGER NOT NULL DEFAULT 5 CHECK (top_n BETWEEN 1 AND 10),
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tag_weights (
    tag        TEXT PRIMARY KEY,
    weight     REAL NOT NULL DEFAULT 1.0,             -- 范围 [0.2, 2.0]
    source     TEXT NOT NULL DEFAULT 'manual',        -- 'manual'|'click'|'feedback'
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback_text (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text        TEXT NOT NULL,                    -- 用户原始反馈文字
    intent          TEXT NOT NULL CHECK (intent IN ('like','dislike','expand','adjust','none')),
    article_id      INTEGER,                          -- 可选：针对某条
    extracted_tags  TEXT NOT NULL DEFAULT '[]',       -- JSON 数组：反馈中提取的新标签
    keywords        TEXT NOT NULL DEFAULT '[]',       -- JSON 数组：提取的关键词（进 interest_weights）
    channel         TEXT NOT NULL DEFAULT 'hermes',   -- 'hermes'|'wecom'
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_registry (
    key        TEXT PRIMARY KEY,                      -- 源 key（rss 自定义源的唯一标识）
    name       TEXT NOT NULL,                         -- 显示名
    kind       TEXT NOT NULL DEFAULT 'rss',           -- 'rss'|'builtin'
    url        TEXT,                                  -- rss url（kind='rss' 必填）
    tags       TEXT NOT NULL DEFAULT '[]',            -- JSON 数组：关联标签
    enabled    INTEGER NOT NULL DEFAULT 1,
    added_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    # ---- v3 用户画像（docs/04 §4 / docs/05 §1.2）----

    def save_profile(self, tags: list[str], sources: list[str], top_n: int) -> None:
        """INSERT OR REPLACE user_profile（id=1 单行）。top_n 越界（1-10）抛 StorageError。"""
        if not 1 <= top_n <= 10:
            raise StorageError(f"top_n 越界: {top_n}（要求 1-10）")
        with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO user_profile (id, tags, sources, top_n, updated_at)"
                " VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)",
                (json.dumps(tags, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), top_n),
            )

    def load_profile(self) -> dict | None:
        """读 user_profile id=1；无行返回 None。dict 含 tags(list)/sources(list)/top_n(int)。"""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT tags, sources, top_n FROM user_profile WHERE id=1").fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"读取 user_profile 失败: {e}") from e
        if row is None:
            return None
        return {
            "tags": json.loads(row["tags"] or "[]"),
            "sources": json.loads(row["sources"] or "[]"),
            "top_n": int(row["top_n"]),
        }

    def save_tag_weight(self, tag: str, weight: float, source: str = "manual") -> None:
        """UPSERT tag_weights；weight clamp [0.2, 2.0]（docs/04 §4）。"""
        clamped = max(0.2, min(2.0, weight))
        with self._lock:
            self._execute(
                "INSERT INTO tag_weights (tag, weight, source, updated_at)"
                " VALUES (?, ?, ?, CURRENT_TIMESTAMP)"
                " ON CONFLICT(tag) DO UPDATE SET weight=excluded.weight,"
                " source=excluded.source, updated_at=CURRENT_TIMESTAMP",
                (tag, clamped, source),
            )

    def list_tags(self) -> list[tuple[str, float]]:
        """SELECT tag, weight FROM tag_weights ORDER BY weight DESC。"""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT tag, weight FROM tag_weights ORDER BY weight DESC").fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 tag_weights 失败: {e}") from e
        return [(r["tag"], float(r["weight"])) for r in rows]

    def register_source(self, key: str, name: str, url: str, tags: list[str]) -> None:
        """INSERT OR REPLACE source_registry（kind='rss'，docs/05 §1.2）。"""
        with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO source_registry (key, name, kind, url, tags, enabled, added_at)"
                " VALUES (?, ?, 'rss', ?, ?, 1, CURRENT_TIMESTAMP)",
                (key, name, url, json.dumps(tags, ensure_ascii=False)),
            )

    def list_sources(self, enabled_only: bool = True) -> list[dict]:
        """SELECT key, name, kind, url, tags, enabled FROM source_registry；tags 解析为 list。"""
        sql = "SELECT key, name, kind, url, tags, enabled FROM source_registry"
        if enabled_only:
            sql += " WHERE enabled=1"
        with self._lock:
            try:
                rows = self._conn.execute(sql).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 source_registry 失败: {e}") from e
        result = []
        for r in rows:
            item = dict(r)
            item["tags"] = json.loads(item["tags"] or "[]")
            result.append(item)
        return result

    def add_feedback_text(self, *, raw_text: str, intent: str, article_id: int | None,
                          extracted_tags: list[str], keywords: list[str],
                          channel: str = "hermes") -> int:
        """插入 feedback_text（docs/04 §4）；intent 非法抛 StorageError。返回新行 id。"""
        if intent not in ("like", "dislike", "expand", "adjust", "none"):
            raise StorageError(f"非法 intent: {intent!r}")
        with self._lock:
            cur = self._execute(
                "INSERT INTO feedback_text (raw_text, intent, article_id, extracted_tags, keywords, channel)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (raw_text, intent, article_id,
                 json.dumps(extracted_tags, ensure_ascii=False),
                 json.dumps(keywords, ensure_ascii=False), channel),
            )
            return int(cur.lastrowid)

    # ---- 点击追踪（设计文档 §15.5）----

    CLICK_CURSOR_KEY = "last_click_sync_id"

    def get_meta(self, key: str) -> str | None:
        """读 meta 表；不存在返回 None。"""
        with self._lock:
            try:
                row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"读取 meta 失败: {e}") from e
        return row["value"] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        """upsert meta（存在则覆盖）。"""
        with self._lock:
            self._execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_click_cursor(self) -> int:
        """点击同步游标（meta 表 CLICK_CURSOR_KEY）；无记录 → 0。"""
        value = self.get_meta(self.CLICK_CURSOR_KEY)
        return int(value) if value is not None else 0

    def set_click_cursor(self, remote_id: int) -> None:
        """推进点击同步游标到 remote_id（调用方保证单调前进）。"""
        self.set_meta(self.CLICK_CURSOR_KEY, str(remote_id))

    def record_click(self, *, article_id: int, click_date: str,
                     remote_id: int, count: int) -> bool:
        """记录一条已同步的点击事件（remote_id 幂等）；返回是否为新事件。"""
        with self._lock:
            cur = self._execute(
                "INSERT OR IGNORE INTO clicks (article_id, click_date, remote_id, count)"
                " VALUES (?, ?, ?, ?)",
                (article_id, click_date, remote_id, count),
            )
            return cur.rowcount == 1

    def count_clicks(self) -> int:
        """clicks 表总行数（统计/验收用）。"""
        with self._lock:
            try:
                row = self._conn.execute("SELECT COUNT(*) FROM clicks").fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"统计 clicks 失败: {e}") from e
        return int(row[0])

    # ---- v3 权重演化查询（docs/05 §4.1）----

    def get_clicks_since(self, click_id: int) -> list[dict]:
        """clicks.id > click_id 的行（LEFT JOIN articles 取 title/summary；文章已删则为 None）。"""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT c.id, a.title, a.summary FROM clicks c"
                    " LEFT JOIN articles a ON a.id = c.article_id"
                    " WHERE c.id > ? ORDER BY c.id", (click_id,),
                ).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 clicks 增量失败: {e}") from e
        return [dict(r) for r in rows]

    def get_feedback_text_since(self, feedback_id: int) -> list[dict]:
        """feedback_text.id > feedback_id 的行；extracted_tags 解析为 list。"""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT id, intent, extracted_tags FROM feedback_text"
                    " WHERE id > ? ORDER BY id", (feedback_id,),
                ).fetchall()
            except sqlite3.Error as e:
                raise StorageError(f"读取 feedback_text 增量失败: {e}") from e
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["extracted_tags"] = json.loads(item["extracted_tags"] or "[]")
            except json.JSONDecodeError:
                item["extracted_tags"] = []
            result.append(item)
        return result

    def get_max_click_id(self) -> int:
        """clicks 表最大 id（无行返回 0）；sync 与 evolve 游标协作用（docs/05 §4.1 修订）。"""
        with self._lock:
            try:
                row = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM clicks").fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"统计 clicks 失败: {e}") from e
        return int(row[0])
