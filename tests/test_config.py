"""T-CFG 配置模块用例（测试文档 §4.1）。"""

from __future__ import annotations

import logging

import pytest
import yaml

from daily_picks.config import ConfigError, RootConfig, load_config, write_default_config


class TestConfig:
    def test_missing_file_raises(self, tmp_path, monkeypatch):  # T-CFG-01
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError):
            load_config()

    def test_minimal_yaml_uses_defaults(self, tmp_path):  # T-CFG-02
        path = tmp_path / "min.yaml"
        path.write_text("digest:\n  top_n: 5\n", encoding="utf-8")
        cfg = load_config(str(path))
        assert isinstance(cfg, RootConfig)
        assert cfg.digest.top_n == 5
        assert cfg.digest.max_candidates == 40
        assert cfg.llm.model == "deepseek-v4-pro"
        assert cfg.push.provider == "wecom"
        assert cfg.storage.db_path == "data/daily_picks.db"

    def test_top_n_exceeds_max_candidates(self, tmp_path):  # T-CFG-03
        path = tmp_path / "bad.yaml"
        path.write_text("digest:\n  top_n: 50\n  max_candidates: 40\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

    def test_invalid_provider(self, tmp_path):  # T-CFG-04
        path = tmp_path / "bad.yaml"
        path.write_text("push:\n  provider: sms\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

    def test_unknown_source_warns_only(self, tmp_path, caplog):  # T-CFG-05
        path = tmp_path / "c.yaml"
        path.write_text("sources:\n  enabled: [rss, foo]\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="daily_picks.config"):
            cfg = load_config(str(path))
        assert cfg.sources.enabled == ["rss", "foo"]
        assert "foo" in caplog.text

    def test_write_default_config_roundtrip(self, tmp_path):  # T-CFG-07
        path = tmp_path / "config.yaml"
        write_default_config(path)
        cfg1 = load_config(str(path))
        cfg2 = load_config(str(path))
        assert cfg1 == cfg2
        text = path.read_text(encoding="utf-8")
        assert "provider: wecom" in text
        data = yaml.safe_load(text)
        assert data["push"]["provider"] == "wecom"
        assert data["push"]["wecom"]["webhook_key_env"] == "WECOM_WEBHOOK_KEY"
        # 嵌套 YAML 拍平为 PushConfig 扁平字段
        assert cfg1.push.wecom_webhook_key_env == "WECOM_WEBHOOK_KEY"
