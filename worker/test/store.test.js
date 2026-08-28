import test from "node:test";
import assert from "node:assert/strict";
import { Store } from "../src/store.js";

// 内存版 D1 prepare/bind 链（.run/.first/.all），仅覆盖本 Store 用到的 SQL 形态
function makeFakeDb() {
  const links = new Map(); // code -> {url, article_id}
  const clicks = [];       // 行数组
  let nextId = 1;
  return {
    prepare(sql) {
      return {
        bind(...params) {
          return {
            async run() {
              if (sql.startsWith("INSERT INTO links")) {
                const [code, url, articleId] = params;
                if (sql.includes("ON CONFLICT")) {
                  links.set(code, { url, article_id: articleId });
                }
              } else if (sql.startsWith("INSERT INTO clicks")) {
                const [articleId, clickDate] = params;
                const existing = clicks.find(
                  (c) => c.article_id === articleId && c.click_date === clickDate
                );
                if (sql.includes("ON CONFLICT") && existing) {
                  existing.count += 1;
                } else {
                  clicks.push({ id: nextId++, article_id: articleId,
                                click_date: clickDate, count: 1 });
                }
              }
            },
            async first() {
              const [code] = params;
              const l = links.get(code);
              return l ? { code, url: l.url, article_id: l.article_id } : null;
            },
            async all() {
              const [after, limit] = params;
              return { results: clicks.filter((c) => c.id > after).slice(0, limit) };
            },
          };
        },
      };
    },
  };
}

test("upsertLink 新增与同 code 覆盖", async () => {
  const store = new Store(makeFakeDb());
  await store.upsertLink({ code: "abcd1234", url: "https://example.com/a", articleId: 1 });
  await store.upsertLink({ code: "abcd1234", url: "https://example.com/b", articleId: 2 });
  const row = await store.getLink("abcd1234");
  assert.equal(row.url, "https://example.com/b"); // ON CONFLICT 覆盖
  assert.equal(row.article_id, 2);
});

test("getLink 未知 code 返回 null", async () => {
  const store = new Store(makeFakeDb());
  assert.equal(await store.getLink("nope1234"), null);
});

test("recordClick 同文章同日 count 累加、不新增行", async () => {
  const store = new Store(makeFakeDb());
  await store.recordClick(42, "2026-08-28");
  await store.recordClick(42, "2026-08-28");
  await store.recordClick(42, "2026-08-29");
  const rows = await store.listClicks(0, 100);
  assert.equal(rows.length, 2);
  assert.equal(rows.find((r) => r.click_date === "2026-08-28").count, 2);
});

test("listClicks 按 id 升序、after 过滤、limit 截断", async () => {
  const store = new Store(makeFakeDb());
  await store.recordClick(1, "2026-08-28");
  await store.recordClick(2, "2026-08-28");
  await store.recordClick(3, "2026-08-28");
  const rows = await store.listClicks(1, 10);
  assert.deepEqual(rows.map((r) => r.id), [2, 3]);
});
