# DailyPicks v3「深度内容精选」实施计划（M10–M13）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v1/v2 聚合精选基础上实现 v3「少而精的深度内容」：启动向导配置用户画像、LLM 深度评分选材、v3 推送模板、文字反馈闭环与权重演化。

**Architecture:** 复用 v2 全部采集/去重/打分/推送链路，在 `cli.run_once` 的"规则打分 → LLM 精排"之间插入 deep 阶段（只对规则分 Top-N 评分，低于阈值过滤，fail-open）；推送按 `profile.enabled` 切换 v3 模板；新增 setup（向导）、deep（深度评分）、digest_v3（模板）、feedback_channels（通道抽象）四个模块，feedback.py 扩展文字反馈解析与权重演化；storage 追加 4 张表与读写方法。

**Tech Stack:** Python 3.11 + uv；SQLite（本地）；DeepSeek `deepseek-v4-pro`（OpenAI 兼容接口，`httpx` + `tenacity` 重试）；pydantic v2 配置；pytest（asyncio_mode=auto）+ respx mock；ruff（line-length 110，忽略 DTZ）。

**Spec:** 本计划实施以下文档（计划从规格出发，规格随计划同行，执行者须同时阅读）：
- `docs/04-v3设计文档.md` — v3 唯一事实来源（锁定函数签名/表 DDL/配置键/里程碑 M10-M13）
- `docs/05-v3开发文档.md` — 实现细节与 prompt 模板
- `docs/06-v3测试文档.md` — 用例清单（T-SETUP/T-DEEP/T-DG3/T-FB/T-EV 编号）
- 基准：`docs/01-设计文档.md`、`docs/02-开发文档.md`、`docs/03-测试文档.md`（v1/v2 架构与回归基线）

## Global Constraints

以下约束来自 AGENTS.md 与 docs/04-06，**每个任务的验收隐含包含全部条目**：

1. **先文档后代码（AGENTS.md 第 4 条）**：函数签名、字段名、配置键、SQL schema 以 docs/04 为准，不得自行改名；发现文档与实现冲突时**先改文档再改代码**，并在提交信息中注明。本计划预判的 6 处冲突及其裁决见下节「文档先行修正总览」。
2. **回归零破坏（R-V3-09）**：全量 242 例 v1/v2 测试不得回归；`init/run/serve/feedback like/stats/test/track sync` 旧命令全部可用。
3. **完成标准（AGENTS.md 第 5 条）**：`pytest` 全绿 + 覆盖率 ≥85%（`pyproject.toml` `fail_under=85`，`daily_picks/cli.py` 与 `__main__.py` 不在统计范围）+ `ruff check daily_picks/ tests/` 零告警 + `daily-picks run --dry-run` 可出预览。
4. **提交格式（AGENTS.md 第 7 条）**：`M<X>: <模块> - <一句话描述>`，如 `M10: setup - 实现启动向导`。
5. **环境与依赖**：Python 3.11 + uv；新增依赖用 `uv add`（本计划预计无需新增依赖）；测试命令统一 `uv run pytest -q`、`uv run pytest --cov=daily_picks --cov-report=term-missing`、`uv run ruff check daily_picks/ tests/`。
6. **LLM 调用约定（docs/05 §0）**：所有 LLM 调用走 `daily_picks/llm.py` 的 `LLMClient.chat(system=, user=, json_mode=True)`（该方法本计划新增，见 Task 4）；LLM 失败一律 fail-open，不抛裸异常到 CLI 顶层（docs/05 §6）。
7. **常驻服务重启（AGENTS.md 第 7 条，2026-08-31 事故教训）**：修改任何 `daily_picks/` 代码后必须 `systemctl --user restart daily-picks.service`，否则线上继续跑旧代码。每个里程碑验收末尾执行。
8. **测试文件命名（docs/06）**：`tests/test_m10_setup.py`、`tests/test_m11_deep.py`、`tests/test_digest_v3.py`、`tests/test_m12_feedback.py`、`tests/test_m13_evolve.py`；LLM 全部 mock（不走网络），mock 方式参照 `tests/test_ranker.py` 的 FakeLLM。
9. **代码风格**：新文件头部 `from __future__ import annotations`；模块 docstring 中文并注明设计文档出处；ruff 行宽 110；对齐现有模块风格。
10. **兼容开关语义（docs/04 §5）**：`profile.enabled: false` = 完全走 v2 行为；所有 v3 分支以 `cfg.profile.enabled` 为总开关。

## 文档先行修正总览（AGENTS.md 第 4 条：先改文档再改代码）

本计划写作者通读 docs/04-06 与现有代码后，发现 **6 处文档与实现/文档内部的冲突**。每处在对应任务的 Step 1 中修改文档并提交（提交信息注明），随后才实现代码：

| # | 冲突 | 裁决（写入文档） | 落地任务 |
|---|---|---|---|
| A1 | docs/05 §1.3 用 `asyncio.run(run_setup(...))` 调用，但 docs/04 §6.1 签名为同步 `def run_setup`（asyncio.run 只接受协程） | docs/04 §6.1 `run_setup` 改为 `async def run_setup` | Task 1 |
| A2 | docs/04 §6.1 `_llm_recommend(tags, llm)` 要求"结果写 source_registry"，但签名无 storage 参数 | docs/04 §6.1：`recommend_sources`/`_llm_recommend` 增 `storage: Storage` 参数并改为 `async def` | Task 4 |
| A3 | docs/05 §0 约定走 `LLMClient.chat(system=, user=, json_mode=True)`，但现有 `daily_picks/llm.py` 无 `chat` 方法（只有 `rank`/`_chat`） | 不冲突——docs/05 是需求，本计划新增 `LLMClient.chat` 实现（Task 4）；docs/05 §0 补一行实现备注 | Task 4 |
| A4 | docs/04 §6.2 `deep_filter(candidates, llm, threshold)` 签名无 `weights` 与 `top_n` 参数，但内部需向 `deep_analyze` 传兴趣权重、降阈值判定用 profile.top_n | docs/04 §6.2：`deep_filter` 增可选参数 `weights: dict[str, float] \| None = None`（3 参调用保持合法）；降阈值判定改用模块常量 `DEEP_MIN_COUNT = 5`（对齐 profile.top_n 默认值）；docs/04 §3 架构图 "pipeline.py" 注记改为集成于 `cli.py run_once`（该文件不存在，docs/05 §2.2 已允许） | Task 6 |
| A5 | docs/04 §6.3 `apply_feedback(fb, storage, extract_keywords=True)` 与现有 v1 `feedback.py::apply_feedback(storage, article_id, kind, extra_keyword)` 同名不同签名；且 `ParsedFeedback` 无 `raw` 字段，无法落 `feedback_text.raw_text`（NOT NULL） | docs/04 §6.3：`ParsedFeedback` 增 `raw: str` 字段；`apply_feedback` 注明"同名分派：首参为 ParsedFeedback → v3 文字反馈；否则保持 v1 like/dislike 行为（R-V3-09 兼容）"；docs/05 §3.2 同步 | Task 11 |
| A6 | docs/05 §4.1 要求 evolve_weights 对 clicks 增量 +0.05，但 v2 `tracking.sync_clicks` 同步时已回写 +0.05（同一点击会双重回写） | docs/05 §4.1 增"游标协作"条款：`sync_clicks` 回写权重后同步推进 meta 键 `last_weight_evolve_id`，evolve_weights 点击演化仅覆盖游标之后未被 sync 回写的 clicks 行（防御性兜底）；登记两个新 meta 键 | Task 15 |

## File Structure（本计划新增/修改的文件全景）

**新增（v3 模块）：**

| 文件 | 职责 | 落地产物 |
|---|---|---|
| `daily_picks/setup.py` | 启动向导：choose_tags / recommend_sources / _llm_recommend / choose_top_n / run_setup | Task 3/4/5 |
| `daily_picks/deep.py` | 深度评分与关键词提取：DeepResult / deep_analyze / deep_filter / format_keywords | Task 6/7/8 |
| `daily_picks/digest_v3.py` | v3 推送模板：build_digest_v3 / source_display_name | Task 8 |
| `daily_picks/feedback_channels.py` | 反馈通道抽象：FeedbackChannel / RawFeedback / HermesChannel | Task 10 |
| `daily_picks/weights.py` | 权重演化公共函数：_bump_keywords（tracking 与 evolve 共用） | Task 14 |
| `tests/test_m10_setup.py` | M10 用例（docs/06 §1） | Task 1–5 |
| `tests/test_m11_deep.py` | M11 deep 用例（docs/06 §2）+ run_once 集成 | Task 6/7/9 |
| `tests/test_digest_v3.py` | v3 模板用例（docs/06 §3） | Task 8 |
| `tests/test_m12_feedback.py` | M12 反馈用例（docs/06 §4） | Task 10–13 |
| `tests/test_m13_evolve.py` | M13 演化用例（docs/06 §5）+ 补充 | Task 14–16 |

**修改（v1/v2 文件）：**

| 文件 | 修改点 | 落地产物 |
|---|---|---|
| `docs/04-v3设计文档.md` | A1/A2/A4/A5 修正 + 修订说明行 | Task 1/4/6/11 |
| `docs/05-v3开发文档.md` | A3/A5/A6 修正 + §4/§5 终稿 + 存储新方法清单 | Task 4/11/15/17 |
| `docs/06-v3测试文档.md` | 补充用例登记（§7 v3 补充用例） | Task 17 |
| `daily_picks/config.py` | ProfileConfig/FeedbackConfig/RootConfig/DEFAULT_YAML/_validate/save_config | Task 1 |
| `daily_picks/storage.py` | 4 张表 DDL + save_profile/load_profile/save_tag_weight/list_tags/register_source/list_sources（Task 2）+ add_feedback_text（Task 12）+ get_clicks_since/get_feedback_text_since/get_max_click_id（Task 15）+ get_v3_counts（Task 16） | Task 2/12/15/16 |
| `daily_picks/llm.py` | 新增 `chat(system, user, json_mode=True)` | Task 4 |
| `daily_picks/feedback.py` | ParsedFeedback/parse_feedback/_heuristic_feedback（Task 11）+ apply_feedback 分派/_apply_text_feedback/_bump_article_tags（Task 12）+ evolve_weights/游标常量（Task 15） | Task 11/12/15 |
| `daily_picks/tracking.py` | apply_click 复用 weights._bump_keywords（Task 14）+ sync_clicks 游标协作（Task 15） | Task 14/15 |
| `daily_picks/cli.py` | cmd_setup/setup 子命令（Task 5）+ run_once deep 阶段与 v3 模板（Task 9）+ feedback 文字路由（Task 13）+ evolve 集成与 stats v3 计数（Task 16） | Task 5/9/13/16 |
| `README.md` | v3 章节 | Task 17 |

---

# 里程碑 M10：用户画像与启动向导

> 里程碑验收（design §9 M10）：`pytest` 全绿；`daily-picks setup` 管道输入跑通；覆盖率 ≥85%；提交后执行 `systemctl --user restart daily-picks.service`。

## Task 1: 文档修正 A1 + config.py v3 配置段

**目标**：先改文档（A1）；config.py 新增 ProfileConfig/FeedbackConfig、DEFAULT_YAML 两段、_validate 校验、save_config 写回函数。

**涉及文件**：
- 修改：`docs/04-v3设计文档.md`（§6.1 run_setup 签名）
- 修改：`daily_picks/config.py`（RootConfig 段、DEFAULT_YAML、_validate、新增 save_config）
- 测试：`tests/test_m10_setup.py`（新建，补充用例——docs/06 未编号，登记见 Task 17）

**接口**：
- Produces（后续任务依赖）：
  - `class ProfileConfig(BaseModel)`: `enabled: bool = False; top_n: int = 5; deep_threshold: int = 60; deep_candidates: int = 40; tags: list[str] = []; sources: list[str] = []`
  - `class FeedbackConfig(BaseModel)`: `channel: str = "hermes"; extract_keywords: bool = True`
  - `RootConfig.profile: ProfileConfig` / `RootConfig.feedback: FeedbackConfig`
  - `def save_config(cfg: RootConfig, path: str = DEFAULT_CONFIG_PATH) -> None`

- [ ] **Step 1: 修改文档（AGENTS.md 第 4 条：先改文档）**

`docs/04-v3设计文档.md` §6.1 中：

```diff
-def run_setup(cfg: RootConfig, storage: Storage, llm: LLMClient | None) -> int:
+async def run_setup(cfg: RootConfig, storage: Storage, llm: LLMClient | None) -> int:
```

并在 §6.1 末尾追加修订说明行：

```
- 修订说明（2026-08-31）：run_setup 为协程——docs/05 §1.3 以 asyncio.run 调用，
  且内部需 await _llm_recommend（LLM 调用）。实现以 async 为准。
```

- [ ] **Step 2: 写失败测试**

新建 `tests/test_m10_setup.py`：

```python
"""M10 用例：配置 v3 段 / setup 向导 / 存储方法（测试文档 docs/06 §1；LLM 全部 mock）。"""

from __future__ import annotations

import pytest

from daily_picks.config import ConfigError, RootConfig, load_config, save_config, write_default_config


class TestV3Config:
    """补充用例：profile/feedback 配置段（docs/06 未编号，登记见 Task 17）。"""

    def test_profile_defaults_disabled(self):
        cfg = RootConfig()
        assert cfg.profile.enabled is False
        assert cfg.profile.top_n == 5
        assert cfg.profile.deep_threshold == 60
        assert cfg.profile.deep_candidates == 40
        assert cfg.feedback.channel == "hermes"
        assert cfg.feedback.extract_keywords is True

    def test_load_config_parses_v3_sections(self, tmp_path):
        path = tmp_path / "config.yaml"
        write_default_config(path)
        text = path.read_text(encoding="utf-8")
        text = text.replace("profile:\n  enabled: false",
                            "profile:\n  enabled: true\n  top_n: 3")
        path.write_text(text, encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.profile.enabled is True
        assert cfg.profile.top_n == 3

    def test_invalid_profile_top_n_rejected(self, tmp_path):
        path = tmp_path / "config.yaml"
        write_default_config(path)
        text = path.read_text(encoding="utf-8").replace("top_n: 5", "top_n: 99")
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="profile.top_n"):
            load_config(str(path))

    def test_invalid_deep_threshold_rejected(self, tmp_path):
        path = tmp_path / "config.yaml"
        write_default_config(path)
        text = path.read_text(encoding="utf-8").replace("deep_threshold: 60",
                                                        "deep_threshold: 150")
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="deep_threshold"):
            load_config(str(path))

    def test_invalid_feedback_channel_rejected(self, tmp_path):
        path = tmp_path / "config.yaml"
        write_default_config(path)
        text = path.read_text(encoding="utf-8").replace("channel: hermes", "channel: telegram")
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="feedback.channel"):
            load_config(str(path))

    def test_save_config_roundtrip(self, tmp_path):
        cfg = RootConfig()
        cfg.profile.enabled = True
        cfg.profile.tags = ["AI大模型", "编程开发"]
        cfg.profile.top_n = 3
        path = tmp_path / "out.yaml"
        save_config(cfg, str(path))
        cfg2 = load_config(str(path))
        assert cfg2.profile.enabled is True
        assert cfg2.profile.tags == ["AI大模型", "编程开发"]
        assert cfg2.profile.top_n == 3
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_m10_setup.py -v
```
预期：FAIL（`AttributeError: 'RootConfig' object has no attribute 'profile'` / `ImportError: save_config`）。

- [ ] **Step 4: 实现**

`daily_picks/config.py`：

1. 在 `DigestConfig` 之后新增（docs/04 §5 锁定字段）：

```python
class ProfileConfig(BaseModel):
    """v3 深度精选配置（docs/04 §5）。enabled=False 时完全走 v2 行为。"""
    enabled: bool = False          # v3 默认关，setup 向导后开
    top_n: int = 5
    deep_threshold: int = 60
    deep_candidates: int = 40
    tags: list[str] = []
    sources: list[str] = []


class FeedbackConfig(BaseModel):
    """v3 文字反馈配置（docs/04 §5）。"""
    channel: str = "hermes"        # 'hermes'（本期）| 'wecom'（后期）
    extract_keywords: bool = True
```

2. `RootConfig` 追加两字段（`tracking` 之后）：

```python
    profile: ProfileConfig = ProfileConfig()
    feedback: FeedbackConfig = FeedbackConfig()
```

3. `DEFAULT_YAML` 在 `digest:` 段之后插入（docs/04 §5 原样）：

```yaml
profile:
  enabled: false            # v3 深度精选开关；setup 向导完成后自动置 true
  top_n: 5                  # 每日推送条数（1-10）
  deep_threshold: 60        # 深度评分阈值（0-100），低于此值不推送
  deep_candidates: 40       # deep 阶段最多评分的候选数（LLM 成本控制）
  tags: []                  # setup 写入（JSON 数组，实际存 user_profile 表）
  sources: []               # setup 写入

feedback:
  channel: hermes           # 'hermes'（本期）| 'wecom'（后期）
  extract_keywords: true    # 反馈文字是否提取关键词写入 interest_weights
```

4. `_validate` 末尾追加：

```python
    if not 1 <= cfg.profile.top_n <= 10:
        raise ConfigError(f"profile.top_n 越界: {cfg.profile.top_n}（要求 1-10）")
    if not 0 <= cfg.profile.deep_threshold <= 100:
        raise ConfigError(f"profile.deep_threshold 越界: {cfg.profile.deep_threshold}（要求 0-100）")
    if cfg.profile.deep_candidates < 1:
        raise ConfigError(f"profile.deep_candidates 必须 >= 1: {cfg.profile.deep_candidates}")
    if cfg.feedback.channel not in {"hermes", "wecom"}:
        raise ConfigError(f"feedback.channel 非法: {cfg.feedback.channel!r}（可选 hermes | wecom）")
```

5. 文件末尾新增（docs/05 §1.1）：

```python
def save_config(cfg: RootConfig, path: str = DEFAULT_CONFIG_PATH) -> None:
    """把 RootConfig 写回 YAML（docs/05 §1.1，setup 向导持久化配置用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_m10_setup.py -v
uv run ruff check daily_picks/config.py tests/test_m10_setup.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 6: 提交**

```bash
git add docs/04-v3设计文档.md daily_picks/config.py tests/test_m10_setup.py
git commit -m "M10: docs - 修正 run_setup 异步签名（先改文档，AGENTS.md 第4条）"
git commit -m "M10: config - profile/feedback 配置段与 save_config"
```

**验收命令**（本任务）：`uv run pytest tests/test_m10_setup.py -q` 全绿；`uv run ruff check daily_picks/config.py` 零告警。

---

## Task 2: storage.py v3 四张表与画像读写

**目标**：`_SCHEMA` 追加 docs/04 §4 锁定的 4 张表 DDL；实现 save_profile/load_profile/save_tag_weight/list_tags/register_source/list_sources（docs/05 §1.2 锁定签名）。

**涉及文件**：
- 修改：`daily_picks/storage.py`（`_SCHEMA` 字符串 + 新方法；顶部 `import json`）
- 测试：`tests/test_m10_setup.py`（T-SETUP-12 + 补充）

**接口**：
- Consumes：`Storage._execute` / `Storage._lock`（现有）
- Produces（后续任务依赖）：
  - `def save_profile(self, tags: list[str], sources: list[str], top_n: int) -> None`
  - `def load_profile(self) -> dict | None`  # dict 含 `tags: list[str]`、`sources: list[str]`、`top_n: int`；无行返回 None
  - `def save_tag_weight(self, tag: str, weight: float, source: str = "manual") -> None`  # clamp [0.2, 2.0]
  - `def list_tags(self) -> list[tuple[str, float]]`  # ORDER BY weight DESC
  - `def register_source(self, key: str, name: str, url: str, tags: list[str]) -> None`
  - `def list_sources(self, enabled_only: bool = True) -> list[dict]`  # dict 含 key/name/kind/url/tags(list)/enabled

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m10_setup.py`：

```python
import json

from daily_picks.storage import StorageError


class TestProfileStorage:
    """docs/06 §1 T-SETUP-12 + 补充：user_profile/tag_weights/source_registry 读写。"""

    # T-SETUP-12：save_profile clamp/拒绝
    def test_save_profile_rejects_out_of_range_top_n(self, tmp_db):
        with pytest.raises(StorageError, match="top_n"):
            tmp_db.save_profile(["AI大模型"], [], 0)
        with pytest.raises(StorageError, match="top_n"):
            tmp_db.save_profile(["AI大模型"], [], 99)

    def test_save_and_load_profile_roundtrip(self, tmp_db):
        assert tmp_db.load_profile() is None  # 初始无行
        tmp_db.save_profile(["AI大模型", "创业商业"], ["hnews", "rss:机器之心"], 5)
        profile = tmp_db.load_profile()
        assert profile == {"tags": ["AI大模型", "创业商业"],
                           "sources": ["hnews", "rss:机器之心"], "top_n": 5}

    def test_save_profile_keeps_single_row(self, tmp_db):
        tmp_db.save_profile(["A"], [], 5)
        tmp_db.save_profile(["B"], [], 3)  # INSERT OR REPLACE，id=1 单行
        rows = tmp_db._conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0]
        assert rows == 1
        assert tmp_db.load_profile()["tags"] == ["B"]

    def test_tag_weight_upsert_and_clamp(self, tmp_db):
        tmp_db.save_tag_weight("AI大模型", 3.0)  # clamp → 2.0
        assert tmp_db.list_tags() == [("AI大模型", 2.0)]
        tmp_db.save_tag_weight("AI大模型", 0.1, source="feedback")  # clamp → 0.2
        assert tmp_db.list_tags() == [("AI大模型", 0.2)]

    def test_register_and_list_sources(self, tmp_db):
        tmp_db.register_source("rss:机器之心", "机器之心",
                               "https://www.jiqizhixin.com/rss", ["AI大模型"])
        rows = tmp_db.list_sources()
        assert len(rows) == 1
        assert rows[0]["key"] == "rss:机器之心"
        assert rows[0]["kind"] == "rss"
        assert rows[0]["tags"] == ["AI大模型"]
        assert json.loads(tmp_db._conn.execute(
            "SELECT tags FROM source_registry").fetchone()[0]) == ["AI大模型"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m10_setup.py::TestProfileStorage -v
```
预期：FAIL（`AttributeError: 'Storage' object has no attribute 'save_profile'`）。

- [ ] **Step 3: 实现**

`daily_picks/storage.py` 头部加 `import json`（`sqlite3` 之后）。`_SCHEMA` 字符串末尾追加 docs/04 §4 的 4 张表 DDL（**逐字复制，勿改**）：

```sql
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
```

`Storage` 类 `get_stats` 方法之后新增：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m10_setup.py -q
uv run pytest tests/test_storage.py tests/test_feedback.py tests/test_tracking.py -q
uv run ruff check daily_picks/storage.py tests/test_m10_setup.py
```
预期：全 PASS（含 v1/v2 回归），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/storage.py tests/test_m10_setup.py
git commit -m "M10: storage - user_profile/tag_weights/feedback_text/source_registry 表与读写"
```

**验收命令**（本任务）：`uv run pytest tests/test_m10_setup.py tests/test_storage.py -q` 全绿。

---

## Task 3: setup.py 交互函数（choose_tags / choose_top_n）

**目标**：实现 docs/04 §6.1 的两个纯交互函数（同步，input()/print() 驱动）。

**涉及文件**：
- 新建：`daily_picks/setup.py`（DEFAULT_TAGS / TAG_SOURCE_MAP / choose_tags / choose_top_n）
- 测试：`tests/test_m10_setup.py`（T-SETUP-01/02/03/07/08）

**接口**：
- Produces：
  - `DEFAULT_TAGS: list[str]`、`TAG_SOURCE_MAP: dict[str, list[str]]`（docs/04 §6.1 原值，逐字复制）
  - `def choose_tags(llm: LLMClient | None = None) -> list[str]`
  - `def choose_top_n() -> int`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m10_setup.py`：

```python
from daily_picks.setup import DEFAULT_TAGS, TAG_SOURCE_MAP, choose_tags, choose_top_n


class TestChooseTags:
    # T-SETUP-01 默认标签展示
    def test_default_first_three(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert choose_tags() == DEFAULT_TAGS[:3]

    # T-SETUP-02 序号多选
    def test_index_multi_select(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "1,3,5")
        assert choose_tags() == ["AI大模型", "创业商业", "人文历史"]

    # T-SETUP-03 自定义标签
    def test_custom_tag(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "自定义:量子计算")
        assert choose_tags() == ["量子计算"]

    def test_mixed_select_and_custom(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "1, 自定义:量子计算")
        assert choose_tags() == ["AI大模型", "量子计算"]

    def test_all_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "99,xyz")
        assert choose_tags() == DEFAULT_TAGS[:3]


class TestChooseTopN:
    # T-SETUP-07 条数默认
    def test_default_five(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert choose_top_n() == 5

    # T-SETUP-08 越界重输
    def test_out_of_range_reprompts(self, monkeypatch, capsys):
        inputs = iter(["99", "3"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        assert choose_top_n() == 3
        assert "1-10" in capsys.readouterr().out
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m10_setup.py::TestChooseTags tests/test_m10_setup.py::TestChooseTopN -v
```
预期：FAIL（`ModuleNotFoundError: No module named 'daily_picks.setup'`）。

- [ ] **Step 3: 实现**

新建 `daily_picks/setup.py`（本任务先写常量与两个同步函数，其余函数在 Task 4/5 追加）：

```python
"""v3 启动向导：标签/来源/条数 → user_profile + config.yaml（docs/04 §6.1 / docs/05 §1）。"""

from __future__ import annotations

import logging

from daily_picks.config import RootConfig
from daily_picks.llm import LLMClient
from daily_picks.storage import Storage

logger = logging.getLogger("daily_picks.setup")

# 默认标签（docs/04 §6.1，锁定）
DEFAULT_TAGS: list[str] = [
    "AI大模型", "编程开发", "创业商业", "投资经济", "人文历史", "个人成长",
]

# 默认标签 → 内置源推荐（docs/04 §6.1，锁定；key 见 §6.5）
TAG_SOURCE_MAP: dict[str, list[str]] = {
    "AI大模型": ["hnews", "infoq", "rss:机器之心"],
    "编程开发": ["juejin", "hnews", "rss:阮一峰"],
    "创业商业": ["rss:36氪", "rss:虎嗅"],
    "投资经济": ["rss:华尔街见闻", "rss:雪球"],
    "人文历史": ["rss:知乎日报", "rss:豆瓣书评"],
    "个人成长": ["rss:少数派", "rss:得到"],
}


def choose_tags(llm: LLMClient | None = None) -> list[str]:
    """展示 DEFAULT_TAGS 多选（逗号分隔序号），支持 '自定义:xxx' 追加。空回车 = 默认前 3 个。

    llm 参数为 docs/04 §6.1 锁定签名预留（本期交互选标签不调用 LLM）。
    """
    print("选择你感兴趣的主题标签（可多选）：")
    for i, tag in enumerate(DEFAULT_TAGS, start=1):
        print(f"  {i}. {tag}")
    raw = input("输入序号（逗号分隔），或 `自定义:xxx` 追加，空回车 = 默认前 3 个: ").strip()
    if not raw:
        return list(DEFAULT_TAGS[:3])
    tags: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("自定义:"):
            custom = part.split(":", 1)[1].strip()
            if custom:
                tags.append(custom)
        elif part.isdigit() and 1 <= int(part) <= len(DEFAULT_TAGS):
            tags.append(DEFAULT_TAGS[int(part) - 1])
    return tags or list(DEFAULT_TAGS[:3])  # 全部非法输入 → 回退默认前 3 个


def choose_top_n() -> int:
    """输入每日条数，默认 5，范围 1-10（越界/非法重输，docs/04 §6.1）。"""
    while True:
        raw = input("每日推送条数（1-10，回车默认 5）: ").strip()
        if not raw:
            return 5
        try:
            top_n = int(raw)
        except ValueError:
            print("请输入数字。")
            continue
        if not 1 <= top_n <= 10:
            print("条数须在 1-10 之间。")
            continue
        return top_n
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m10_setup.py -q
uv run ruff check daily_picks/setup.py tests/test_m10_setup.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/setup.py tests/test_m10_setup.py
git commit -m "M10: setup - choose_tags/choose_top_n 交互函数"
```

**验收命令**（本任务）：`uv run pytest tests/test_m10_setup.py -q` 全绿。

---

## Task 4: LLMClient.chat + 来源推荐（recommend_sources / _llm_recommend）

**目标**：先改文档（A2/A3）；llm.py 新增 `chat`；setup.py 实现 LLM 来源推荐与注册。

**涉及文件**：
- 修改：`docs/04-v3设计文档.md`（§6.1 两签名）、`docs/05-v3开发文档.md`（§0 chat 备注）
- 修改：`daily_picks/llm.py`（新增 chat 方法）
- 修改：`daily_picks/setup.py`（recommend_sources / _llm_recommend）
- 测试：`tests/test_m10_setup.py`（T-SETUP-04/05/06/11 + chat 补充）

**接口**：
- Consumes：`LLMClient._chat` / `_extract_content` / `_strip_fences`（llm.py 现有）；`Storage.register_source/list_sources`
- Produces：
  - `async def chat(self, system: str, user: str, json_mode: bool = True) -> str`（docs/05 §0；返回 choices[0].message.content，json_mode 时剥 ```json 围栏；网络/密钥异常抛 `LLMError`，调用方 fail-open）
  - `async def recommend_sources(tags: list[str], llm: LLMClient | None, storage: Storage) -> list[str]`（A2 裁决后签名）
  - `async def _llm_recommend(tags: list[str], llm: LLMClient, storage: Storage) -> list[str]`（A2 裁决后签名；返回注册成功的源 key 列表）

- [ ] **Step 1: 修改文档（AGENTS.md 第 4 条）**

`docs/04-v3设计文档.md` §6.1：

```diff
-def recommend_sources(tags: list[str], llm: LLMClient | None) -> list[str]:
+async def recommend_sources(tags: list[str], llm: LLMClient | None, storage: Storage) -> list[str]:
     """tags ∩ TAG_SOURCE_MAP 的并集为推荐源；llm 非空时调用 _llm_recommend 补充。"""

-def _llm_recommend(tags: list[str], llm: LLMClient) -> list[str]:
+async def _llm_recommend(tags: list[str], llm: LLMClient, storage: Storage) -> list[str]:
     """LLM 按标签推荐信息源（JSON 输出 source_name/url 列表），结果写 source_registry。"""
```

§6.1 末尾追加修订说明行：

```
- 修订说明（2026-08-31）：recommend_sources/_llm_recommend 为协程并增 storage 参数——
  注册 source_registry 需要存储句柄，且调用 LLM 需 await。
```

`docs/05-v3开发文档.md` §0 追加一行：

```
- `LLMClient.chat(system=, user=, json_mode=True) -> str`：通用单轮 chat（M10 新增），
  返回 choices[0].message.content（json_mode 时剥离 ```json 围栏）；失败抛 LLMError，调用方 fail-open。
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/test_m10_setup.py`：

```python
import json as json_mod

from daily_picks.llm import LLMError
from daily_picks.setup import _llm_recommend, recommend_sources


class FakeChatLLM:
    """mock LLMClient.chat：返回预置 JSON 文本，记录调用参数。"""

    def __init__(self, reply: str = ""):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.calls.append((system, user))
        return self.reply


class TestRecommendSources:
    # T-SETUP-04 来源推荐内置映射
    async def test_builtin_map(self, tmp_db):
        sources = await recommend_sources(["AI大模型"], None, tmp_db)
        assert sources == ["hnews", "infoq", "rss:机器之心"]

    # T-SETUP-11 无 LLM 降级
    async def test_without_llm_uses_builtin_only(self, tmp_db):
        sources = await recommend_sources(["AI大模型", "编程开发"], None, tmp_db)
        assert set(sources) == {"hnews", "infoq", "rss:机器之心", "juejin", "rss:阮一峰"}
        assert tmp_db.list_sources() == []  # 无 LLM → 不注册任何自定义源

    def test_map_union_dedupes(self):
        assert TAG_SOURCE_MAP["AI大模型"][0] == "hnews"


class TestLlmRecommend:
    def _llm(self, reply: str) -> FakeChatLLM:
        return FakeChatLLM(reply)

    # T-SETUP-05 LLM 来源推荐
    async def test_registers_sources(self, tmp_db):
        llm = self._llm(json_mod.dumps(
            {"sources": [{"name": "机器之心", "url": "https://www.jiqizhixin.com/rss"}]},
            ensure_ascii=False))
        keys = await _llm_recommend(["AI大模型"], llm, tmp_db)
        assert keys == ["rss:机器之心"]
        rows = tmp_db.list_sources()
        assert rows[0]["key"] == "rss:机器之心"
        assert rows[0]["url"] == "https://www.jiqizhixin.com/rss"

    # T-SETUP-06 非法 url 跳过
    async def test_skips_invalid_url(self, tmp_db):
        llm = self._llm('{"sources": [{"name": "x", "url": "not-a-url"}]}')
        assert await _llm_recommend(["AI大模型"], llm, tmp_db) == []
        assert tmp_db.list_sources() == []

    async def test_invalid_json_returns_empty(self, tmp_db):
        llm = self._llm("这不是JSON")
        assert await _llm_recommend(["AI大模型"], llm, tmp_db) == []

    async def test_llm_error_fail_open(self, tmp_db):
        class RaisingLLM:
            async def chat(self, system, user, json_mode=True):
                raise LLMError("boom")

        sources = await recommend_sources(["AI大模型"], RaisingLLM(), tmp_db)
        assert sources == ["hnews", "infoq", "rss:机器之心"]  # 仅内置映射，不抛错


class TestLlmChat:
    """补充用例：LLMClient.chat 契约（docs/05 §0）。"""

    async def test_chat_strips_fences_and_passes_messages(self):
        class FakeClient:
            async def _chat(self, messages, **kw):
                self.messages = messages
                return {"choices": [{"message": {"content": "```json\n{\"a\": 1}\n```"}}]}

        client = FakeClient()
        # 直接绑定 LLMClient.chat 到假实例（验证消息构造与围栏剥离）
        from daily_picks.llm import LLMClient
        text = await LLMClient.chat(client, "sys", "user")
        assert text == '{"a": 1}'
        assert client.messages == [{"role": "system", "content": "sys"},
                                   {"role": "user", "content": "user"}]
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_m10_setup.py::TestRecommendSources tests/test_m10_setup.py::TestLlmRecommend tests/test_m10_setup.py::TestLlmChat -v
```
预期：FAIL（`ImportError: cannot import name 'recommend_sources'` / `AttributeError: 'LLMClient' object has no attribute 'chat'`）。

- [ ] **Step 4: 实现**

1. `daily_picks/llm.py` `LLMClient` 类 `rank` 方法之后新增：

```python
    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        """通用单轮 chat（docs/05 §0）：返回 choices[0].message.content；
        json_mode 时剥离 ```json 围栏。网络/密钥异常抛 LLMError（调用方 fail-open）。"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = await self._chat(messages)
        content = _extract_content(data)
        return _strip_fences(content) if json_mode else content
```

2. `daily_picks/setup.py` 头部补 `import json`，并在 `choose_top_n` 之后追加：

```python
async def recommend_sources(tags: list[str], llm: LLMClient | None, storage: Storage) -> list[str]:
    """tags ∩ TAG_SOURCE_MAP 的并集为推荐源；llm 非空时 _llm_recommend 补充（注册 source_registry）。

    LLM 失败 fail-open：只记 WARNING，仅用内置映射（docs/04 §10 降级表）。
    """
    sources: list[str] = []
    for tag in tags:
        for key in TAG_SOURCE_MAP.get(tag, []):
            if key not in sources:
                sources.append(key)
    if llm is not None:
        try:
            for key in await _llm_recommend(tags, llm, storage):
                if key not in sources:
                    sources.append(key)
        except LLMError as e:
            logger.warning("LLM 来源推荐失败（仅用内置映射）: %s", e)
    return sources


async def _llm_recommend(tags: list[str], llm: LLMClient, storage: Storage) -> list[str]:
    """LLM 按标签推荐信息源（prompt 见 docs/05 §5.3）；url 须以 http(s):// 开头，
    注册到 source_registry（key=`rss:<名称>`），返回注册成功的源 key 列表。"""
    system = "你是内容源推荐专家。根据用户兴趣标签推荐高质量深度内容源（RSS）。"
    user = (
        f"标签：{'、'.join(tags)}\n"
        '输出 JSON：{"sources": [{"name": "源名", "url": "https://...rss 地址"}]}（每个标签最多 3 个）'
    )
    text = await llm.chat(system, user, json_mode=True)
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as e:
        logger.warning("LLM 来源推荐输出非法 JSON: %s", e)
        return []
    if not isinstance(data, dict):
        return []
    keys: list[str] = []
    for item in data.get("sources", [])[: len(tags) * 3]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url.startswith(("http://", "https://")):
            logger.warning("LLM 推荐源被跳过（名称缺失或 url 非法）: %r", item)
            continue
        key = f"rss:{name}"
        storage.register_source(key, name, url, tags)
        keys.append(key)
    return keys
```

（`from daily_picks.llm import LLMClient, LLMError` 需在 setup.py 头部导入。）

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_m10_setup.py tests/test_llm.py -q
uv run ruff check daily_picks/setup.py daily_picks/llm.py tests/test_m10_setup.py
```
预期：全 PASS（test_llm.py 回归不受影响），ruff 零告警。

- [ ] **Step 6: 提交**

```bash
git add docs/04-v3设计文档.md docs/05-v3开发文档.md daily_picks/llm.py daily_picks/setup.py tests/test_m10_setup.py
git commit -m "M10: docs - 修正 recommend_sources 签名与 chat 约定（先改文档，AGENTS.md 第4条）"
git commit -m "M10: llm - 新增通用 chat(system, user, json_mode) 方法"
git commit -m "M10: setup - LLM 来源推荐与 source_registry 注册"
```

**验收命令**（本任务）：`uv run pytest tests/test_m10_setup.py tests/test_llm.py -q` 全绿。

---

## Task 5: run_setup 向导编排 + `daily-picks setup` 命令

**目标**：run_setup 串联三步向导 → user_profile + config.yaml 写回；cli.py 新增 setup 子命令。

**涉及文件**：
- 修改：`daily_picks/setup.py`（run_setup + DEFAULT_CONFIG_PATH/save_config 导入）
- 修改：`daily_picks/cli.py`（`build_parser` 加 p_setup、新增 cmd_setup、handlers 注册、导入 run_setup）
- 测试：`tests/test_m10_setup.py`（T-SETUP-09/10）

**接口**：
- Consumes：`choose_tags`/`recommend_sources`/`choose_top_n`（Task 3/4）、`storage.save_profile`（Task 2）、`save_config`（Task 1）、`cli._open_storage`/`cli._llm_key_missing`（现有）
- Produces：
  - `async def run_setup(cfg: RootConfig, storage: Storage, llm: LLMClient | None) -> int`（返回 0 成功 / 130 中断）
  - `def cmd_setup(args: argparse.Namespace) -> int`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m10_setup.py`：

```python
import argparse

from daily_picks.setup import run_setup
from daily_picks.storage import Storage


class TestRunSetup:
    def _env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        cfg = load_config("config.yaml")
        storage = Storage(tmp_path / "data" / "test.db")
        storage.init_schema()
        return cfg, storage

    # T-SETUP-09 完整向导写库
    async def test_full_wizard_writes_profile_and_config(self, tmp_path, monkeypatch, capsys):
        cfg, storage = self._env(tmp_path, monkeypatch)
        inputs = iter(["1,2", "", "3"])  # 标签 1,2 → 来源回车（内置推荐）→ 条数 3
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        assert await run_setup(cfg, storage, None) == 0
        profile = storage.load_profile()
        assert profile is not None
        assert profile["tags"] == ["AI大模型", "编程开发"]
        assert profile["top_n"] == 3
        assert set(profile["sources"]) >= {"hnews", "infoq", "juejin", "rss:机器之心", "rss:阮一峰"}
        cfg2 = load_config("config.yaml")  # config.yaml 已写回
        assert cfg2.profile.enabled is True
        assert cfg2.profile.top_n == 3
        assert cfg2.profile.tags == ["AI大模型", "编程开发"]
        assert "配置完成" in capsys.readouterr().out

    # T-SETUP-10 幂等重跑
    async def test_rerun_overwrites_single_row(self, tmp_path, monkeypatch):
        cfg, storage = self._env(tmp_path, monkeypatch)
        inputs = iter(["1", "", "5", "3", "", "4"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        assert await run_setup(cfg, storage, None) == 0
        assert await run_setup(cfg, storage, None) == 0
        rows = storage._conn.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0]
        assert rows == 1  # 第二次覆盖更新，仍单行
        assert storage.load_profile()["tags"] == ["AI大模型"]
        assert storage.load_profile()["top_n"] == 4

    async def test_keyboard_interrupt_returns_130(self, tmp_path, monkeypatch, capsys):
        cfg, storage = self._env(tmp_path, monkeypatch)

        def _raise_kb(prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _raise_kb)
        assert await run_setup(cfg, storage, None) == 130
        assert "向导未完成" in capsys.readouterr().out
        assert storage.load_profile() is None  # 未写库
```

CLI 层测试追加：

```python
class TestSetupCmd:
    """补充用例：setup 子命令解析与执行（cli.py 行为验证，不在覆盖率统计范围）。"""

    def test_parser_has_setup_subcommand(self):
        from daily_picks.cli import build_parser
        args = build_parser().parse_args(["setup"])
        assert args.command == "setup"

    def test_cmd_setup_runs_wizard(self, tmp_path, monkeypatch, capsys):
        from daily_picks import cli as cli_mod
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        inputs = iter(["1", "", "3"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        assert cli_mod.cmd_setup(argparse.Namespace()) == 0
        assert "配置完成" in capsys.readouterr().out
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        assert storage.load_profile()["top_n"] == 3
```

（`import argparse` 补到测试文件头部。）

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m10_setup.py::TestRunSetup tests/test_m10_setup.py::TestSetupCmd -v
```
预期：FAIL（`ImportError: cannot import name 'run_setup'`）。

- [ ] **Step 3: 实现**

1. `daily_picks/setup.py` 头部导入改为：

```python
from daily_picks.config import DEFAULT_CONFIG_PATH, RootConfig, save_config
```

文件末尾追加：

```python
async def run_setup(cfg: RootConfig, storage: Storage, llm: LLMClient | None) -> int:
    """交互式向导（docs/05 §1.1）：标签 → 来源 → 条数 → user_profile + config.yaml 写回。

    幂等可重跑；KeyboardInterrupt → 提示后返回 130，不写任何数据。
    """
    try:
        tags = choose_tags(llm)
        sources = await recommend_sources(tags, llm, storage)
        top_n = choose_top_n()
    except KeyboardInterrupt:
        print("向导未完成，可随时重跑 daily-picks setup")
        return 130
    storage.save_profile(tags, sources, top_n)
    cfg.profile.enabled = True
    cfg.profile.tags = tags
    cfg.profile.sources = sources
    cfg.profile.top_n = top_n
    save_config(cfg, DEFAULT_CONFIG_PATH)
    print(f"配置完成：标签 {len(tags)} 个、信息源 {len(sources)} 个、每日 {top_n} 条。")
    print("可运行 `daily-picks run --dry-run` 预览 v3 深度精选。")
    return 0
```

2. `daily_picks/cli.py`：

- 头部导入追加：`from daily_picks.setup import run_setup`
- `build_parser` 中 `p_stats` 之前插入：

```python
    p_setup = sub.add_parser("setup", help="v3 启动向导：标签/信息源/每日条数配置")
```

- `cmd_feedback` 之前新增：

```python
def cmd_setup(args: argparse.Namespace) -> int:
    """setup 子命令（docs/05 §1.3）。无 LLM key → llm=None，降级为纯内置映射推荐。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    storage = _open_storage(cfg)
    llm = LLMClient(cfg.llm) if not _llm_key_missing(cfg) else None
    return asyncio.run(run_setup(cfg, storage, llm))
```

- `main()` 的 handlers 字典加 `"setup": cmd_setup`。

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m10_setup.py tests/test_cli.py tests/test_config.py -q
uv run ruff check daily_picks/setup.py daily_picks/cli.py tests/test_m10_setup.py
```
预期：全 PASS（test_cli.py 回归不受影响），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/setup.py daily_picks/cli.py tests/test_m10_setup.py
git commit -m "M10: setup - run_setup 向导与 daily-picks setup 命令"
```

**验收命令**（本任务，M10 里程碑 gate）：

```bash
cd ~/daily-picks
uv run pytest -q                                                    # 全绿（242 + M10 新用例）
uv run pytest --cov=daily_picks --cov-report=term-missing | grep TOTAL   # ≥85%
uv run ruff check daily_picks/ tests/
# 管道输入跑通向导（临时目录防污染真实库）：
cd $(mktemp -d) && cp ~/daily-picks/config.example.yaml config.yaml && \
  uv run --project ~/daily-picks daily-picks setup <<< $'1,2\n\n3\n'
systemctl --user restart daily-picks.service   # AGENTS.md 第 7 条
```

---

# 里程碑 M11：深度选材与 v3 推送

> 里程碑验收（design §9 M11）：`pytest` 全绿；`run --dry-run`（mock LLM）输出 v3 格式；覆盖率 ≥85%；提交后重启常驻服务。

## Task 6: 文档修正 A4 + deep.py 深度分析（deep_analyze）

**目标**：先改文档（A4）；deep.py 建立常量/DeepResult/deep_analyze（prompt 用 docs/05 §5.1）。

**涉及文件**：
- 修改：`docs/04-v3设计文档.md`（§6.2 deep_filter 签名、§3 集成点注记）
- 新建：`daily_picks/deep.py`（常量 + DeepResult + deep_analyze）
- 测试：`tests/test_m11_deep.py`（T-DEEP-01/02/03/04）

**接口**：
- Consumes：`LLMClient.chat`（Task 4）、`models.Article`
- Produces：
  - 常量：`DEEP_SCORE_MIN: float = 0.0`、`KEYWORDS_MIN: int = 3`、`KEYWORDS_MAX: int = 5`、`DEEP_TIMEOUT_S: float = 30.0`、`DEEP_MIN_COUNT: int = 5`、`BANNED_REASONS`、`DEEP_SYSTEM_PROMPT`
  - `@dataclass class DeepResult: article_id: int; deep_score: int; keywords: list[str]; reason: str; ok: bool`
  - `async def deep_analyze(article: Article, llm: LLMClient, weights: dict[str, float]) -> DeepResult`

- [ ] **Step 1: 修改文档（AGENTS.md 第 4 条）**

`docs/04-v3设计文档.md` §6.2：

```diff
-async def deep_filter(candidates: list[ScoredArticle], llm: LLMClient,
-                      threshold: int) -> tuple[list[ScoredArticle], list[DeepResult]]:
+async def deep_filter(candidates: list[ScoredArticle], llm: LLMClient,
+                      threshold: int,
+                      weights: dict[str, float] | None = None) -> tuple[list[ScoredArticle], list[DeepResult]]:
```

§6.2 末尾追加修订说明行：

```
- 修订说明（2026-08-31）：deep_filter 增可选参数 weights（透传 deep_analyze 的用户兴趣；
  3 参调用保持合法）；降阈值判定改用模块常量 DEEP_MIN_COUNT = 5（对齐 profile.top_n 默认值，
  签名无 top_n 参数）；集成点在 cli.py run_once（§3 架构图注记的 pipeline.py 不存在，
  docs/05 §2.2 已允许 run_once 方案）；v3 路径中 deep 过滤后的候选直接进入 LLM 精排
  （select_candidates 仅在非 deep 路径执行，实现口径以 docs/05 §2.2 代码为准）。
```

- [ ] **Step 2: 写失败测试**

新建 `tests/test_m11_deep.py`：

```python
"""M11 用例：深度评分/过滤/关键词（测试文档 docs/06 §2；LLM 全部 mock 不走网络）。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from daily_picks.deep import DeepResult, deep_analyze, deep_filter, format_keywords
from daily_picks.models import Article, ScoredArticle


def make_article(source: str = "rss", title: str = "AI 编程工具实战",
                 summary: str | None = "用 AI 写代码的十个技巧") -> Article:
    return Article(source=source, source_key="k1", title=title,
                   url="https://example.com/k1", summary=summary)


def make_scored(article_id: int = 1, title: str = "AI 编程工具实战",
                score: float = 10.0) -> ScoredArticle:
    return ScoredArticle(article=make_article(title=title), score=score, article_id=article_id)


class FakeSeqLLM:
    """按调用顺序返回预置 JSON 文本；记录 user 消息与并发度（T-DEEP-08 用）。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.users: list[str] = []

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            self.users.append(user)
            return self.replies[min(self.calls, len(self.replies) - 1)]
        finally:
            self.calls += 1
            self.active -= 1


VALID_JSON = ('{"deep_score": 78, "keywords": ["A", "B", "C"],'
              ' "reason": "文中用具体数据对比了三种方案，缓存实测尤其有参考价值。"}')


class TestDeepAnalyze:
    # T-DEEP-01 深度评分有效
    async def test_valid_result(self):
        llm = FakeSeqLLM([VALID_JSON])
        result = await deep_analyze(make_article(), llm, {"AI": 2.0})
        assert result.deep_score == 78
        assert result.ok is True
        assert result.keywords == ["A", "B", "C"]
        assert "AI" in llm.users[0] and result.reason  # 兴趣关键词注入 user 消息 + 输出完整

    # T-DEEP-02 评分越界回退
    async def test_score_out_of_range(self):
        llm = FakeSeqLLM(['{"deep_score": 150, "keywords": ["A", "B", "C"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    async def test_score_non_numeric(self):
        llm = FakeSeqLLM(['{"deep_score": "78", "keywords": ["A", "B", "C"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    # T-DEEP-03 关键词不足
    async def test_keywords_too_few_keeps_original(self):
        llm = FakeSeqLLM(['{"deep_score": 60, "keywords": ["A"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False
        assert result.keywords == ["A"]  # 保留原值

    # T-DEEP-04 理由含禁用词
    async def test_banned_reason_word(self):
        llm = FakeSeqLLM(['{"deep_score": 80, "keywords": ["A", "B", "C"], "reason": "本文深入浅出"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    async def test_empty_reason_falls_back(self):
        llm = FakeSeqLLM(['{"deep_score": 80, "keywords": ["A", "B", "C"], "reason": ""}'])
        result = await deep_analyze(make_article(title="Rust 异步实战"), llm, {})
        assert result.ok is True  # 回退文案，非失败
        assert "Rust 异步实战" in result.reason

    async def test_invalid_json_returns_not_ok(self):
        llm = FakeSeqLLM(["不是JSON"])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_m11_deep.py::TestDeepAnalyze -v
```
预期：FAIL（`ModuleNotFoundError: No module named 'daily_picks.deep'`）。

- [ ] **Step 4: 实现**

新建 `daily_picks/deep.py`（deep_filter 在 Task 7 追加）：

```python
"""深度评分 + 关键词提取（docs/04 §6.2 / docs/05 §2.1，M11 核心）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from daily_picks.llm import LLMClient, LLMError
from daily_picks.models import Article

logger = logging.getLogger("daily_picks.deep")

DEEP_SCORE_MIN: float = 0.0    # LLM 深度评分（0-100）输出下限校验
KEYWORDS_MIN: int = 3
KEYWORDS_MAX: int = 5
DEEP_TIMEOUT_S: float = 30.0   # 单篇分析超时秒数（docs/05 §2.1）
DEEP_MIN_COUNT: int = 5        # 过滤后不足该数触发降阈值重试（对齐 profile.top_n 默认值，docs/04 §6.2 修订）

# 推荐理由禁用词（docs/04 §7，写死在代码侧校验）
BANNED_REASONS = ("深入浅出", "受益匪浅", "干货满满", "值得一读", "不容错过")

# 深度分析 system prompt（docs/05 §5.1 模板，原样使用）
DEEP_SYSTEM_PROMPT = (
    "你是资深内容编辑，评估文章思考深度。评分标准：观点原创性(40%)、论证扎实度(30%)、"
    "信息密度(20%)、对读者的启发价值(10%)。推荐理由必须引用文章的具体观点/数据/场景，"
    "禁止使用任何套话。"
)


@dataclass
class DeepResult:
    article_id: int
    deep_score: int             # 0-100
    keywords: list[str]         # 3-5 个
    reason: str                 # 具体推荐理由（引用文章细节，禁止空泛）
    ok: bool                    # LLM 输出有效？


async def deep_analyze(article: Article, llm: LLMClient,
                       weights: dict[str, float]) -> DeepResult:
    """LLM 深度分析：输入 title+summary+url+用户兴趣，输出 {deep_score, keywords, reason}。

    解析失败/输出非法 → ok=False（不抛异常，fail-open 依据，docs/05 §2.1）。
    article_id 由调用方（deep_filter）回填——本函数签名无 id 参数。
    """
    keywords_text = "、".join(sorted(weights, key=weights.get, reverse=True)) or "（暂无）"
    user = (
        f"标题：{article.title}\n摘要：{article.summary or ''}\nURL：{article.url}\n"
        f"用户兴趣关键词：{keywords_text}\n"
        '输出 JSON：{"deep_score": <0-100整数>, "keywords": [3-5个名词短语], '
        '"reason": "2-3句，含具体引用，禁止\'深入浅出/受益匪浅/干货满满/值得一读/不容错过\'"}'
    )
    try:
        text = await llm.chat(DEEP_SYSTEM_PROMPT, user, json_mode=True)
        data = json.loads(text or "{}")
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        logger.warning("deep_analyze 失败（fail-open）: %s", e)
        return DeepResult(article_id=0, deep_score=0, keywords=[], reason="", ok=False)
    if not isinstance(data, dict):
        return DeepResult(article_id=0, deep_score=0, keywords=[], reason="", ok=False)

    ok = True
    score_raw = data.get("deep_score")
    if not (isinstance(score_raw, int) and not isinstance(score_raw, bool)
            and DEEP_SCORE_MIN <= score_raw <= 100):
        ok = False
    keywords_raw = data.get("keywords")
    if not isinstance(keywords_raw, list) or len(keywords_raw) < KEYWORDS_MIN:
        ok = False
    keywords = [str(k) for k in keywords_raw[:KEYWORDS_MAX]] if isinstance(keywords_raw, list) else []

    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = f"文章围绕 {article.title} 展开"   # docs/05 §2.1 回退文案
    elif any(word in reason for word in BANNED_REASONS):
        ok = False

    return DeepResult(article_id=0, deep_score=score_raw if isinstance(score_raw, int) else 0,
                      keywords=keywords, reason=reason, ok=ok)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_m11_deep.py::TestDeepAnalyze -v
uv run ruff check daily_picks/deep.py tests/test_m11_deep.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 6: 提交**

```bash
git add docs/04-v3设计文档.md daily_picks/deep.py tests/test_m11_deep.py
git commit -m "M11: docs - 修订 deep_filter 权重参数与集成点注记（先改文档，AGENTS.md 第4条）"
git commit -m "M11: deep - deep_analyze 深度评分与输出校验"
```

**验收命令**（本任务）：`uv run pytest tests/test_m11_deep.py -q` 全绿。

---

## Task 7: deep_filter 并发过滤

**目标**：批量深度分析（并发 ≤3、30s 超时、fail-open、不足降阈值重试一次）。

**涉及文件**：
- 修改：`daily_picks/deep.py`（deep_filter + `import asyncio`、`from daily_picks.models import ScoredArticle`）
- 测试：`tests/test_m11_deep.py`（T-DEEP-05/06/07/08）

**接口**：
- Consumes：`deep_analyze`（Task 6）、`models.ScoredArticle`
- Produces：
  - `async def deep_filter(candidates: list[ScoredArticle], llm: LLMClient, threshold: int, weights: dict[str, float] | None = None) -> tuple[list[ScoredArticle], list[DeepResult]]`
    - 语义：并发 ≤3（信号量）；单篇 `asyncio.wait_for(..., timeout=DEEP_TIMEOUT_S)`；保留 `deep_score >= threshold` 或 `ok=False`（fail-open）的候选；过滤后 `< DEEP_MIN_COUNT` 且 `threshold-10 >= 0` 时按**已得结果**降阈值重过滤一次（不重复调用 LLM）；返回 (过滤后候选, 全量 DeepResult 列表，顺序与输入一致)；DeepResult.article_id 回填 `sa.article_id`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m11_deep.py`：

```python
import pytest

from daily_picks import deep as deep_mod


def _replies(scores: list[int], extra: str = "") -> list[str]:
    out = []
    for s in scores:
        out.append('{"deep_score": %d, "keywords": ["A", "B", "C"], "reason": "文中引用具体数据论证观点%s"}' % (s, extra))
    return out


class TestDeepFilter:
    # T-DEEP-05 批量过滤保高分
    async def test_keeps_high_scores(self):
        llm = FakeSeqLLM(_replies([78, 55, 30]))
        candidates = [make_scored(article_id=i, score=50.0 - i) for i in (1, 2, 3)]
        filtered, results = await deep_filter(candidates, llm, threshold=60)
        assert [sa.article_id for sa in filtered] == [1]
        assert len(results) == 3          # 全量 DeepResult
        assert [r.article_id for r in results] == [1, 2, 3]  # 回填 article_id

    # T-DEEP-06 fail-open 保留失败篇（超时 mock）
    async def test_fail_open_keeps_failed(self, monkeypatch):
        monkeypatch.setattr(deep_mod, "DEEP_TIMEOUT_S", 0.01)  # 缩短超时避免测试拖慢（freezegun 冻结会挂死，勿加 frozen_now）

        class SlowLLM:
            async def chat(self, system, user, json_mode=True):
                await asyncio.sleep(0.1)  # 超过 0.01s → wait_for 超时
                return VALID_JSON

        filtered, results = await deep_filter([make_scored(article_id=1)], SlowLLM(), threshold=60)
        assert [sa.article_id for sa in filtered] == [1]  # 超时篇保留
        assert results[0].ok is False

    # T-DEEP-07 过滤后不足降阈值重试
    async def test_low_yield_lowers_threshold_once(self):
        llm = FakeSeqLLM(_replies([55, 52, 50]))
        candidates = [make_scored(article_id=i) for i in (1, 2, 3)]
        filtered, _ = await deep_filter(candidates, llm, threshold=60)
        assert [sa.article_id for sa in filtered] == [1, 2, 3]  # 阈值降为 50 后全部保留
        assert llm.calls == 3  # 不重复调用 LLM（复用首轮结果）

    # T-DEEP-08 并发上限 ≤3
    async def test_concurrency_capped_at_three(self):
        llm = FakeSeqLLM(_replies([70] * 5))
        candidates = [make_scored(article_id=i) for i in range(1, 6)]
        await deep_filter(candidates, llm, threshold=60)
        assert llm.max_active <= 3

    async def test_empty_candidates(self):
        llm = FakeSeqLLM([])
        filtered, results = await deep_filter([], llm, threshold=60)
        assert filtered == [] and results == []

    async def test_weights_passed_to_analyze(self, monkeypatch):
        seen: list[dict[str, float]] = []
        original = deep_mod.deep_analyze

        async def spy(article, llm, weights):
            seen.append(weights)
            return await original(article, llm, weights)

        monkeypatch.setattr(deep_mod, "deep_analyze", spy)
        await deep_filter([make_scored(article_id=1)], FakeSeqLLM([VALID_JSON]),
                          threshold=60, weights={"AI": 2.0})
        assert seen == [{"AI": 2.0}]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m11_deep.py::TestDeepFilter -v
```
预期：FAIL（`ImportError: cannot import name 'deep_filter'`）。

- [ ] **Step 3: 实现**

`daily_picks/deep.py`：头部 `import asyncio`；导入行改为 `from daily_picks.models import Article, ScoredArticle`；`deep_analyze` 之后追加：

```python
async def deep_filter(candidates: list[ScoredArticle], llm: LLMClient,
                      threshold: int,
                      weights: dict[str, float] | None = None) -> tuple[list[ScoredArticle], list[DeepResult]]:
    """批量 deep_analyze（并发 ≤3，单篇 DEEP_TIMEOUT_S 超时），保留 deep_score >= threshold 的候选。

    ok=False（LLM 失败/超时/输出非法）的文章**保留**（fail-open，不因 deep 故障丢候选）；
    过滤后不足 DEEP_MIN_COUNT 条且 threshold-10 >= 0 时，按首轮结果降阈值重过滤一次
    （不重复调用 LLM，docs/04 §10 降级表）。返回 (过滤后候选, 全量 DeepResult 列表)。
    """
    if not candidates:
        return [], []
    weights = weights or {}
    sem = asyncio.Semaphore(3)

    async def _analyze_one(sa: ScoredArticle) -> DeepResult:
        async with sem:
            try:
                result = await asyncio.wait_for(
                    deep_analyze(sa.article, llm, weights), timeout=DEEP_TIMEOUT_S)
            except TimeoutError:
                logger.warning("deep 分析超时 article_id=%s（fail-open 保留）", sa.article_id)
                return DeepResult(article_id=sa.article_id, deep_score=0,
                                  keywords=[], reason="", ok=False)
            result.article_id = sa.article_id if sa.article_id is not None else 0
            return result

    results = await asyncio.gather(*(_analyze_one(sa) for sa in candidates))

    def _keep(r: DeepResult, th: int) -> bool:
        return (not r.ok) or r.deep_score >= th

    filtered = [sa for sa, r in zip(candidates, results) if _keep(r, threshold)]
    if len(filtered) < DEEP_MIN_COUNT and threshold - 10 >= 0:
        logger.info("deep 过滤后不足 %d 条，降阈值 %d 重试一次", DEEP_MIN_COUNT, threshold - 10)
        filtered = [sa for sa, r in zip(candidates, results) if _keep(r, threshold - 10)]
    return filtered, results
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m11_deep.py -q
uv run ruff check daily_picks/deep.py tests/test_m11_deep.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/deep.py tests/test_m11_deep.py
git commit -m "M11: deep - deep_filter 并发过滤与降阈值重试"
```

**验收命令**（本任务）：`uv run pytest tests/test_m11_deep.py -q` 全绿。

---

## Task 8: format_keywords + digest_v3 推送模板

**目标**：format_keywords 与 v3 模板 build_digest_v3 / source_display_name。

**涉及文件**：
- 修改：`daily_picks/deep.py`（format_keywords）
- 新建：`daily_picks/digest_v3.py`
- 测试：`tests/test_m11_deep.py`（T-DEEP-09/10）、`tests/test_digest_v3.py`（T-DG3-01..04）

**接口**：
- Consumes：`models.Pick/Article`、`deep.DeepResult/format_keywords`、`digest.truncate/SUMMARY_MAX_CHARS`
- Produces：
  - `def format_keywords(keywords: list[str]) -> str`  # "k1、k2、k3" 顿号拼接，超 5 截断
  - `def build_digest_v3(items: list[Pick], articles: dict[int, Article], deep_map: dict[int, DeepResult]) -> str`
  - `def source_display_name(source_key: str) -> str`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m11_deep.py`：

```python
class TestFormatKeywords:
    # T-DEEP-09 format_keywords
    def test_join_and_truncate(self):
        assert format_keywords(["A", "B", "C", "D", "E", "F"]) == "A、B、C、D、E"
        assert format_keywords(["A", "B", "C"]) == "A、B、C"
        assert format_keywords([]) == ""
    # T-DEEP-10（模板兜底摘要）渲染断言在 tests/test_digest_v3.py::TestBuildDigestV3::test_missing_deep_falls_back_to_summary
```

新建 `tests/test_digest_v3.py`：

```python
"""v3 推送模板用例（测试文档 docs/06 §3 T-DG3-01~04）。"""

from __future__ import annotations

from daily_picks.deep import DeepResult
from daily_picks.digest_v3 import build_digest_v3, source_display_name
from daily_picks.models import Article, Pick


def make_article(title: str = "AI 编程工具实战", author: str | None = "作者甲",
                 summary: str | None = "用 AI 写代码的十个技巧，从零到一") -> Article:
    return Article(source="hnews", source_key="k1", title=title,
                   url="https://example.com/1", author=author, summary=summary)


def make_deep(article_id: int = 1, score: int = 78) -> DeepResult:
    return DeepResult(article_id=article_id, deep_score=score,
                      keywords=["AI", "大模型", "工具链"],
                      reason="文章用具体数据对比了三种方案的落地成本，缓存实测尤其有参考价值。",
                      ok=True)


class TestBuildDigestV3:
    def _items(self, n: int = 2) -> list[Pick]:
        return [Pick(article_id=i, rank=i, reason=f"理由{i}") for i in range(1, n + 1)]

    def _articles(self, n: int = 2) -> dict[int, Article]:
        return {i: make_article(title=f"深度文章{i}") for i in range(1, n + 1)}

    # T-DG3-01 完整条目格式
    def test_full_entry_format(self):
        text = build_digest_v3(self._items(2), self._articles(2),
                               {1: make_deep(1), 2: make_deep(2)})
        assert "【Hacker News】深度文章1" in text
        assert "关键词：AI、大模型、工具链" in text
        assert "推荐理由：文章用具体数据" in text
        assert "链接：https://example.com/1" in text
        assert "作者：作者甲" in text

    # T-DG3-02 条数头部
    def test_header_count(self):
        text = build_digest_v3(self._items(2), self._articles(2),
                               {1: make_deep(1), 2: make_deep(2)})
        assert text.startswith("📚 今日深度精选（2条）")

    # T-DG3-03 作者行
    def test_author_line_omitted_when_empty(self):
        articles = {1: make_article(author=None)}
        text = build_digest_v3(self._items(1), articles, {1: make_deep(1)})
        assert "作者：" not in text
        assert "链接：" in text

    # T-DEEP-10 模板兜底摘要（deep_map 缺该 article_id）
    def test_missing_deep_falls_back_to_summary(self):
        text = build_digest_v3(self._items(1), self._articles(1), {})
        assert "摘要：" in text
        assert "关键词：" not in text

    def test_fail_open_entry_uses_summary(self):
        bad = DeepResult(article_id=1, deep_score=0, keywords=[], reason="", ok=False)
        text = build_digest_v3(self._items(1), self._articles(1), {1: bad})
        assert "摘要：" in text

    def test_empty_items_prompt(self):
        text = build_digest_v3([], {}, {})
        assert "今日无精选内容" in text


class TestSourceDisplayName:
    # T-DG3-04 source_display_name
    def test_builtin_names(self):
        assert source_display_name("hnews") == "Hacker News"
        assert source_display_name("infoq") == "InfoQ"
        assert source_display_name("juejin") == "掘金"
        assert source_display_name("bilibili") == "B站"
        assert source_display_name("zhihu") == "知乎热榜"

    def test_rss_custom_name(self):
        assert source_display_name("rss:机器之心") == "机器之心"
        assert source_display_name("rss") == "RSS"

    def test_unknown_key_falls_back_to_key(self):
        assert source_display_name("unknown") == "unknown"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_digest_v3.py tests/test_m11_deep.py::TestFormatKeywords -v
```
预期：FAIL（`ModuleNotFoundError: No module named 'daily_picks.digest_v3'`）。

- [ ] **Step 3: 实现**

1. `daily_picks/deep.py` 末尾追加：

```python
def format_keywords(keywords: list[str]) -> str:
    """'k1、k2、k3' 顿号拼接，超 5 个截断（docs/04 §6.2）。"""
    return "、".join(keywords[:KEYWORDS_MAX])
```

2. 新建 `daily_picks/digest_v3.py`：

```python
"""v3 深度精选推送模板（docs/04 §7 / docs/05 §2.3）。"""

from __future__ import annotations

from daily_picks.deep import DeepResult, format_keywords
from daily_picks.digest import SUMMARY_MAX_CHARS, truncate
from daily_picks.models import Article, Pick

# 内置源显示名（docs/04 §6.5）
SOURCE_DISPLAY_NAMES = {
    "hnews": "Hacker News",
    "infoq": "InfoQ",
    "juejin": "掘金",
    "bilibili": "B站",
    "zhihu": "知乎热榜",
    "rss": "RSS",
}

DIGEST_SEPARATOR = "─" * 14


def source_display_name(source_key: str) -> str:
    """内置源中文名映射；'rss:<名称>' 自定义源 → 名称（key 内嵌名，无需查 registry）；未知 → 原 key。"""
    if source_key in SOURCE_DISPLAY_NAMES:
        return SOURCE_DISPLAY_NAMES[source_key]
    if source_key.startswith("rss:"):
        return source_key[len("rss:"):]
    return source_key


def build_digest_v3(items: list[Pick], articles: dict[int, Article],
                    deep_map: dict[int, DeepResult]) -> str:
    """v3 模板（docs/04 §7）：
    📚 今日深度精选（n条）／──────────────／每条目：【来源】标题/关键词/推荐理由/链接/作者。
    deep_map 缺该条目（fail-open 保留）→ 用原摘要兜底行 `摘要：…`（docs/05 §2.3）。
    空列表返回"今日无精选内容。"提示文案。
    """
    lines = [f"📚 今日深度精选（{len(items)}条）"]
    for pick in sorted(items, key=lambda p: p.rank):
        article = articles.get(pick.article_id)
        if article is None:
            continue
        lines.append(DIGEST_SEPARATOR)
        lines.append(f"【{source_display_name(article.source)}】{article.title}")
        deep = deep_map.get(pick.article_id)
        if deep is not None and deep.ok:
            lines.append(f"关键词：{format_keywords(deep.keywords)}")
            lines.append(f"推荐理由：{deep.reason}")
        else:
            summary = truncate(article.summary, SUMMARY_MAX_CHARS)
            if summary:
                lines.append(f"摘要：{summary}")
        lines.append(f"链接：{article.url}")
        if article.author:
            lines.append(f"作者：{article.author}")
    if not items:
        lines.append("今日无精选内容。")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_digest_v3.py tests/test_m11_deep.py tests/test_digest.py -q
uv run ruff check daily_picks/deep.py daily_picks/digest_v3.py tests/test_digest_v3.py
```
预期：全 PASS（test_digest.py v2 回归不受影响），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/deep.py daily_picks/digest_v3.py tests/test_m11_deep.py tests/test_digest_v3.py
git commit -m "M11: digest_v3 - v3 推送模板与来源显示名"
```

**验收命令**（本任务）：`uv run pytest tests/test_digest_v3.py tests/test_m11_deep.py -q` 全绿。

---

## Task 9: cli.run_once 集成 deep 阶段与 v3 模板

**目标**：run_once 插入 deep 阶段（profile.enabled 且有 key）、top_n 切换、推送模板切换。

**涉及文件**：
- 修改：`daily_picks/cli.py`（导入 + run_once 步骤 7/8 重构）
- 测试：`tests/test_m11_deep.py`（v3 e2e 集成，对齐 tests/test_e2e.py 的 respx 写法）

**接口**：
- Consumes：`deep_filter/DeepResult`（Task 7）、`build_digest_v3`（Task 8）、`select_candidates/rank_and_pick`（现有）
- Produces：run_once 行为变更（对外签名不变 `async def run_once(cfg: RootConfig, dry_run: bool = False) -> int`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m11_deep.py`：

```python
# ---- v3 run_once 集成（对齐 tests/test_e2e.py 的 respx mock 写法；cli.py 不在覆盖率范围，验证行为）----

import json as json_mod
from pathlib import Path

import httpx

from daily_picks.cli import run_once

RSS_URL = "https://sspai.com/feed"
RSS_URL2 = "https://www.ruanyifeng.com/blog/atom.xml"
BILI_URL = "https://api.bilibili.com/x/web-interface/popular"
ZHIHU_URL = "https://api.zhihu.com/topstory/hot-lists/total"
JUEJIN_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
HN_URL = "https://hn.algolia.com/api/v1/search"
INFOQ_URL = "https://www.infoq.cn/feed"
LLM_URL = "https://api.deepseek.com/chat/completions"

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def mock_sources(mock_http) -> None:
    mock_http.get(RSS_URL).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(RSS_URL2).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(BILI_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("bilibili_sample.json"))))
    mock_http.get(ZHIHU_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("zhihu_sample.json"))))
    mock_http.post(JUEJIN_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("juejin_sample.json"))))
    mock_http.get(HN_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("hnews_sample.json"))))
    mock_http.get(INFOQ_URL).mock(return_value=httpx.Response(200, content=load("infoq_sample.xml")))


def llm_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


DEEP_JSON = ('{"deep_score": 70, "keywords": ["AI", "大模型", "深度思考"],'
             ' "reason": "文章用具体数据对比了三种方案的落地成本，缓存实测尤其有参考价值。"}')
RANK_JSON = ('{"picks": [{"article_id": 1, "rank": 1, "reason": "AI主题深度"},'
             ' {"article_id": 2, "rank": 2, "reason": "工具链实测"},'
             ' {"article_id": 3, "rank": 3, "reason": "架构演进"}]}')


class TestRunOnceV3:
    async def test_v3_deep_path_outputs_v3_digest(self, sample_config, tmp_path, mock_http,
                                                  frozen_now, monkeypatch):
        cfg = sample_config
        cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
        cfg.profile.enabled = True
        cfg.profile.top_n = 3
        cfg.profile.deep_threshold = 60
        cfg.profile.deep_candidates = 40
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        mock_sources(mock_http)
        # 8 篇新文章 → 8 次 deep chat + 1 次 rank chat（respx 按序出队）
        mock_http.post(LLM_URL).mock(side_effect=[llm_reply(DEEP_JSON)] * 8 + [llm_reply(RANK_JSON)])

        assert await run_once(cfg, dry_run=True) == 0
        text = Path(cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert text.startswith("📚 今日深度精选（3条）")
        assert "关键词：AI、大模型、深度思考" in text
        assert "推荐理由：文章用具体数据" in text
        assert text.count("关键词：") == 3

    async def test_v3_no_key_skips_deep(self, sample_config, tmp_path, mock_http,
                                        frozen_now, monkeypatch):
        cfg = sample_config
        cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
        cfg.profile.enabled = True
        cfg.profile.top_n = 3
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # 确保无 key（本地 .env 可能注入真实 key）
        mock_sources(mock_http)
        # 无 DEEPSEEK_API_KEY：deep 跳过，等同 v2 降级；LLM 端点不应被调用
        assert await run_once(cfg, dry_run=True) == 0
        text = Path(cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert "📚 今日深度精选" in text  # 模板仍是 v3（profile.enabled）
        assert "摘要：" in text or "今日无精选内容" in text  # deep 缺位 → 摘要兜底
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m11_deep.py::TestRunOnceV3 -v
```
预期：FAIL（输出仍是 `📌 今日精选`，无 v3 头部）。

- [ ] **Step 3: 实现**

`daily_picks/cli.py`：

1. 头部导入追加：

```python
from daily_picks.deep import DeepResult, deep_filter
from daily_picks.digest_v3 import build_digest_v3
```

2. 把 run_once 中现有这段（当前在"步骤 7"注释附近）：

```python
    candidates = select_candidates(scored, cfg.digest.max_candidates, cfg.digest.min_score)
    llm_client = LLMClient(cfg.llm)
    if _llm_key_missing(cfg):
        print("未配置 DEEPSEEK_API_KEY，使用规则分降级")
        logger.warning("未配置 DEEPSEEK_API_KEY，使用规则分降级")
    picks, fallback_used = await rank_and_pick(
        candidates, llm_client, weights, cfg.digest.top_n, cfg.llm.max_input_chars
    )
```

替换为：

```python
    llm_client = LLMClient(cfg.llm)
    key_missing = _llm_key_missing(cfg)
    if key_missing:
        print("未配置 DEEPSEEK_API_KEY，使用规则分降级")
        logger.warning("未配置 DEEPSEEK_API_KEY，使用规则分降级")

    # 步骤 7.5（v3）：deep 阶段（docs/04 §3.1）。profile.enabled 且有 key 时，对规则分
    # Top-N（deep_candidates）执行深度评分并过滤低于 deep_threshold 的候选；
    # 无 key 时跳过 deep（等同 v2 选材，docs/04 §10 降级表）。
    top_n = cfg.profile.top_n if cfg.profile.enabled else cfg.digest.top_n
    deep_map_full: dict[int, DeepResult] = {}
    if cfg.profile.enabled and not key_missing:
        top_for_deep = sorted(scored, key=lambda sa: sa.score, reverse=True)[: cfg.profile.deep_candidates]
        candidates, deep_results = await deep_filter(
            top_for_deep, llm_client, cfg.profile.deep_threshold, weights)
        deep_map_full = {d.article_id: d for d in deep_results}
    else:
        candidates = select_candidates(scored, cfg.digest.max_candidates, cfg.digest.min_score)

    picks, fallback_used = await rank_and_pick(
        candidates, llm_client, weights, top_n, cfg.llm.max_input_chars
    )
```

3. 把 run_once 中"步骤 8"整段（`digest_items: list[tuple[int, Article, str]] = []` 至 `digest_text = build_digest_text(digest_items, run_date)`）替换为：

```python
    # 步骤 8：生成微信 markdown 简报（docs/04 §7；profile.enabled 时走 v3 模板）
    if cfg.profile.enabled:
        articles_by_id: dict[int, Article] = {}
        for pick in picks:
            picked = by_id.get(pick.article_id)
            if picked is None:
                logger.warning("精选条目无对应文章 article_id=%s，跳过", pick.article_id)
                continue
            article = picked.article
            if pick.article_id in url_map:
                article = dataclasses.replace(article, url=url_map[pick.article_id])
            articles_by_id[pick.article_id] = article
        digest_text = build_digest_v3(picks, articles_by_id, deep_map_full)
    else:
        digest_items: list[tuple[int, Article, str]] = []
        for pick in picks:
            picked = by_id.get(pick.article_id)
            if picked is None:
                logger.warning("精选条目无对应文章 article_id=%s，跳过", pick.article_id)
                continue
            article = picked.article
            if pick.article_id in url_map:
                article = dataclasses.replace(article, url=url_map[pick.article_id])
            digest_items.append((pick.rank, article, pick.reason))
        digest_text = build_digest_text(digest_items, run_date)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m11_deep.py tests/test_e2e.py tests/test_cli.py -q
uv run ruff check daily_picks/cli.py tests/test_m11_deep.py
```
预期：全 PASS（**T-E2E 全系列 v2 回归必须绿**），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/cli.py tests/test_m11_deep.py
git commit -m "M11: cli - run_once deep 阶段与 v3 模板切换"
```

**验收命令**（本任务，M11 里程碑 gate）：

```bash
cd ~/daily-picks
uv run pytest -q
uv run pytest --cov=daily_picks --cov-report=term-missing | grep TOTAL   # ≥85%
uv run ruff check daily_picks/ tests/
uv run daily-picks run --dry-run     # 有 DEEPSEEK_API_KEY 时输出 📚 今日深度精选 v3 格式；
                                     # 无 key 时验证 v2 回归路径不崩
systemctl --user restart daily-picks.service
```

---

# 里程碑 M12：反馈闭环（Hermes 通道）

> 里程碑验收（design §9 M12）：`pytest` 全绿；实测 `feedback "多推点AI硬件"` → feedback_text + tag_weights 有记录；覆盖率 ≥85%；提交后重启常驻服务。

## Task 10: feedback_channels.py 通道抽象

**目标**：FeedbackChannel ABC + RawFeedback + HermesChannel（docs/04 §8）。

**涉及文件**：
- 新建：`daily_picks/feedback_channels.py`
- 测试：`tests/test_m12_feedback.py`（补充用例）

**接口**：
- Produces：
  - `@dataclass class RawFeedback: text: str; article_id: int | None = None; channel: str = "hermes"`
  - `class FeedbackChannel(ABC)`: `name: str = ""`；`async def receive(self) -> list[RawFeedback]`（abstract）；`async def acknowledge(self, fb_id: str) -> None`（abstract）
  - `class HermesChannel(FeedbackChannel)`: `name = "hermes"`；receive 返回 `[]`、acknowledge 为 no-op（v3 仅 CLI 直写库，docs/05 §3.1）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_m12_feedback.py`（M12 全部用例文件，本任务先写通道部分）：

```python
"""M12 用例：反馈通道/意图解析/演化落库（测试文档 docs/06 §4；LLM 全部 mock）。"""

from __future__ import annotations

import pytest

from daily_picks.feedback_channels import FeedbackChannel, HermesChannel, RawFeedback


class TestFeedbackChannel:
    def test_raw_feedback_defaults(self):
        fb = RawFeedback(text="多推点AI硬件")
        assert fb.text == "多推点AI硬件"
        assert fb.article_id is None
        assert fb.channel == "hermes"

    def test_abstract_class_not_instantiable(self):
        with pytest.raises(TypeError):
            FeedbackChannel()  # 含抽象方法，禁止实例化

    async def test_hermes_receive_returns_empty(self):
        channel = HermesChannel()
        assert channel.name == "hermes"
        assert await channel.receive() == []

    async def test_hermes_acknowledge_noop(self):
        await HermesChannel().acknowledge("fb-1")  # 不抛错即通过
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m12_feedback.py::TestFeedbackChannel -v
```
预期：FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现**

新建 `daily_picks/feedback_channels.py`：

```python
"""反馈通道抽象（docs/04 §8）：Hermes 为 CLI 直写占位，后期企微回调复用同一演化链路。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RawFeedback:
    text: str
    article_id: int | None = None
    channel: str = "hermes"


class FeedbackChannel(ABC):
    name: str = ""

    @abstractmethod
    async def receive(self) -> list[RawFeedback]:
        """拉取未处理反馈。"""

    @abstractmethod
    async def acknowledge(self, fb_id: str) -> None:
        """标记已处理。"""


class HermesChannel(FeedbackChannel):
    """Hermes 通道：v3 仅通过 CLI 直写 feedback_text，零部署（docs/05 §3.1）。"""

    name = "hermes"

    async def receive(self) -> list[RawFeedback]:
        return []

    async def acknowledge(self, fb_id: str) -> None:
        return None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m12_feedback.py::TestFeedbackChannel -v
uv run ruff check daily_picks/feedback_channels.py tests/test_m12_feedback.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/feedback_channels.py tests/test_m12_feedback.py
git commit -m "M12: feedback_channels - FeedbackChannel 抽象与 Hermes 通道"
```

**验收命令**（本任务）：`uv run pytest tests/test_m12_feedback.py -q` 全绿。

---

## Task 11: 文档修正 A5 + parse_feedback 意图解析

**目标**：先改文档（A5）；feedback.py 新增 ParsedFeedback/parse_feedback/_heuristic_feedback。

**涉及文件**：
- 修改：`docs/04-v3设计文档.md`（§6.3）、`docs/05-v3开发文档.md`（§3.2）
- 修改：`daily_picks/feedback.py`（追加 v3 解析部分；v1 apply_feedback 本任务暂不动）
- 测试：`tests/test_m12_feedback.py`（T-FB-01/02/03/04/12）

**接口**：
- Consumes：`LLMClient.chat`、`LLMError`
- Produces：
  - `FEEDBACK_INTENTS = ("like", "dislike", "expand", "adjust", "none")`
  - `@dataclass class ParsedFeedback: raw: str; intent: str; article_id: int | None; tags: list[str]; keywords: list[str]; top_n: int | None`（A5 裁决后含 raw）
  - `async def parse_feedback(raw: str, llm: LLMClient) -> ParsedFeedback`
  - `def _heuristic_feedback(raw: str) -> ParsedFeedback`（docs/05 §3.2 兜底规则）

- [ ] **Step 1: 修改文档（AGENTS.md 第 4 条）**

`docs/04-v3设计文档.md` §6.3 ParsedFeedback：

```diff
 @dataclass
 class ParsedFeedback:
+    raw: str                    # 原始反馈文字（落 feedback_text.raw_text）
     intent: str                 # 见 FEEDBACK_INTENTS
     article_id: int | None
     tags: list[str]             # 提取的新标签（intent=expand）
     keywords: list[str]         # 提取的关键词（写 interest_weights）
     top_n: int | None           # intent=adjust 时的新条数
```

§6.3 末尾追加修订说明行：

```
- 修订说明（2026-08-31）：ParsedFeedback 增 raw 字段（feedback_text.raw_text 为 NOT NULL，
  落库需要原文）；apply_feedback 为同名分派——首参为 ParsedFeedback → v3 文字反馈路径；
  否则保持 v1 签名 apply_feedback(storage, article_id, kind, extra_keyword=None) 行为不变
  （R-V3-09 兼容，docs/05 §3.2）。
```

`docs/05-v3开发文档.md` §3.2 末尾追加：

```
- 实现注记（2026-08-31）：feedback.py 中 v1 `apply_feedback` 更名为内部 `_apply_like_dislike`，
  公开 `apply_feedback` 做类型分派（首参 ParsedFeedback → `_apply_text_feedback`）；
  v1 调用点（cli.py、tests/test_feedback.py）无需改动。
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/test_m12_feedback.py`：

```python
import json as json_mod

from daily_picks.feedback import FEEDBACK_INTENTS, parse_feedback
from daily_picks.llm import LLMError


class FakeFeedbackLLM:
    """mock LLMClient.chat：返回预置 JSON 文本，或按配置抛 LLMError。"""

    def __init__(self, reply: str | None = None, raise_error: bool = False):
        self.reply = reply or ""
        self.raise_error = raise_error
        self.calls = 0

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.calls += 1
        if self.raise_error:
            raise LLMError("boom")
        return self.reply


def _llm_json(intent: str, **overrides) -> str:
    data = {"intent": intent, "article_id": None, "tags": [], "keywords": [], "top_n": None}
    data.update(overrides)
    return json_mod.dumps(data, ensure_ascii=False)


class TestParseFeedback:
    # T-FB-01 意图 like
    async def test_intent_like(self):
        llm = FakeFeedbackLLM(_llm_json("like"))
        fb = await parse_feedback("这条不错", llm)
        assert fb.intent == "like"
        assert fb.raw == "这条不错"

    # T-FB-02 意图 expand（含标签提取）
    async def test_intent_expand_with_tags(self):
        llm = FakeFeedbackLLM(_llm_json("expand", tags=["AI硬件"], keywords=["AI硬件"]))
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"
        assert fb.tags == ["AI硬件"]

    # T-FB-03 意图 adjust
    async def test_intent_adjust(self):
        llm = FakeFeedbackLLM(_llm_json("adjust", top_n=3))
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    # T-FB-04 无 LLM 启发式兜底
    async def test_heuristic_dislike_without_llm(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("不要推游戏了", llm)
        assert fb.intent == "dislike"

    async def test_heuristic_adjust(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    async def test_heuristic_expand(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"

    async def test_heuristic_none(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("今天天气不错", llm)
        assert fb.intent == "none"

    # T-FB-12 解析失败不抛
    async def test_invalid_json_falls_back_to_heuristic(self):
        llm = FakeFeedbackLLM("这不是JSON{{{")
        fb = await parse_feedback("随便说点啥", llm)
        assert fb.intent == "none"

    def test_intents_constant(self):
        assert FEEDBACK_INTENTS == ("like", "dislike", "expand", "adjust", "none")
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_m12_feedback.py::TestParseFeedback -v
```
预期：FAIL（`ImportError: cannot import name 'parse_feedback'`）。

- [ ] **Step 4: 实现**

`daily_picks/feedback.py`：头部补 `import json`、`import re`、`from dataclasses import dataclass`、`from daily_picks.config import ConfigError`、`from daily_picks.llm import LLMClient, LLMError`。在 `hit_keywords` 之后追加：

```python
# ---- v3 文字反馈解析（docs/04 §6.3 / docs/05 §3.2）----

FEEDBACK_INTENTS = ("like", "dislike", "expand", "adjust", "none")

# 反馈解析 system prompt（docs/05 §5.2 模板，原样使用）
FEEDBACK_SYSTEM_PROMPT = (
    "你是用户反馈分析师。意图定义：like=对内容满意；dislike=不满；expand=希望扩展某方向"
    "（含新标签）；adjust=调整条数；none=无法归类。"
)


@dataclass
class ParsedFeedback:
    raw: str                    # 原始反馈文字（落 feedback_text.raw_text，docs/04 §6.3 修订）
    intent: str                 # 见 FEEDBACK_INTENTS
    article_id: int | None
    tags: list[str]             # 提取的新标签（intent=expand）
    keywords: list[str]         # 提取的关键词（写 interest_weights）
    top_n: int | None           # intent=adjust 时的新条数


async def parse_feedback(raw: str, llm: LLMClient) -> ParsedFeedback:
    """LLM 解析文字反馈：意图识别 + 标签/关键词提取（docs/05 §5.2 JSON 输出）。

    失败/非法输出 → 启发式兜底（docs/05 §3.2），不抛异常。
    """
    user = (
        "用户反馈：" + raw + "\n"
        '输出 JSON：{"intent": "like|dislike|expand|adjust|none", "article_id": null, '
        '"tags": [提取的新标签，intent=expand 时], "keywords": [从中提取的兴趣关键词], '
        '"top_n": null}（top_n 仅 intent=adjust 时填数字）'
    )
    try:
        text = await llm.chat(FEEDBACK_SYSTEM_PROMPT, user, json_mode=True)
        data = json.loads(text or "{}")
    except (LLMError, ConfigError, json.JSONDecodeError, TypeError) as e:
        # ConfigError（缺 DEEPSEEK_API_KEY）也走启发式——无 LLM 时反馈仍可用（docs/05 §3.2）
        logger.warning("parse_feedback LLM 失败，使用启发式兜底: %s", e)
        return _heuristic_feedback(raw)
    if not isinstance(data, dict) or data.get("intent") not in FEEDBACK_INTENTS:
        return _heuristic_feedback(raw)
    article_id = data.get("article_id")
    top_n = data.get("top_n")
    fb = ParsedFeedback(
        raw=raw,
        intent=data["intent"],
        article_id=article_id if isinstance(article_id, int) else None,
        tags=[str(t) for t in data.get("tags", [])] if isinstance(data.get("tags"), list) else [],
        keywords=[str(k) for k in data.get("keywords", [])] if isinstance(data.get("keywords"), list) else [],
        top_n=top_n if isinstance(top_n, int) else None,
    )
    if fb.intent == "adjust" and not (fb.top_n is not None and 1 <= fb.top_n <= 10):
        fb.top_n = None  # 非法条数按 none 处理（apply 阶段跳过）
    return fb


def _heuristic_feedback(raw: str) -> ParsedFeedback:
    """无 LLM/解析失败兜底（docs/05 §3.2）：
    含"不要/少推/反感" → dislike；含"数字+条" → adjust；含"多推/喜欢/想看" → expand；否则 none。
    """

    def _fb(intent: str, top_n: int | None = None) -> ParsedFeedback:
        return ParsedFeedback(raw=raw, intent=intent, article_id=None,
                              tags=[], keywords=[], top_n=top_n)

    text = raw.strip()
    if not text:
        return _fb("none")
    if any(word in text for word in ("不要", "少推", "反感", "不喜欢")):
        return _fb("dislike")
    m = re.search(r"(\d+)\s*条", text)
    if m and 1 <= int(m.group(1)) <= 10:
        return _fb("adjust", top_n=int(m.group(1)))
    if any(word in text for word in ("多推", "喜欢", "想看")):
        return _fb("expand")
    return _fb("none")
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_m12_feedback.py tests/test_feedback.py -q
uv run ruff check daily_picks/feedback.py tests/test_m12_feedback.py
```
预期：全 PASS（test_feedback.py v1 回归不受影响），ruff 零告警。

- [ ] **Step 6: 提交**

```bash
git add docs/04-v3设计文档.md docs/05-v3开发文档.md daily_picks/feedback.py tests/test_m12_feedback.py
git commit -m "M12: docs - 修订 ParsedFeedback.raw 与 apply_feedback 分派（先改文档，AGENTS.md 第4条）"
git commit -m "M12: feedback - parse_feedback 意图解析与启发式兜底"
```

**验收命令**（本任务）：`uv run pytest tests/test_m12_feedback.py -q` 全绿。

---

## Task 12: apply_feedback v3 分派与演化落库

**目标**：storage.add_feedback_text；feedback.py 同名分派（v1 逻辑迁入 `_apply_like_dislike`）+ `_apply_text_feedback` + `_bump_article_tags`。

**涉及文件**：
- 修改：`daily_picks/storage.py`（add_feedback_text）
- 修改：`daily_picks/feedback.py`（apply_feedback 分派重构）
- 测试：`tests/test_m12_feedback.py`（T-FB-05/06/07/08/09）

**接口**：
- Consumes：`storage.save_profile/load_profile/list_tags/save_tag_weight/bump_keyword_weight/get_articles_by_ids`（Task 2 + 现有）
- Produces：
  - `def add_feedback_text(self, *, raw_text: str, intent: str, article_id: int | None, extracted_tags: list[str], keywords: list[str], channel: str = "hermes") -> int`
  - `def apply_feedback(first, second, third=True, extra_keyword=None)`（分派器；两种调用形式见 A5）
  - `def _apply_like_dislike(storage: Storage, article_id: int, kind: str, extra_keyword: str | None = None) -> dict`（原 v1 实现迁入）
  - `def _apply_text_feedback(fb: ParsedFeedback, storage: Storage, extract_keywords: bool = True) -> None`
  - `def _bump_article_tags(storage: Storage, article_id: int, delta: float) -> list[str]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m12_feedback.py`：

```python
from daily_picks.feedback import ParsedFeedback, apply_feedback
from daily_picks.models import Article


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    ids = storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])
    return ids[0]


def make_fb(intent: str, raw: str = "多推点AI硬件", article_id: int | None = None,
            tags: list[str] | None = None, keywords: list[str] | None = None,
            top_n: int | None = None) -> ParsedFeedback:
    return ParsedFeedback(raw=raw, intent=intent, article_id=article_id,
                          tags=tags or [], keywords=keywords or [], top_n=top_n)


class TestApplyTextFeedback:
    def _profile(self, tmp_db):
        tmp_db.save_profile(["AI大模型"], ["hnews"], 5)

    def _fb_rows(self, tmp_db):
        return tmp_db._conn.execute("SELECT * FROM feedback_text").fetchall()

    # T-FB-05 落库
    def test_feedback_text_recorded(self, tmp_db):
        apply_feedback(make_fb("none", raw="今天天气不错"), tmp_db)
        rows = self._fb_rows(tmp_db)
        assert len(rows) == 1
        assert rows[0]["intent"] == "none"
        assert rows[0]["channel"] == "hermes"
        assert rows[0]["raw_text"] == "今天天气不错"

    # T-FB-06 expand 写标签
    def test_expand_writes_tag_weight(self, tmp_db):
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert dict(tmp_db.list_tags())["AI硬件"] == 1.5

    def test_expand_existing_tag_keeps_weight(self, tmp_db):
        tmp_db.save_tag_weight("AI硬件", 2.0, "click")
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=[]), tmp_db)
        assert dict(tmp_db.list_tags())["AI硬件"] == 2.0  # 已有标签不动（保留演化值）

    # T-FB-07 expand 合并进 profile
    def test_expand_merges_into_profile(self, tmp_db):
        self._profile(tmp_db)
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.load_profile()["tags"] == ["AI大模型", "AI硬件"]

    # T-FB-08 adjust 更新 top_n（tags/sources 不变）
    def test_adjust_updates_top_n_only(self, tmp_db):
        self._profile(tmp_db)
        apply_feedback(make_fb("adjust", top_n=3), tmp_db)
        profile = tmp_db.load_profile()
        assert profile["top_n"] == 3
        assert profile["tags"] == ["AI大模型"]
        assert profile["sources"] == ["hnews"]

    def test_adjust_without_profile_is_noop(self, tmp_db):
        apply_feedback(make_fb("adjust", top_n=3), tmp_db)
        assert tmp_db.load_profile() is None  # 无画像不凭空造

    # T-FB-09 关键词写权重
    def test_keywords_bump_interest_weights(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)  # 预置 1.0
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.get_interest_weights()["AI硬件"] == pytest.approx(1.1)

    def test_keywords_clamped_at_2(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)
        for _ in range(20):
            apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.get_interest_weights()["AI硬件"] == 2.0

    def test_extract_keywords_disabled(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]),
                       tmp_db, extract_keywords=False)
        assert tmp_db.get_interest_weights()["AI硬件"] == 1.0  # 不写关键词

    # like/dislike 文字路径（含 article_id）复用 v1 + tag 联动
    def test_text_like_reuses_v1_and_bumps_tags(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        tmp_db.save_tag_weight("AI", 1.0, "manual")
        aid = seed_article(tmp_db, title="AI 编程工具实战")
        apply_feedback(make_fb("like", raw="这篇不错", article_id=aid), tmp_db)
        assert tmp_db.get_feedback_kinds(aid) == ["like"]
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.1)   # v1 like +0.1
        assert dict(tmp_db.list_tags())["AI"] == pytest.approx(1.1)        # tag +0.1

    def test_text_dislike_missing_article_no_crash(self, tmp_db):
        apply_feedback(make_fb("dislike", raw="这篇不行", article_id=999), tmp_db)  # 不抛
        rows = self._fb_rows(tmp_db)
        assert len(rows) == 1  # 反馈文字仍落库
```

v1 兼容回归补充（分派器必须不破坏现有调用）：

```python
class TestApplyFeedbackDispatch:
    """分派器 v1 路径回归（对齐 tests/test_feedback.py 的既有断言）。"""

    def test_v1_signature_still_works(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        result = apply_feedback(tmp_db, aid, "like")
        assert result["updated"] == ["AI"]
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.1)

    def test_v1_extra_keyword_still_works(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="今天天气不错")
        result = apply_feedback(tmp_db, aid, "like", extra_keyword="开源")
        assert result["updated"] == ["开源"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m12_feedback.py::TestApplyTextFeedback tests/test_m12_feedback.py::TestApplyFeedbackDispatch -v
```
预期：FAIL（`AttributeError: 'Storage' object has no attribute 'add_feedback_text'`）。

- [ ] **Step 3: 实现**

1. `daily_picks/storage.py` `list_sources` 之后新增：

```python
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
```

2. `daily_picks/feedback.py`：

- 现有 `apply_feedback` 函数整体改名为 `_apply_like_dislike`（函数体、docstring 不动，仅 docstring 首行加"（原 v1 实现，2026-08-31 更名）"）。
- 文件末尾追加：

```python
# ---- v3 文字反馈应用（docs/04 §6.3 / docs/05 §3.2；同名分派见下）----


def apply_feedback(first, second, third=True, extra_keyword=None):
    """同名分派（docs/04 §6.3 修订）：
    - v3 文字反馈：apply_feedback(fb: ParsedFeedback, storage, extract_keywords=True)
    - v1 like/dislike：apply_feedback(storage, article_id, kind, extra_keyword=None)
    """
    if isinstance(first, ParsedFeedback):
        return _apply_text_feedback(first, second, extract_keywords=third)
    return _apply_like_dislike(first, second, third, extra_keyword)


def _bump_article_tags(storage: Storage, article_id: int, delta: float) -> list[str]:
    """文章 title+summary 命中的标签（tag_weights 表中的）各 +delta（save_tag_weight 钳制 [0.2,2.0]）。"""
    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        return []
    text = f"{rows[0]['title'] or ''} {rows[0]['summary'] or ''}".lower()
    hits: list[str] = []
    for tag, current in storage.list_tags():
        if tag and tag.lower() in text:
            storage.save_tag_weight(tag, current + delta, "feedback")
            hits.append(tag)
    return hits


def _apply_text_feedback(fb: ParsedFeedback, storage: Storage,
                         extract_keywords: bool = True) -> None:
    """落库 feedback_text + 演化（docs/04 §6.3）：
    - like/dislike + article_id：复用 v1 逻辑写 feedback 表；命中标签 ±0.1（tag_weights）
    - expand：新标签写 tag_weights(1.5, 'feedback')（已存在的保留演化值），并合并进 user_profile.tags
    - adjust：top_n 写 user_profile（保留原 tags/sources；无画像跳过）
    - extract_keywords：keywords 写 interest_weights（+0.1，bump_keyword_weight 钳制 [0.2,2.0]）
    """
    storage.add_feedback_text(raw_text=fb.raw, intent=fb.intent, article_id=fb.article_id,
                              extracted_tags=fb.tags, keywords=fb.keywords, channel="hermes")

    profile = storage.load_profile()
    existing_tags = profile["tags"] if profile else []
    existing_sources = profile["sources"] if profile else []
    existing_top_n = profile["top_n"] if profile else 5

    if fb.intent in ("like", "dislike") and fb.article_id is not None:
        try:
            _apply_like_dislike(storage, fb.article_id, fb.intent)
        except FeedbackError as e:
            logger.warning("文字反馈关联文章不存在 article_id=%s: %s", fb.article_id, e)
        _bump_article_tags(storage, fb.article_id, 0.1 if fb.intent == "like" else -0.1)
    elif fb.intent == "expand":
        existing_tag_weights = dict(storage.list_tags())
        for tag in fb.tags:
            if tag and tag not in existing_tag_weights:
                storage.save_tag_weight(tag, 1.5, "feedback")
        merged_tags = list(dict.fromkeys(existing_tags + fb.tags))
        storage.save_profile(merged_tags, existing_sources, existing_top_n)
    elif fb.intent == "adjust" and fb.top_n is not None and profile is not None:
        storage.save_profile(existing_tags, existing_sources, fb.top_n)

    if extract_keywords:
        for kw in fb.keywords:
            storage.bump_keyword_weight(kw, 0.1)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m12_feedback.py tests/test_feedback.py tests/test_cli.py -q
uv run ruff check daily_picks/feedback.py daily_picks/storage.py tests/test_m12_feedback.py
```
预期：全 PASS（**tests/test_feedback.py、test_cli.py 的 v1 反馈回归必须绿**——分派器兼容），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/storage.py daily_picks/feedback.py tests/test_m12_feedback.py
git commit -m "M12: feedback - apply_feedback 分派与文字反馈演化落库"
```

**验收命令**（本任务）：`uv run pytest tests/test_m12_feedback.py tests/test_feedback.py -q` 全绿。

---

## Task 13: CLI feedback 文字路由（兼容 like/dislike）

**目标**：feedback 子命令支持 `feedback "<文字>"`，与 `feedback like 42` 共存（docs/05 §3.3）。

**涉及文件**：
- 修改：`daily_picks/cli.py`（build_parser feedback 段、cmd_feedback 重写、_route_feedback）
- 测试：`tests/test_m12_feedback.py`（T-FB-10/11）

**接口**：
- Consumes：`parse_feedback`/`apply_feedback`（Task 11/12）、现有 `cmd_feedback` 打印逻辑
- Produces：
  - `def _route_feedback(args: argparse.Namespace) -> tuple[str | None, str | None, int | None]`  # 返回 (kind, text, article_id)，kind 非空走 v1 路径，text 非空走文字路径

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m12_feedback.py`：

```python
import argparse

from daily_picks import cli as cli_mod
from daily_picks.config import write_default_config


class TestFeedbackCliRouting:
    def _chdir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")

    def _seed(self, tmp_path):
        from daily_picks.storage import Storage
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        storage.init_schema()
        aid = storage.upsert_articles([
            Article(source="rss", source_key="k1", title="AI 编程工具实战",
                    url="https://example.com/a")
        ])[0]
        storage.bump_keyword_weight("AI", 0)
        return storage, aid

    # T-FB-10 CLI 文字路由
    def test_text_feedback_routes_to_text_path(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)

        async def fake_parse(raw, llm):
            return ParsedFeedback(raw=raw, intent="expand", article_id=None,
                                  tags=["AI硬件"], keywords=["AI硬件"], top_n=None)

        monkeypatch.setattr(cli_mod, "parse_feedback", fake_parse)
        args = argparse.Namespace(feedback_value=["多推点AI"], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        out = capsys.readouterr().out
        assert "意图: expand" in out
        from daily_picks.storage import Storage
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        assert storage._conn.execute("SELECT COUNT(*) FROM feedback_text").fetchone()[0] == 1

    # T-FB-11 CLI 原用法兼容
    def test_like_42_keeps_v1_behavior(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        storage, aid = self._seed(tmp_path)
        args = argparse.Namespace(feedback_value=["like", str(aid)], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        assert "已更新关键词权重: AI" in capsys.readouterr().out
        assert storage.get_interest_weights()["AI"] == pytest.approx(1.1)

    def test_kind_flag_with_id(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        storage, aid = self._seed(tmp_path)
        args = argparse.Namespace(feedback_value=[str(aid)], kind="dislike", keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        assert storage.get_feedback_kinds(aid) == ["dislike"]

    def test_no_args_prints_usage(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        args = argparse.Namespace(feedback_value=[], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 1
        assert "用法" in capsys.readouterr().err
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m12_feedback.py::TestFeedbackCliRouting -v
```
预期：FAIL（现 cmd_feedback 无 feedback_value 分支，`like 42` 被当文字解析或直接 AttributeError）。

- [ ] **Step 3: 实现**

`daily_picks/cli.py`：

1. 头部导入改为：

```python
from daily_picks.feedback import FeedbackError, apply_feedback, parse_feedback
```

2. `build_parser` 中 feedback 段替换为：

```python
    p_feedback = sub.add_parser("feedback", help="偏好反馈：like/dislike <id> 或文字反馈 <文字>（v3）")
    p_feedback.add_argument("feedback_value", nargs="*",
                            help="like/dislike 用法：kind + 文章 id；文字反馈：反馈原文")
    p_feedback.add_argument("--kind", choices=["like", "dislike"],
                            help="兼容显式指定反馈类型（可选）")
    p_feedback.add_argument("--keyword", help="附加关键词（文章未命中时用于调整权重）")
```

3. `cmd_feedback` 整体替换为：

```python
def _route_feedback(args: argparse.Namespace) -> tuple[str | None, str | None, int | None]:
    """路由 feedback 参数（docs/05 §3.3）。返回 (kind, text, article_id)：
    kind 非空 → 原 like/dislike 逻辑；text 非空 → 文字反馈路径。
    兼容旧式 Namespace(kind=..., article_id=...)（tests/test_cli.py 既有用例）。
    """
    values = list(getattr(args, "feedback_value", None) or [])
    kind = getattr(args, "kind", None)
    if kind is not None:
        article_id = getattr(args, "article_id", None)
        if isinstance(article_id, int):
            return kind, None, article_id
        if values and values[0].isdigit():
            return kind, None, int(values[0])
        return None, " ".join(values), None
    if len(values) == 2 and values[0] in ("like", "dislike") and values[1].isdigit():
        return values[0], None, int(values[1])
    return None, " ".join(values) or None, None


def cmd_feedback(args: argparse.Namespace) -> int:
    """feedback 子命令（docs/05 §3.3）：like/dislike <id> 走原逻辑；纯文字走 parse+apply。"""
    kind, text, article_id = _route_feedback(args)
    cfg = load_config(DEFAULT_CONFIG_PATH)
    storage = _open_storage(cfg)
    if kind is not None:
        try:
            result = apply_feedback(storage, article_id, kind,
                                    extra_keyword=getattr(args, "keyword", None))
        except FeedbackError as e:
            print(f"反馈失败: {e}", file=sys.stderr)
            return 1
        print(f"反馈已记录（{kind}）")
        if result["updated"]:
            print(f"已更新关键词权重: {', '.join(result['updated'])}")
        else:
            print("文章未命中任何关键词，权重未变化（like 可加 --keyword 指定附加关键词）")
        print(f"文章 {article_id} 状态: {result['article_state']}")
        return 0
    if not text:
        print('用法：daily-picks feedback like|dislike <文章id>  或  daily-picks feedback "<文字反馈>"',
              file=sys.stderr)
        return 1
    llm = LLMClient(cfg.llm)
    fb = asyncio.run(parse_feedback(text, llm))
    apply_feedback(fb, storage, extract_keywords=cfg.feedback.extract_keywords)
    print(f"文字反馈已记录（意图: {fb.intent}）")
    if fb.tags:
        print(f"新标签: {', '.join(fb.tags)}")
    if fb.intent == "adjust" and fb.top_n:
        print(f"每日条数已调整为 {fb.top_n} 条")
    return 0
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m12_feedback.py tests/test_cli.py tests/test_feedback.py -q
uv run ruff check daily_picks/cli.py tests/test_m12_feedback.py
```
预期：全 PASS（**tests/test_cli.py::TestFeedbackCmd 既有用例必须绿**——旧 Namespace 兼容路径），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/cli.py tests/test_m12_feedback.py
git commit -m "M12: cli - feedback 文字反馈路由（兼容 like/dislike 原用法）"
```

**验收命令**（本任务，M12 里程碑 gate）：

```bash
cd ~/daily-picks
uv run pytest -q
uv run pytest --cov=daily_picks --cov-report=term-missing | grep TOTAL   # ≥85%
uv run ruff check daily_picks/ tests/
# 实测文字反馈（写真实库；docs/06 §7 建议先备份：mv data/daily_picks.db data/daily_picks.db.bak）
uv run daily-picks feedback "多推点AI硬件"
uv run daily-picks feedback like 1    # 原用法仍可用
uv run daily-picks stats              # 输出含 v3 计数（Task 16 上线后）
systemctl --user restart daily-picks.service
```

---

# 里程碑 M13：自适应演化 + 收尾

> 里程碑验收（design §9 M13）：全量 pytest + 覆盖率 ≥85%；`run --dry-run` 端到端 v3 格式；git log 里程碑提交清晰；提交后重启常驻服务。

## Task 14: weights.py 公共函数 + tracking.apply_click 复用

**目标**：抽公共函数 `_bump_keywords` 到 weights.py（docs/05 §4.1），tracking.apply_click 改复用，行为不变。

**涉及文件**：
- 新建：`daily_picks/weights.py`
- 修改：`daily_picks/tracking.py`（apply_click 重构）
- 测试：`tests/test_m13_evolve.py`（新建，补充用例）+ 回归 `tests/test_tracking.py`

**接口**：
- Consumes：`storage.get_interest_weights/bump_keyword_weight`
- Produces：
  - `def _bump_keywords(text: str, delta: float, storage: Storage) -> list[str]`  # text 中命中的关键词各 +delta（clamp [0.2,2.0]），返回命中列表

- [ ] **Step 1: 写失败测试**

新建 `tests/test_m13_evolve.py`（M13 全部用例文件）：

```python
"""M13 用例：权重演化（测试文档 docs/06 §5；LLM 无关，纯存储操作）。

注意：evolve_weights 在 Task 15 才实现，本文件头部暂不导入（Task 15 追加导入行）。
"""

from __future__ import annotations

import pytest

from daily_picks.models import Article
from daily_picks.weights import _bump_keywords


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    ids = storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])
    return ids[0]


def weight(storage, keyword: str) -> float | None:
    return storage.get_interest_weights().get(keyword)


class TestBumpKeywords:
    """补充用例：公共函数（tracking.apply_click 与 evolve_weights 共用）。"""

    def test_hits_and_bumps(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        tmp_db.bump_keyword_weight("大模型", 0)
        hits = _bump_keywords("本周 AI 与 大模型 实践", 0.05, tmp_db)
        assert hits == ["AI", "大模型"]
        assert weight(tmp_db, "AI") == pytest.approx(1.05)

    def test_case_insensitive_and_no_hit(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        assert _bump_keywords("本周学习 ai 工程", 0.05, tmp_db) == ["AI"]
        assert _bump_keywords("今天天气不错", 0.05, tmp_db) == []
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 未命中不动
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m13_evolve.py::TestBumpKeywords -v
```
预期：FAIL（`ModuleNotFoundError: No module named 'daily_picks.weights'`）。

- [ ] **Step 3: 实现**

1. 新建 `daily_picks/weights.py`：

```python
"""权重演化公共工具（docs/05 §4.1）：tracking 点击回写与 feedback 权重演化共用。"""

from __future__ import annotations

from daily_picks.storage import Storage


def _bump_keywords(text: str, delta: float, storage: Storage) -> list[str]:
    """text 中命中的关键词各 +delta（bump_keyword_weight 钳制 [0.2, 2.0]），返回命中列表。

    命中口径与 feedback.hit_keywords 一致：大小写不敏感子串匹配（docs/05 §4.1）。
    """
    weights = storage.get_interest_weights()
    lower = (text or "").lower()
    hits = [kw for kw in weights if kw and kw.lower() in lower]
    for kw in hits:
        storage.bump_keyword_weight(kw, delta)
    return hits
```

2. `daily_picks/tracking.py`：导入行追加 `from daily_picks.weights import _bump_keywords`；`apply_click` 函数体替换为：

```python
    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        return {"updated": [], "missing": True}
    hits = _bump_keywords(f"{rows[0]['title'] or ''} {rows[0]['summary'] or ''}", delta, storage)
    logger.info("点击回写 article_id=%s 命中关键词=%s delta=%s", article_id, hits, delta)
    return {"updated": hits, "missing": False}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m13_evolve.py::TestBumpKeywords tests/test_tracking.py -q
uv run ruff check daily_picks/weights.py daily_picks/tracking.py tests/test_m13_evolve.py
```
预期：全 PASS（**tests/test_tracking.py 全部回归必须绿**——apply_click 行为不变），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/weights.py daily_picks/tracking.py tests/test_m13_evolve.py
git commit -m "M13: weights - _bump_keywords 公共函数与 apply_click 复用"
```

**验收命令**（本任务）：`uv run pytest tests/test_m13_evolve.py tests/test_tracking.py -q` 全绿。

---

## Task 15: 文档修正 A6 + evolve_weights 权重演化

**目标**：先改文档（A6 游标协作）；storage 增演化查询方法；feedback.py 实现 evolve_weights；tracking.sync_clicks 游标协作。

**涉及文件**：
- 修改：`docs/05-v3开发文档.md`（§4.1 游标协作）
- 修改：`daily_picks/storage.py`（get_clicks_since/get_feedback_text_since/get_max_click_id）
- 修改：`daily_picks/feedback.py`（evolve_weights + 游标常量）
- 修改：`daily_picks/tracking.py`（sync_clicks 末尾推进演化游标）
- 测试：`tests/test_m13_evolve.py`（T-EV-01/02/03/04 + sync 协作补充）

**接口**：
- Consumes：`weights._bump_keywords`（Task 14）、`storage.get_meta/set_meta/add_feedback_text/record_click`
- Produces：
  - `def get_clicks_since(self, click_id: int) -> list[dict]`  # clicks.id > click_id，含 LEFT JOIN articles 的 title/summary
  - `def get_feedback_text_since(self, feedback_id: int) -> list[dict]`  # 含 id/intent/extracted_tags(list)
  - `def get_max_click_id(self) -> int`
  - `def evolve_weights(storage: Storage) -> None`（docs/04 §6.3 锁定签名）
  - 常量：`CLICK_CURSOR_KEY = "last_weight_evolve_id"`、`FEEDBACK_CURSOR_KEY = "last_feedback_evolve_id"`、`CLICK_EVOLVE_DELTA = 0.05`、`EXPAND_EVOLVE_DELTA = 0.1`

- [ ] **Step 1: 修改文档（AGENTS.md 第 4 条）**

`docs/05-v3开发文档.md` §4.1 末尾追加：

```
- 游标协作（2026-08-31 修订，避免同一点击双重回写）：v2 的 sync_clicks 在同步时已
  对每条新点击回写 +0.05（tracking.apply_click）。evolve_weights 的点击演化使用 meta 键
  `last_weight_evolve_id`（clicks.id 游标），sync_clicks 回写完成后同步推进该游标到
  max(clicks.id)；evolve_weights 点击演化仅覆盖游标之后、未被 sync 回写的 clicks 行
  （防御性兜底，如未来其他渠道写入的 clicks）。feedback_text 演化游标为
  `last_feedback_evolve_id`（feedback_text.id 游标）。
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/test_m13_evolve.py`：

```python
from daily_picks.feedback import CLICK_CURSOR_KEY, FEEDBACK_CURSOR_KEY, evolve_weights


class TestEvolveWeights:
    def _click(self, tmp_db, aid: int, remote_id: int) -> None:
        tmp_db.record_click(article_id=aid, click_date="2026-08-27", remote_id=remote_id, count=1)

    # T-EV-01 点击演化
    def test_click_evolve_bumps_keywords(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        self._click(tmp_db, aid, remote_id=1)
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)

    # T-EV-02 游标幂等
    def test_evolve_twice_no_double_bump(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        self._click(tmp_db, aid, remote_id=1)
        evolve_weights(tmp_db)
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 第二次不重复
        assert tmp_db.get_meta(CLICK_CURSOR_KEY) == "1"

    # T-EV-03 标签演化
    def test_expand_feedback_tag_evolves(self, tmp_db):
        tmp_db.add_feedback_text(raw_text="多推点AI硬件", intent="expand", article_id=None,
                                 extracted_tags=["AI硬件"], keywords=[])
        evolve_weights(tmp_db)
        assert weight(tmp_db, "AI硬件") == pytest.approx(1.1)  # 新词默认 1.0 + 0.1
        assert tmp_db.get_meta(FEEDBACK_CURSOR_KEY) == "1"

    def test_non_expand_feedback_not_evolved(self, tmp_db):
        tmp_db.add_feedback_text(raw_text="今天天气不错", intent="none", article_id=None,
                                 extracted_tags=[], keywords=[])
        evolve_weights(tmp_db)
        assert tmp_db.get_interest_weights() == {}

    # T-EV-04 权重 clamp
    def test_evolve_clamped_at_2(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        for i in range(30):
            self._click(tmp_db, aid, remote_id=i + 1)
            evolve_weights(tmp_db)
            assert weight(tmp_db, "AI") <= 2.0
        assert weight(tmp_db, "AI") == 2.0

    # 补充：sync 游标协作（docs/05 §4.1 修订）
    async def test_sync_clicks_advances_evolve_cursor(self, tmp_db):
        from daily_picks.models import ClickEvent
        from daily_picks.tracking import sync_clicks

        class FakeTrackingClient:
            async def fetch_clicks(self, after: int):
                return ([], False) if after else (
                    [ClickEvent(remote_id=1, article_id=aid, click_date="2026-08-27", count=1)],
                    False)

        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="AI 硬件趋势观察")
        await sync_clicks(tmp_db, FakeTrackingClient(), 0.05)
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # sync 回写
        evolve_weights(tmp_db)  # 演化应跳过已回写行
        assert weight(tmp_db, "AI") == pytest.approx(1.05)  # 无双重回写
        assert int(tmp_db.get_meta(CLICK_CURSOR_KEY)) >= 1
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_m13_evolve.py::TestEvolveWeights -v
```
预期：FAIL（`ImportError: cannot import name 'evolve_weights'`）。

- [ ] **Step 4: 实现**

1. `daily_picks/storage.py` `count_clicks` 之后新增：

```python
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
```

2. `daily_picks/feedback.py` 头部导入追加 `from daily_picks.weights import _bump_keywords`；文件末尾追加：

```python
# ---- v3 权重演化（docs/04 §6.3 / docs/05 §4.1）----

CLICK_CURSOR_KEY = "last_weight_evolve_id"      # clicks.id 演化游标（meta 表）
FEEDBACK_CURSOR_KEY = "last_feedback_evolve_id"  # feedback_text.id 演化游标（meta 表）
CLICK_EVOLVE_DELTA = 0.05                        # 点击弱信号（对齐 tracking.click_delta 默认值）
EXPAND_EVOLVE_DELTA = 0.1                        # 扩展标签强化步长


def evolve_weights(storage: Storage) -> None:
    """点击/反馈驱动的权重演化（docs/04 §6.3）：
    1. clicks 增量（游标 last_weight_evolve_id）→ 文章 title+summary 关键词 +0.05
    2. feedback_text 中 intent='expand' 未处理的（游标 last_feedback_evolve_id）→ 标签关键词 +0.1
    3. 写入全部经 bump_keyword_weight 钳制 [0.2, 2.0]；游标单调推进（幂等，可反复调用）。
    """
    click_cursor = int(storage.get_meta(CLICK_CURSOR_KEY) or 0)
    for row in storage.get_clicks_since(click_cursor):
        _bump_keywords(f"{row['title'] or ''} {row['summary'] or ''}",
                       CLICK_EVOLVE_DELTA, storage)
        click_cursor = max(click_cursor, int(row["id"]))
    if click_cursor:
        storage.set_meta(CLICK_CURSOR_KEY, str(click_cursor))

    fb_cursor = int(storage.get_meta(FEEDBACK_CURSOR_KEY) or 0)
    for row in storage.get_feedback_text_since(fb_cursor):
        if row["intent"] == "expand":
            for tag in row["extracted_tags"]:
                storage.bump_keyword_weight(tag, EXPAND_EVOLVE_DELTA)
        fb_cursor = max(fb_cursor, int(row["id"]))
    if fb_cursor:
        storage.set_meta(FEEDBACK_CURSOR_KEY, str(fb_cursor))
```

3. `daily_picks/tracking.py`：导入行追加 `from daily_picks.feedback import CLICK_CURSOR_KEY`；`sync_clicks` 中 `if after > cursor:` 块之后追加：

```python
    # 游标协作（docs/05 §4.1 修订）：同步时已回写权重，推进演化游标避免双重回写
    storage.set_meta(CLICK_CURSOR_KEY, str(storage.get_max_click_id()))
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_m13_evolve.py tests/test_tracking.py tests/test_feedback.py tests/test_m12_feedback.py -q
uv run ruff check daily_picks/storage.py daily_picks/feedback.py daily_picks/tracking.py tests/test_m13_evolve.py
```
预期：全 PASS，ruff 零告警。

- [ ] **Step 6: 提交**

```bash
git add docs/05-v3开发文档.md daily_picks/storage.py daily_picks/feedback.py daily_picks/tracking.py tests/test_m13_evolve.py
git commit -m "M13: docs - 修订 evolve 游标协作说明（先改文档，AGENTS.md 第4条）"
git commit -m "M13: feedback - evolve_weights 权重演化与游标幂等"
```

**验收命令**（本任务）：`uv run pytest tests/test_m13_evolve.py tests/test_tracking.py -q` 全绿。

---

## Task 16: run_once 集成演化 + stats v3 计数

**目标**：run_once 采集后打分前调用 evolve_weights；cmd_stats 输出 v3 计数；storage.get_v3_counts。

**涉及文件**：
- 修改：`daily_picks/storage.py`（get_v3_counts）
- 修改：`daily_picks/cli.py`（run_once 步骤 5.6 + cmd_stats + 导入 evolve_weights）
- 测试：`tests/test_m13_evolve.py`（T-EV-05 + stats 补充）

**接口**：
- Consumes：`evolve_weights`（Task 15）、`storage.load_profile`
- Produces：
  - `def get_v3_counts(self) -> dict`  # `{'feedback_text': int, 'tag_weights': int, 'profile_configured': bool, 'top_n': int | None}`
  - run_once / cmd_stats 行为变更（签名不变）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_m13_evolve.py`：

```python
# ---- v3 run_once 演化集成（自带源 mock：tests/ 无 __init__.py，跨测试文件导入不可靠）----

import json as json_mod
from pathlib import Path

import httpx

from daily_picks import cli as cli_mod
from daily_picks.cli import run_once

RSS_URL = "https://sspai.com/feed"
RSS_URL2 = "https://www.ruanyifeng.com/blog/atom.xml"
BILI_URL = "https://api.bilibili.com/x/web-interface/popular"
ZHIHU_URL = "https://api.zhihu.com/topstory/hot-lists/total"
JUEJIN_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
HN_URL = "https://hn.algolia.com/api/v1/search"
INFOQ_URL = "https://www.infoq.cn/feed"
FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def mock_sources(mock_http) -> None:
    mock_http.get(RSS_URL).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(RSS_URL2).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(BILI_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("bilibili_sample.json"))))
    mock_http.get(ZHIHU_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("zhihu_sample.json"))))
    mock_http.post(JUEJIN_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("juejin_sample.json"))))
    mock_http.get(HN_URL).mock(
        return_value=httpx.Response(200, json=json_mod.loads(load("hnews_sample.json"))))
    mock_http.get(INFOQ_URL).mock(return_value=httpx.Response(200, content=load("infoq_sample.xml")))


class TestEvolveIntegration:
    """docs/06 §5 T-EV-05 + stats v3 计数补充（对齐 tests/test_e2e.py 的 respx 写法）。"""

    async def test_evolve_called_before_scoring(self, sample_config, tmp_path, mock_http,
                                                frozen_now, monkeypatch):
        cfg = sample_config
        cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
        cfg.profile.enabled = True
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # 无 key → 规则降级，不触 LLM 端点
        mock_sources(mock_http)

        order: list[str] = []
        monkeypatch.setattr(cli_mod, "evolve_weights", lambda storage: order.append("evolve"))
        real_rule_score = cli_mod.rule_score

        def wrapped_rule_score(*args, **kw):
            order.append("score")
            return real_rule_score(*args, **kw)

        monkeypatch.setattr(cli_mod, "rule_score", wrapped_rule_score)
        assert await run_once(cfg, dry_run=True) == 0
        assert order and order[0] == "evolve"  # 演化在打分之前
        assert "score" in order

    async def test_evolve_not_called_when_profile_disabled(self, sample_config, tmp_path,
                                                           mock_http, frozen_now, monkeypatch):
        cfg = sample_config
        cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
        assert cfg.profile.enabled is False  # v2 行为：不触发演化
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        mock_sources(mock_http)
        calls: list[bool] = []
        monkeypatch.setattr(cli_mod, "evolve_weights", lambda storage: calls.append(True))
        assert await run_once(cfg, dry_run=True) == 0
        assert calls == []


class TestStatsV3:
    """补充用例：stats 输出 v3 计数（docs/05 §4.2）。"""

    def test_stats_includes_v3_counts(self, tmp_path, monkeypatch, capsys):
        from daily_picks import cli as cli_mod
        from daily_picks.config import write_default_config
        from daily_picks.storage import Storage

        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        storage.init_schema()
        storage.save_profile(["AI大模型"], ["hnews"], 3)
        storage.add_feedback_text(raw_text="多推点AI硬件", intent="expand", article_id=None,
                                  extracted_tags=["AI硬件"], keywords=[])
        storage.save_tag_weight("AI硬件", 1.5, "feedback")
        import argparse

        assert cli_mod.cmd_stats(argparse.Namespace(days=7)) == 0
        out = capsys.readouterr().out
        assert "文字反馈: 1 条" in out
        assert "标签权重: 1 条" in out
        assert "已配置（每日 3 条）" in out
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_m13_evolve.py::TestEvolveIntegration tests/test_m13_evolve.py::TestStatsV3 -v
```
预期：FAIL（run_once 无 evolve 调用 → `order == []`；stats 无 v3 行）。

- [ ] **Step 3: 实现**

1. `daily_picks/storage.py` 末尾新增：

```python
    def get_v3_counts(self) -> dict:
        """v3 计数（stats 输出，docs/05 §4.2）：feedback_text/tag_weights 行数 + user_profile 状态。"""
        profile = self.load_profile()
        with self._lock:
            try:
                fb_row = self._conn.execute("SELECT COUNT(*) FROM feedback_text").fetchone()
                tag_row = self._conn.execute("SELECT COUNT(*) FROM tag_weights").fetchone()
            except sqlite3.Error as e:
                raise StorageError(f"统计 v3 数据失败: {e}") from e
        return {
            "feedback_text": int(fb_row[0]),
            "tag_weights": int(tag_row[0]),
            "profile_configured": profile is not None,
            "top_n": profile["top_n"] if profile else None,
        }
```

2. `daily_picks/cli.py`：

- 导入行追加：`from daily_picks.feedback import evolve_weights`（并入现有 feedback 导入行）
- `run_once` 中"步骤 5.5 点击同步"块之后、"步骤 6"注释之前插入：

```python
    # 步骤 5.6（v3）：权重演化（docs/05 §4.1，采集后打分前）。失败只记日志，不阻塞主流程。
    if cfg.profile.enabled:
        try:
            evolve_weights(storage)
        except StorageError as e:
            logger.warning("权重演化失败（不影响主流程）: %s", e)
```

- `cmd_stats` 在 `print(f"  成本(CNY)…")` 之后追加：

```python
    v3 = storage.get_v3_counts()
    print("v3 深度精选:")
    print(f"  文字反馈: {v3['feedback_text']} 条")
    print(f"  标签权重: {v3['tag_weights']} 条")
    if v3["profile_configured"]:
        print(f"  用户画像: 已配置（每日 {v3['top_n']} 条）")
    else:
        print("  用户画像: 未配置（运行 daily-picks setup）")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_m13_evolve.py tests/test_cli.py tests/test_e2e.py -q
uv run ruff check daily_picks/cli.py daily_picks/storage.py tests/test_m13_evolve.py
```
预期：全 PASS（test_cli.py::TestStatsCmd、test_e2e.py 回归必须绿），ruff 零告警。

- [ ] **Step 5: 提交**

```bash
git add daily_picks/storage.py daily_picks/cli.py tests/test_m13_evolve.py
git commit -m "M13: cli - run_once 集成权重演化与 stats v3 计数"
```

**验收命令**（本任务）：`uv run pytest tests/test_m13_evolve.py tests/test_cli.py tests/test_e2e.py -q` 全绿。

---

## Task 17: 文档终稿与全量收尾

**目标**：docs/05 §5 终稿、docs/06 补用例清单、README v3 章节、AGENTS.md 核对；全量验收。

**涉及文件**：
- 修改：`docs/05-v3开发文档.md`（§4.2 收尾段更新 + §0 chat 备注已在 Task 4 + 登记存储新方法清单）
- 修改：`docs/06-v3测试文档.md`（§7 补充用例清单）
- 修改：`README.md`（v3 章节）
- 核对：`AGENTS.md`（v3 阅读顺序已在位，无需改；确认提交格式示例仍适用）

**接口**：无新代码；纯文档。

- [ ] **Step 1: 更新 docs/05**

§4.2 收尾段改写为已完成状态，并追加最终实现清单：

```markdown
### 4.2 文档收尾（已完成，2026-08-31）

- docs/05 §5 为最终版 prompt 模板（deep_analyze/parse_feedback/_llm_recommend 实现直接复用）
- docs/06 §7 为 v3 补充用例清单
- AGENTS.md 第 1 条已含 v3 阅读顺序；README 含 v3 章节
- `daily-picks stats` 输出含 v3 计数（feedback_text/tag_weights/user_profile 状态）

## 7. v3 存储新方法清单（M10-M13 实现）

save_profile / load_profile / save_tag_weight / list_tags / register_source / list_sources /
add_feedback_text / get_clicks_since / get_feedback_text_since / get_max_click_id / get_v3_counts
（全部在 daily_picks/storage.py，签名见各方法 docstring）
```

- [ ] **Step 2: 更新 docs/06**

文件末尾追加：

```markdown
## 7. v3 补充用例清单（M10-M13 实现期新增，未编号于上表）

| 文件 | 用例 | 断言要点 |
|---|---|---|
| test_m10_setup.py | TestV3Config | profile/feedback 配置默认值、越界校验、save_config 回环 |
| test_m10_setup.py | TestProfileStorage | 画像单行幂等、tag 权重 clamp、源注册 |
| test_m10_setup.py | TestChooseTags/TestChooseTopN 边界 | 混合输入、全非法回退默认 |
| test_m10_setup.py | TestLlmChat | chat 围栏剥离与消息构造 |
| test_m11_deep.py | TestDeepAnalyze 边界 | 非数字评分、空 reason 回退、非法 JSON |
| test_m11_deep.py | TestDeepFilter 边界 | 空候选、weights 透传、超时不重复调 LLM |
| test_m11_deep.py | TestRunOnceV3 | v3 deep 路径出 v3 简报；无 key 跳过 deep |
| test_m12_feedback.py | TestFeedbackChannel | 抽象类不可实例化、Hermes 占位行为 |
| test_m12_feedback.py | TestParseFeedback 启发式边界 | adjust/expand/none 启发式分支 |
| test_m12_feedback.py | TestApplyTextFeedback 边界 | 已有标签不动、无画像 adjust noop、clamp |
| test_m12_feedback.py | TestApplyFeedbackDispatch | v1 签名回归（分派器兼容） |
| test_m13_evolve.py | TestBumpKeywords | 命中/大小写/未命中不动 |
| test_m13_evolve.py | TestEvolveWeights 补充 | sync 游标协作不双重回写、非 expand 不演化 |
| test_m13_evolve.py | TestEvolveIntegration | profile 关闭不演化 |
| test_m13_evolve.py | TestStatsV3 | stats 输出 v3 计数 |
```

- [ ] **Step 3: 更新 README.md**

在 README 现有用法章节后新增 v3 章节（含 setup 向导、feedback 文字反馈、v3 推送格式示例、`profile:`/`feedback:` 配置段说明、v3 与 v2 切换开关 `profile.enabled`）。

- [ ] **Step 4: 核对 AGENTS.md**

确认第 1 条含 v3 阅读顺序（已在位，2026-08-31 提交 a986ecd 引入）、第 7 条提交格式与重启规范无遗漏；无需修改则不提交。

- [ ] **Step 5: 提交**

```bash
git add docs/05-v3开发文档.md docs/06-v3测试文档.md README.md
git commit -m "M13: docs - 开发/测试文档终稿与 README v3 章节"
```

**验收命令**（本任务，M13 最终 gate）：

```bash
cd ~/daily-picks
uv run pytest -q                                                      # 全量全绿（242 + v3 全部新用例）
uv run pytest --cov=daily_picks --cov-report=term-missing | grep TOTAL    # TOTAL ≥85%
uv run ruff check daily_picks/ tests/                                # 零告警
uv run daily-picks run --dry-run          # 端到端：有 key 出 📚 v3 格式；无 key 走 v2 回归
uv run daily-picks setup <<< $'1,2\n\n3\n'  # 临时目录防污染（见 M10 gate）
uv run daily-picks feedback "多推点AI硬件"   # 文字反馈实测
uv run daily-picks stats                    # 输出含 v3 计数
git log --oneline -20                       # 里程碑提交清晰（M10→M13）
systemctl --user restart daily-picks.service   # AGENTS.md 第 7 条（最后必做）
```

---

## 执行备注（裁决记录与风险）

1. **文档冲突裁决**：本计划预判的 6 处冲突（A1-A6）已在对应任务 Step 1 以"先改文档"落地，提交信息均注明。执行时若发现**新的**文档/实现冲突，同样遵循 AGENTS.md 第 4 条：先改 docs/04-06，再改代码，提交信息注明，并回到本计划补录。
2. **同一点击双重回写**：A6 裁决后，`sync_clicks` 回写即推进演化游标；T-EV 补充用例 `test_sync_clicks_advances_evolve_cursor` 锁住该行为。
3. **DeepResult.article_id=0** 是 `deep_analyze` 单测时的占位值（签名无 id 参数），`deep_filter` 必回填 `sa.article_id`；下游（digest_v3）只通过 `deep_map` 的 key 取用，不依赖该字段。
4. **测试隔离**：所有 v3 测试不触网（FakeLLM/respx）；`cli.py` 不在覆盖率统计范围（pyproject omit），但行为由 test_cli/test_e2e/v3 集成用例验证。
5. **实测防污染**：`daily-picks setup`/`feedback` 实测写真实库，按 docs/06 §7 用备份库法（`mv data/daily_picks.db data/daily_picks.db.bak`）。
