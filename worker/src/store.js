// D1 访问层：links / clicks 全部读写（设计文档 §15.4）。
// D1 prepare().bind().run()/first()/all() 语义见 Cloudflare D1 API 文档。

export class Store {
  constructor(db) {
    this.db = db;
  }

  async upsertLink({ code, url, articleId }) {
    await this.db
      .prepare(
        "INSERT INTO links (code, url, article_id) VALUES (?, ?, ?) " +
          "ON CONFLICT(code) DO UPDATE SET url=excluded.url, article_id=excluded.article_id"
      )
      .bind(code, url, articleId)
      .run();
  }

  async getLink(code) {
    return this.db
      .prepare("SELECT code, url, article_id FROM links WHERE code = ?")
      .bind(code)
      .first();
  }

  async recordClick(articleId, clickDate) {
    // 同文章同日点击 count++（不新增行）→ 本地"每文章每天至多一次回写"（设计文档 §15.4）
    await this.db
      .prepare(
        "INSERT INTO clicks (article_id, click_date) VALUES (?, ?) " +
          "ON CONFLICT(article_id, click_date) DO UPDATE SET count = count + 1"
      )
      .bind(articleId, clickDate)
      .run();
  }

  async listClicks(after, limit) {
    const { results } = await this.db
      .prepare(
        "SELECT id, article_id, click_date, count FROM clicks WHERE id > ? ORDER BY id LIMIT ?"
      )
      .bind(after, limit)
      .all();
    return results;
  }
}
