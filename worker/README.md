# DailyPicks 点击追踪 Worker（Cloudflare Workers + D1）

对应设计文档 `docs/01-设计文档.md` §15。前置条件：Node.js ≥ 18、Cloudflare 账号。

## 1. 安装与本地测试

```bash
cd worker
npm install -g wrangler        # 或每次用 npx wrangler@latest
node --test                   # 11+4 个用例全绿（无网络、无 D1）
```

## 2. 创建 D1 数据库并建表

```bash
npx wrangler d1 create daily-picks-track
# 输出 database_id，复制到 wrangler.toml（从 wrangler.example.toml 复制修改）
npx wrangler d1 execute daily-picks-track --remote --file=schema.sql
```

## 3. 设置 API_TOKEN 并部署

```bash
npx wrangler secret put API_TOKEN   # 输入随机长令牌（与本地 .env 的 TRACKING_API_TOKEN 同值）
npx wrangler deploy
# 记录输出的 workers.dev 域名：https://daily-picks-track.<你的子域>.workers.dev
```

## 4. 验证

```bash
# 未鉴权应 401
curl -i https://daily-picks-track.<你的子域>.workers.dev/api/clicks?after=0
# 注册一条测试链接
curl -i -X POST https://daily-picks-track.<你的子域>.workers.dev/api/links \
  -H "Authorization: Bearer <API_TOKEN>" -H "Content-Type: application/json" \
  -d '{"code":"abcd1234","url":"https://example.com","article_id":1}'
# 点击应 302 到 example.com
curl -i https://daily-picks-track.<你的子域>.workers.dev/c/abcd1234
# 再次拉取应看到 1 条点击
curl -s "https://daily-picks-track.<你的子域>.workers.dev/api/clicks?after=0" \
  -H "Authorization: Bearer <API_TOKEN>"
```

## 5. 契约与维护

- API 契约与 D1 schema 见设计文档 §15.3/§15.4；修改契约必须同步修改
  `daily_picks/tracking.py` 与设计文档。
- 免费额度（D1 写 10 万行/天）个人使用远够；超限时 Worker 报错、本地 fail-open。
