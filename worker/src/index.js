// 点击追踪 Worker 入口（设计文档 §15.3）：
//   GET  /c/{code}        → 302 重定向原始 URL 并记录点击（公开，无鉴权）
//   POST /api/links       → 注册 {code, url, article_id}（Bearer API_TOKEN）
//   GET  /api/clicks?after=N → 返回 id>N 的点击事件（Bearer API_TOKEN）
import { Store } from "./store.js";

const CODE_RE = /^[A-Za-z0-9]{8}$/;
export const MAX_CLICKS_PER_PAGE = 1000;

export function isAuthorized(request, apiToken) {
  const header = request.headers.get("Authorization") || "";
  return Boolean(apiToken) && header === `Bearer ${apiToken}`;
}

export function utcDate(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

export async function handleRequest(request, env, store = new Store(env.DB)) {
  const url = new URL(request.url);
  const { pathname } = url;

  const clickMatch = pathname.match(/^\/c\/([A-Za-z0-9]{8})$/);
  if (clickMatch && request.method === "GET") {
    const link = await store.getLink(clickMatch[1]);
    if (!link) {
      return new Response("link not found", { status: 404 });
    }
    // 记录失败不阻塞 302（契约 §15.3 遥测降级：302 + 记录失败，优于主功能降级）
    try {
      await store.recordClick(link.article_id, utcDate());
    } catch (err) {
      console.warn(`recordClick failed: ${err.message}`);
    }
    return new Response("", { status: 302, headers: { Location: link.url } });
  }

  if (pathname === "/api/links" && request.method === "POST") {
    if (!isAuthorized(request, env.API_TOKEN)) {
      return new Response('{"error":"unauthorized"}', {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }
    let body;
    try {
      body = await request.json();
    } catch {
      return new Response('{"error":"invalid json"}', { status: 400 });
    }
    const { code, url: target, article_id: articleId } = body ?? {};
    if (
      typeof code !== "string" || !CODE_RE.test(code) ||
      typeof target !== "string" || !/^https?:\/\//.test(target) ||
      !Number.isInteger(articleId) || articleId <= 0
    ) {
      return new Response('{"error":"invalid fields"}', { status: 400 });
    }
    await store.upsertLink({ code, url: target, articleId });
    return new Response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }

  if (pathname === "/api/clicks" && request.method === "GET") {
    if (!isAuthorized(request, env.API_TOKEN)) {
      return new Response('{"error":"unauthorized"}', {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }
    const after = Number.parseInt(url.searchParams.get("after") ?? "0", 10) || 0;
    const rows = await store.listClicks(after, MAX_CLICKS_PER_PAGE);
    const clicks = rows.map((r) => ({
      id: r.id,
      article_id: r.article_id,
      click_date: r.click_date,
      count: r.count,
    }));
    return new Response(
      JSON.stringify({ clicks, has_more: rows.length === MAX_CLICKS_PER_PAGE }),
      { status: 200, headers: { "content-type": "application/json" } }
    );
  }

  return new Response("not found", { status: 404 });
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
