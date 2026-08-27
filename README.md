# DailyPicks（今日精选）

每天自动从 **B站 / 知乎 / 掘金 / Hacker News / InfoQ / RSS** 聚合候选内容，用 LLM 按个人兴趣
筛选排序、去重，生成"每日精选"简报并推送到微信的个人 Agent。

> 最小可用的个人 Agent 范式：采集（Collect）→ 理解（Understand）→ 决策（Decide）→ 推送（Deliver），
> 外加一个可学习的偏好反馈闭环。

**当前进度**：M0 脚手架 ✅ · M1 采集层 ✅ · M2 排序与 LLM 精排 ✅ · **M3 简报与微信推送（进行中）** · M4 调度闭环 · M5 打磨开源

## 功能特性

| 需求 | 功能 | 状态 |
|---|---|---|
| R-001 | ≥6 类内容源（RSS/B站/知乎/掘金/HN/InfoQ），每源独立开关、失败隔离 | ✅ M1 |
| R-002 | 跨源去重（content_hash 唯一约束） | ✅ M0 |
| R-003 | 规则打分：关键词权重 + 来源权重 + 时效加成 | ✅ M2 |
| R-004 | LLM 精排（DeepSeek v4-pro，非法输出自动降级规则分） | ✅ M2 |
| R-005 | 微信推送：企业微信机器人 / Server酱 二选一 | 🔨 M3 |
| R-006 | 每日 08:00 调度，当日幂等不重复推送 | ⏳ M4 |
| R-007 | 偏好反馈：like/dislike 调整关键词权重 | ⏳ M4 |
| R-008 | 统计报表：推送历史 / token / 成本 | ⏳ M4 |
| R-009 | dry-run 预览（不推送，输出精选到控制台） | ✅ M1 |
| R-010 | 自检命令：`test llm` / `test push` | ⏳ M4 |
| R-011 | 日志落盘 + 轮转 | ✅ M0 |

## 真实精选效果（M2 实测，LLM 精排）

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

## 快速开始

要求：Python 3.11 + [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:pzh-ops/daily-picks.git && cd daily-picks
uv sync                                  # 安装依赖

cp .env.example .env                     # 填入 DEEPSEEK_API_KEY（推送 key 可选）
uv run daily-picks init                  # 生成 config.yaml + 初始化 SQLite（5 张表）

uv run daily-picks run --dry-run         # 预览今日精选（控制台输出，不推送）
uv run daily-picks test llm              # LLM 连通性自检
uv run daily-picks run                   # 立即完整执行（采集→排序→推送）
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

## Demo 截图

> 待补充：M3 推送功能完成后，在此补充真实截图——微信收到的推送效果、`stats` 成本报表。

## 开发

```bash
uv run pytest          # 全量测试（111 例，覆盖率 98.5%）
uv run ruff check .    # Lint
```

详见 `docs/`（设计/开发/测试文档）与 `AGENTS.md`。
