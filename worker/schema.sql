CREATE TABLE IF NOT EXISTS links (
  code       TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  article_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS clicks (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id       INTEGER NOT NULL,
  click_date       TEXT NOT NULL,
  count            INTEGER NOT NULL DEFAULT 1,
  first_clicked_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(article_id, click_date)
);
