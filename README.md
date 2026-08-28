# DailyPicks（今日精选）

[![CI](https://github.com/pzh-ops/daily-picks/actions/workflows/ci.yml/badge.svg)](https://github.com/pzh-ops/daily-picks/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

每天自动从 **B站 / 知乎 / 掘金 / Hacker News / InfoQ / RSS** 聚合候选内容，用 LLM 按个人兴趣
筛选排序、去重，生成"每日精选"简报并推送到微信的个人 Agent。

> 最小可用的个人 Agent 范式：采集（Collect）→ 理解（Understand）→ 决策（Decide）→ 推送（Deliver），
> 外加一个可学习的偏好反馈闭环。

**当前进度**：M0 脚手架 ✅ · M1 采集层 ✅ · M2 排序与 LLM 精排 ✅ · M3 简报与微信推送 ✅ · M4 调度闭环 ✅ · M5 打磨开源 ✅

## 目录

- [功能特性](#功能特性)
- [真实精选效果](#真实精选效果)
- [简报格式](#简报格式)
- [架构](#架构)
- [工作原理](#工作原理)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [真实运行效果](#真实运行效果)
- [开发](#开发)
- [贡献](#贡献)
- [许可证](#许可证)

## 功能特性

| 需求 | 功能 | 状态 |
|---|---|---|
| R-001 | ≥6 类内容源（RSS/B站/知乎/掘金/HN/InfoQ），每源独立开关、失败隔离 | ✅ M1 |
| R-002 | 跨源去重（content_hash 唯一约束） | ✅ M0 |
| R-003 | 规则打分：关键词权重 + 来源权重 + 时效加成 | ✅ M2 |
| R-004 | LLM 精排（DeepSeek v4-pro，非法输出自动降级规则分） | ✅ M2 |
| R-005 | 微信推送：企业微信机器人 / Server酱 二选一 | ✅ M3 |
| R-006 | 每日 08:00 调度，当日幂等不重复推送 | ✅ M4 |
| R-007 | 偏好反馈：like/dislike 调整关键词权重 | ✅ M4 |
| R-008 | 统计报表：推送历史 / token / 成本 | ✅ M4 |
| R-009 | dry-run 预览（写 logs/last_digest.md，不推送） | ✅ M3 |
| R-010 | 自检命令：`test llm` / `test push` | ✅ M4 |
| R-011 | 日志落盘 + 轮转 | ✅ M0 |

## 真实精选效果

以下为 M2 实测、LLM 精排输出的真实结果：

```
精选 10 条
1. 【infoq】DeepSeek 开源 Harness：AI 智能体基础设施开始"拆分" —— DeepSeek开源AI智能体基础设施，切合开源与大模型
2. 【infoq】535B 大模型"直播"训练三个月：代码、数据、Loss全公开，吴恩达公开力挺 —— 535B大模型全公开训练
3. 【juejin】我全程用 AI开发了一款微信小游戏，上线了 —— AI独立开发小游戏全流程，实践价值高
4. 【juejin】Cursor 转 Codex 大半个月，聊聊我的真实感受 —— AI编程工具深度对比，助独立开发选型
5. 【zhihu】腾讯高管回应「腾讯做 AI 慢了」质疑 —— 大厂大模型战略解析，信息密度高
6. 【zhihu】小米发布国内首款 3nm 智驾芯片「玄戒 D100」 —— 3nm AI芯片发布，硬件加速AI落地
7. 【juejin】2026编程圈很火的10个Skills —— AI编程技能包集合，实用性强
8. 【rss】科技爱好者周刊（第 408 期）：你需要知道的 AI 缓存知识 —— AI缓存技术科普，拓展技术视野
9. 【hnews】Serve Markdown to AI Agents with Accept Headers —— 面向AI代理的Markdown服务
10. 【juejin】从零开始:前端转型AI agent直到就业第十八天-第五十六天 —— 前端转型AI agent经验
```

单次运行成本约 ¥0.1（DeepSeek v4-pro，40 候选精排 10 条）。

## 简报格式

以下为 M3 实测、dry-run 生成的 logs/last_digest.md 内容：

```
📌 今日精选 · 2026-08-27

1. 【掘金】GLM5.3Flash 我"忍"你很久，今天"曝光"你！
   摘要：这几天忍着不发，可憋死我了！ 今天终于可以"曝光"它了！Ox 模型到底是谁，想必大家已经知道了。 没错，主角就是 GLM…
   理由：GLM大模型新版本，与大模型兴趣高度相关。
   作者：甲维斯 ｜ 链接：https://juejin.cn/post/7678324732161851442

2. 【知乎】很多人有 「电子囤积癖」…（其余条目略）
```

推送时企业微信以 `msgtype=text` 原文发送（不支持链接渲染，URL 为纯文本）；Server酱自动把
`链接：URL` 行转换为 `[链接](URL)` markdown。同日重复运行自动跳过推送（幂等）。

## 架构

```
┌────────────────────────────  每日 08:00（APScheduler 或 cron）  ────────────────────────────┐
│                                                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐      │
│  │ RssAdapter│   │Bilibili  │   │ Zhihu    │   │ Juejin   │   │ HNews    │   │ InfoQ    │      │
│  │  (RSS/Atom)│  │Adapter   │   │ Adapter  │   │ Adapter  │   │ Adapter  │   │ Adapter  │      │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘      │
│       └──────────────┴──────┬──────┴───────────────┘                                        │
│                             ▼                                                                 │
│                    ┌─────────────────┐  去重+入库   ┌───────────────┐                        │
│                    │   Storage(SQLite)│◄───────────│  models.Article│                        │
│                    └────────┬────────┘             └───────────────┘                        │
│                             ▼                                                                 │
│                    ┌─────────────────┐   规则打分(关键词/来源/时效)                            │
│                    │   Ranker        │──► 选出 ≤40 候选 ──► LLMClient(DeepSeek)               │
│                    └────────┬────────┘   精排选 Top10（JSON 输出，非法则降级）                 │
│                             ▼                                                                 │
│                    ┌─────────────────┐   ┌──────────────────┐                                │
│                    │   Digest 生成    │──►│  Publisher 推送    │──► 微信（企业微信/Server酱）   │
│                    └─────────────────┘   └──────────────────┘                                │
│                             │                                                                │
│                             └──► logs/last_digest.md（dry-run 预览）                          │
│                                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────────┐             │
│  │ 反馈闭环：daily-picks feedback like|dislike <id> → 更新 interest_weights  │             │
│  └────────────────────────────────────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 工作原理

每日 08:00（Asia/Shanghai）触发：并发采集 6 源 → 去重入库 → 规则打分取 ≤40 候选 → DeepSeek
精排 Top 10（失败自动降级为规则分）→ 生成简报 → 推送微信；当日幂等，重复运行不重复推送。

## 点击追踪（v2）：点击即反馈

推送链接默认是短链中转：`{你的 Worker 域名}/c/{8位随机码}`。点击链接 → Worker 302
跳转原文并记录事件 → 下次 `daily-picks run`（或手动 `daily-picks track sync`）自动把
点击回写为偏好（命中关键词权重 +0.05，上限 2.0）。

启用步骤（详见 `worker/README.md`）：

1. 部署 Worker（Cloudflare 免费）：`cd worker` 按 README 三步（d1 create → secret put → deploy）。
2. 本地配置 `config.yaml`：

```yaml
tracking:
  base_url: "https://daily-picks-track.xxx.workers.dev"
```

3. `.env` 加 `TRACKING_API_TOKEN=<与 Worker 端 API_TOKEN 同值>`。
4. `daily-picks test track` 自检连通；`daily-picks track sync` 手动同步点击。

未配置 `tracking.base_url` 时功能关闭，行为与 v1 完全一致。点击事件仅含文章 id 与
时间戳，存储于你自己的 Cloudflare D1（个人账户）。

## 快速开始

要求：Python 3.11 + [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:pzh-ops/daily-picks.git && cd daily-picks
uv sync                                  # 安装依赖

cp .env.example .env                     # 填入 DEEPSEEK_API_KEY（推送 key 可选）
uv run daily-picks init                  # 生成 config.yaml + 初始化 SQLite（5 张表）

uv run daily-picks run --dry-run         # 预览今日精选（写 logs/last_digest.md，不推送）
uv run daily-picks test push             # 推送连通性自检（发一条测试消息）
uv run daily-picks test llm              # LLM 连通性自检（验证 DeepSeek key 与延迟）
uv run daily-picks run                   # 立即完整执行（采集→排序→推送）
uv run daily-picks serve                 # 常驻调度：每天 08:00（Asia/Shanghai）自动执行
uv run daily-picks feedback like 12      # 偏好反馈：like/dislike 调整关键词权重（可加 --keyword）
uv run daily-picks stats --days 7        # 统计报表：运行/推送/token/成本（USD + CNY）
```

## 配置说明

### config.yaml（`daily-picks init` 生成）

全部可调参数见 `config.example.yaml` 与 `docs/01-设计文档.md §11`：内容源开关与参数、
LLM 参数（模型默认 `deepseek-v4-pro`）、兴趣关键词权重、推送渠道（`wecom` / `serverchan` / `none`）、
数据库与日志路径。

### .env（不入 Git，模板见 .env.example）

只需要 3 个 key：

| 环境变量 | 用途 | 必填 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API Key | ✅ |
| `WECOM_WEBHOOK_KEY` | 企业微信机器人 webhook key（推送渠道二选一） | 选填 |
| `SERVERCHAN_SENDKEY` | Server酱 SendKey（推送渠道二选一） | 选填 |

## 真实运行效果

**微信推送**（企业微信机器人，2026-08-27 首次真实推送）：

```
📌 今日精选 · 2026-08-27
1. 【知乎】Qwen3.8-Flash新架构模型，训练成本降9成… —— 大模型新架构
2. 【InfoQ】535B 大模型"直播"训练三个月，吴恩达公开力挺…
3. 【InfoQ】大模型推理加速全链路：内存管理、编译优化…
4. 【知乎】怎么看一周 70 万亿 Token的 GLM 5.3 Flash…
5. 【InfoQ】DeepSeek 开源 Harness：AI 智能体基础设施开始"拆分"…
…
推送成功（wecom）: errcode=0 ok ｜ 单次成本 ¥0.09
```

- 采集 6 源 133 条 → 去重 → 规则打分 40 候选 → LLM 精排 10 条 → 微信推送，全流程真实可用。
- `logs/last_digest.md` 保存每次 dry-run 预览；`daily-picks stats` 查看历史成本。

## 开发

```bash
uv run pytest          # 全量测试（200 例，覆盖率 97.7%）
uv run ruff check .    # Lint
```

详见 `docs/`（设计/开发/测试文档）与 `AGENTS.md`。

## 贡献

欢迎提交 issue 与 PR：

1. 提 issue：bug 报告、内容源失效、功能建议均可
2. 开发流程：Fork → 新建分支 → 修改 → `uv run pytest` + `uv run ruff check .` 全绿后提 PR 到 `main`
3. 新功能请补测试；测试禁止真实网络请求（HTTP 一律 respx mock），详见 `docs/03-测试文档.md`

## 许可证

[MIT](./LICENSE) © 2026 peterzhang176@gmail.com
