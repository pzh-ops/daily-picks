"""T-CFG 配置模块用例（测试文档 §4.1）。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import yaml

from daily_picks.config import ConfigError, LLMConfig, RootConfig, load_config, write_default_config


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

    def test_api_key_missing_raises(self, monkeypatch):  # T-CFG-06
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ConfigError):
            _ = LLMConfig().api_key

    def test_api_key_from_env(self, monkeypatch):  # T-CFG-06 补充
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        assert LLMConfig().api_key == "sk-test"

    def test_keyword_weight_out_of_range(self, tmp_path):  # T-CFG-08
        path = tmp_path / "bad.yaml"
        path.write_text("interests:\n  keywords:\n    - {keyword: AI, weight: 100}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

    # ---- 补充用例（提升 config.py 分支覆盖）----

    def test_dotenv_loaded_and_existing_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("DP_DOTENV_KEY=from_dotenv\n", encoding="utf-8")
        monkeypatch.setenv("DP_DOTENV_KEY", "existing")
        with pytest.raises(ConfigError):
            load_config()  # config.yaml 不存在会报错，但 .env 先被加载
        assert os.environ["DP_DOTENV_KEY"] == "existing"  # 已存在的环境变量优先

    def test_dotenv_sets_missing_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("DP_DOTENV_KEY=from_dotenv\n", encoding="utf-8")
        monkeypatch.delenv("DP_DOTENV_KEY", raising=False)
        with pytest.raises(ConfigError):
            load_config()
        assert os.environ["DP_DOTENV_KEY"] == "from_dotenv"

    def test_dotenv_skips_comments_and_blanks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("# 注释行\n\nDP_SKIP_KEY=val\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config()
        assert os.environ["DP_SKIP_KEY"] == "val"

    def test_unknown_source_section_warns(self, tmp_path, caplog):
        path = tmp_path / "c.yaml"
        path.write_text("sources:\n  foo:\n    weight: 1\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="daily_picks.config"):
            cfg = load_config(str(path))
        assert cfg.sources.enabled == []
        assert "foo" in caplog.text

    def test_type_validation_error(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("llm:\n  temperature: hot\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

    def test_broken_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("digest: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

    def test_top_level_not_mapping(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(path))

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


class TestTrackingConfig:
    """T-CFG：tracking 配置段（设计文档 §15.6）。"""

    # T-CFG-TRACK-01：默认关闭（base_url 空 → enabled False，默认值与 §11 一致）
    def test_tracking_defaults_disabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        cfg = load_config("config.yaml")
        assert cfg.tracking.base_url == ""
        assert cfg.tracking.enabled is False
        assert cfg.tracking.api_key_env == "TRACKING_API_TOKEN"
        assert cfg.tracking.timeout_s == 10
        assert cfg.tracking.click_delta == 0.05

    # T-CFG-TRACK-02：base_url 非空 → enabled True
    def test_tracking_enabled_with_base_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        text = Path("config.yaml").read_text(encoding="utf-8").replace(
            'base_url: ""', 'base_url: "https://track.example.workers.dev"'
        )
        Path("config.yaml").write_text(text, encoding="utf-8")
        cfg = load_config("config.yaml")
        assert cfg.tracking.enabled is True

    # T-CFG-TRACK-03：base_url 非法协议 → ConfigError
    def test_tracking_bad_base_url_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        text = Path("config.yaml").read_text(encoding="utf-8").replace(
            'base_url: ""', 'base_url: "ftp://bad"'
        )
        Path("config.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="tracking.base_url"):
            load_config("config.yaml")

    # T-CFG-TRACK-04：click_delta 越界 → ConfigError
    def test_tracking_click_delta_out_of_range_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")
        text = Path("config.yaml").read_text(encoding="utf-8").replace(
            "click_delta: 0.05", "click_delta: 0.5"
        )
        Path("config.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError, match="tracking.click_delta"):
            load_config("config.yaml")
