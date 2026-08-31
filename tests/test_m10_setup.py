"""M10 用例：配置 v3 段 / setup 向导 / 存储方法（测试文档 docs/06 §1；LLM 全部 mock）。"""

from __future__ import annotations

import json

import pytest

from daily_picks.config import ConfigError, RootConfig, load_config, save_config, write_default_config
from daily_picks.storage import StorageError


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
