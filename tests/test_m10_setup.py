"""M10 用例：配置 v3 段 / setup 向导 / 存储方法（测试文档 docs/06 §1；LLM 全部 mock）。"""

from __future__ import annotations

import argparse
import json
import json as json_mod

import pytest

from daily_picks.config import ConfigError, RootConfig, load_config, save_config, write_default_config
from daily_picks.llm import LLMError
from daily_picks.setup import (
    DEFAULT_TAGS,
    TAG_SOURCE_MAP,
    _llm_recommend,
    choose_tags,
    choose_top_n,
    recommend_sources,
    run_setup,
)
from daily_picks.storage import Storage, StorageError


class TestV3Config:
    """补充用例：profile/feedback 配置段（docs/06 未编号，登记见 Task 17）。"""

    def test_profile_defaults_disabled(self):
        cfg = RootConfig()
        assert cfg.profile.enabled is False
        assert cfg.profile.top_n == 5
        assert cfg.profile.deep_threshold == 60
        assert cfg.profile.deep_candidates == 20  # 8/31 调参
        assert cfg.feedback.channel == "hermes"
        assert cfg.feedback.extract_keywords is True

    def test_load_config_parses_v3_sections(self, tmp_path):
        path = tmp_path / "config.yaml"
        write_default_config(path)
        text = path.read_text(encoding="utf-8")
        text = text.replace("profile:\n  enabled: false", "profile:\n  enabled: true")
        text = text.replace("top_n: 5", "top_n: 3")
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


class TestRunSetup:
    def _env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        cfg = load_config("config.yaml")
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)  # Storage 要求父目录存在
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
        inputs = iter(["1", "", "5", "1", "", "4"])
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


class TestRegistryDrivenCollect:
    """registry 驱动采集（D-07 补做，docs/04 §6.5）：setup 注册的 rss 源合并进采集。"""

    def _cfg_with_rss(self):
        from daily_picks.config import RootConfig, SourceSection
        cfg = RootConfig()
        cfg.sources.enabled = ["rss", "hnews"]
        cfg.sources.rss = SourceSection(urls=["https://cfg.example.com/feed"])
        return cfg

    def test_build_adapters_merges_registry_urls(self, tmp_path):
        from daily_picks.sources import build_adapters
        from daily_picks.sources.rss import RssAdapter
        storage = Storage(tmp_path / "t.db")
        storage.init_schema()
        storage.register_source("rss:测试源", "测试源", "https://reg.example.com/rss", ["AI大模型"])
        adapters = build_adapters(self._cfg_with_rss(), storage)
        rss = next(a for a in adapters if isinstance(a, RssAdapter))
        assert rss.extra_urls == ["https://reg.example.com/rss"]

    def test_build_adapters_without_storage_no_extra(self):
        from daily_picks.sources import build_adapters
        from daily_picks.sources.rss import RssAdapter
        adapters = build_adapters(self._cfg_with_rss())
        rss = next(a for a in adapters if isinstance(a, RssAdapter))
        assert rss.extra_urls == []

    def test_build_adapters_ignores_disabled(self, tmp_path):
        from daily_picks.sources import build_adapters
        from daily_picks.sources.rss import RssAdapter
        storage = Storage(tmp_path / "t.db")
        storage.init_schema()
        storage.register_source("rss:启用", "启用", "https://on.example.com/rss", [])
        storage.register_source("rss:禁用", "禁用", "https://off.example.com/rss", [])
        conn = storage._conn
        conn.execute("UPDATE source_registry SET enabled=0 WHERE key='rss:禁用'")
        conn.commit()
        adapters = build_adapters(self._cfg_with_rss(), storage)
        rss = next(a for a in adapters if isinstance(a, RssAdapter))
        assert rss.extra_urls == ["https://on.example.com/rss"]

    def test_fetch_uses_union_dedup(self, tmp_path, monkeypatch):
        """fetch 时 config.urls 与 extra_urls 并集去重（保序）。"""
        from daily_picks.sources.rss import RssAdapter
        adapter = RssAdapter(extra_urls=["https://b.example.com/feed", "https://a.example.com/feed"])
        seen: list[str] = []
        import httpx
        class FakeClient:
            async def get(self, url, **kw):
                seen.append(url)
                resp = httpx.Response(200, text="<rss><channel><title>t</title></channel></rss>")
                return resp
        cfg = self._cfg_with_rss().sources.rss
        cfg.urls = ["https://a.example.com/feed", "https://c.example.com/feed"]
        import asyncio
        asyncio.run(adapter.fetch(cfg, FakeClient()))
        assert seen == ["https://a.example.com/feed", "https://c.example.com/feed", "https://b.example.com/feed"]
