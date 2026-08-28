import test from "node:test";
import assert from "node:assert/strict";
import { handleRequest, isAuthorized, utcDate, MAX_CLICKS_PER_PAGE } from "../src/index.js";

const CODE = "abcd1234";

class MemoryStore {
  constructor() {
    this.links = new Map();
    this.clicks = [];
    this.nextId = 1;
  }
  async upsertLink({ code, url, articleId }) {
    this.links.set(code, { url, articleId });
  }
  async getLink(code) {
    const l = this.links.get(code);
    return l ? { code, url: l.url, article_id: l.articleId } : null;
  }
  async recordClick(articleId, clickDate) {
    const existing = this.clicks.find(
      (c) => c.article_id === articleId && c.click_date === clickDate
    );
    if (existing) {
      existing.count += 1;
    } else {
      this.clicks.push({ id: this.nextId++, article_id: articleId,
                         click_date: clickDate, count: 1 });
    }
  }
  async listClicks(after, limit) {
    return this.clicks.filter((c) => c.id > after).slice(0, limit);
  }
}

function makeEnv(token = "test-token") {
  return { API_TOKEN: token, DB: null };
}

function makeRequest(path, { method = "GET", token, body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return new Request(`https://track.example.workers.dev${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
}

test("isAuthorized：token 匹配/不匹配/缺失", () => {
  assert.equal(isAuthorized(makeRequest("/api/clicks", { token: "t" }), "t"), true);
  assert.equal(isAuthorized(makeRequest("/api/clicks", { token: "x" }), "t"), false);
  assert.equal(isAuthorized(makeRequest("/api/clicks"), "t"), false);
  assert.equal(isAuthorized(makeRequest("/api/clicks", { token: "t" }), ""), false);
});

test("utcDate：UTC 日期串", () => {
  assert.equal(utcDate(new Date("2026-08-28T03:00:00Z")), "2026-08-28");
  assert.equal(utcDate(new Date("2026-08-28T23:30:00Z")), "2026-08-28");
});

test("GET /c/{code}：302 重定向并记录点击", async () => {
  const store = new MemoryStore();
  await store.upsertLink({ code: CODE, url: "https://example.com/post/1", articleId: 42 });
  const res = await handleRequest(makeRequest(`/c/${CODE}`), makeEnv(), store);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("Location"), "https://example.com/post/1");
  assert.deepEqual(store.clicks.map((c) => [c.article_id, c.count]), [[42, 1]]);
});

test("GET /c/{code}：未知 code 或非法格式 → 404 且不记录点击", async () => {
  const store = new MemoryStore();
  assert.equal((await handleRequest(makeRequest("/c/nope1234"), makeEnv(), store)).status, 404);
  assert.equal((await handleRequest(makeRequest("/c/ab-cd12"), makeEnv(), store)).status, 404);
  assert.equal(store.clicks.length, 0);
});

test("POST /api/links：未鉴权 → 401", async () => {
  const res = await handleRequest(
    makeRequest("/api/links", { method: "POST", body: { code: CODE, url: "https://x.com/a", article_id: 1 } }),
    makeEnv()
  );
  assert.equal(res.status, 401);
});

test("POST /api/links：合法 → 200 且入库", async () => {
  const store = new MemoryStore();
  const res = await handleRequest(
    makeRequest("/api/links", {
      method: "POST",
      token: "test-token",
      body: { code: CODE, url: "https://example.com/a", article_id: 1 },
    }),
    makeEnv(),
    store
  );
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });
  assert.ok(store.links.has(CODE));
});

test("POST /api/links：字段非法（非 http url / 负数 id / 短码格式错）→ 400", async () => {
  const store = new MemoryStore();
  const bad = [
    { code: CODE, url: "javascript:alert(1)", article_id: 1 },
    { code: CODE, url: "https://example.com/a", article_id: -1 },
    { code: "short", url: "https://example.com/a", article_id: 1 },
  ];
  for (const body of bad) {
    const res = await handleRequest(
      makeRequest("/api/links", { method: "POST", token: "test-token", body }),
      makeEnv(),
      store
    );
    assert.equal(res.status, 400);
  }
});

test("GET /api/clicks：鉴权 + 游标过滤 + 契约字段", async () => {
  const store = new MemoryStore();
  await store.upsertLink({ code: CODE, url: "https://example.com/a", articleId: 42 });
  await handleRequest(makeRequest(`/c/${CODE}`), makeEnv(), store);
  await handleRequest(makeRequest(`/c/${CODE}`), makeEnv(), store); // 同文章同日 count=2
  const res = await handleRequest(
    makeRequest("/api/clicks?after=0", { token: "test-token" }),
    makeEnv(),
    store
  );
  const data = await res.json();
  assert.equal(res.status, 200);
  assert.equal(data.has_more, false);
  assert.equal(data.clicks.length, 1);
  assert.equal(data.clicks[0].id, 1);
  assert.equal(data.clicks[0].article_id, 42);
  assert.equal(data.clicks[0].count, 2);
  assert.match(data.clicks[0].click_date, /^\d{4}-\d{2}-\d{2}$/); // 日期 = 测试运行日（UTC）
});

test("GET /api/clicks：未鉴权 → 401", async () => {
  const res = await handleRequest(makeRequest("/api/clicks"), makeEnv(), new MemoryStore());
  assert.equal(res.status, 401);
});

test("GET /api/clicks：满页 has_more=true", async () => {
  const store = new MemoryStore();
  for (let i = 0; i < MAX_CLICKS_PER_PAGE + 1; i += 1) {
    await store.recordClick(i, "2026-08-28");
  }
  const res = await handleRequest(
    makeRequest("/api/clicks?after=0", { token: "test-token" }),
    makeEnv(),
    store
  );
  const data = await res.json();
  assert.equal(data.clicks.length, MAX_CLICKS_PER_PAGE);
  assert.equal(data.has_more, true);
});

test("其他路径 → 404", async () => {
  const res = await handleRequest(makeRequest("/"), makeEnv(), new MemoryStore());
  assert.equal(res.status, 404);
});
