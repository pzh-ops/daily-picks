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
