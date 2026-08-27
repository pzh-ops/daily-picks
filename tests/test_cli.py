"""CLI 补充用例：init 交互确认与 EOFError 优雅取消（M1）、M4 子命令 feedback/stats/serve/test llm。

cli.py 不在覆盖率统计范围（pyproject coverage.omit），此处验证行为与退出码。
"""

from __future__ import annotations

import argparse
from datetime import datetime

import httpx
import pytest

from daily_picks import cli as cli_mod
from daily_picks.cli import cmd_feedback, cmd_init, cmd_serve, cmd_stats, cmd_test, main
from daily_picks.config import write_default_config
from daily_picks.models import Article
from daily_picks.storage import Storage

LLM_URL = "https://api.deepseek.com/chat/completions"


def chdir_with_default_config(tmp_path, monkeypatch) -> None:
    """切到临时目录并生成默认 config.yaml（cmd_* 以相对路径读取）。"""
    monkeypatch.chdir(tmp_path)
    write_default_config("config.yaml")


class TestInit:
    def test_confirm_no_cancels(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("app: {}\n", encoding="utf-8")
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        assert cmd_init(argparse.Namespace(force=False)) == 0
        assert "已取消" in capsys.readouterr().out
        assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "app: {}\n"  # 未被覆盖

    def test_eof_treated_as_cancel(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("app: {}\n", encoding="utf-8")

        def _raise_eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)
        assert cmd_init(argparse.Namespace(force=False)) == 0  # 不抛"未预期的错误"
        assert "取消" in capsys.readouterr().out
        assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == "app: {}\n"  # 未被覆盖

    def test_force_overwrites_and_initializes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("old\n", encoding="utf-8")
        assert cmd_init(argparse.Namespace(force=True)) == 0
        out = capsys.readouterr().out
        assert "provider: wecom" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert (tmp_path / ".env.example").exists()
        assert (tmp_path / "data" / "daily_picks.db").exists()
        assert "已初始化数据库" in out


class TestFeedbackCmd:
    """M4 feedback 子命令：权重更新打印、找不到文章退出码 1。"""

    def _seed(self, tmp_path) -> tuple[Storage, int]:
        db = tmp_path / "data" / "daily_picks.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        storage = Storage(db)
        storage.init_schema()
        aid = storage.upsert_articles([
            Article(source="rss", source_key="k1", title="AI 编程工具实战",
                    url="https://example.com/a")
        ])[0]
        storage.bump_keyword_weight("AI", 0)  # AI = 1.0
        return storage, aid

    def test_like_updates_keyword_weights(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        storage, aid = self._seed(tmp_path)
        assert cmd_feedback(argparse.Namespace(kind="like", article_id=aid, keyword=None)) == 0
        out = capsys.readouterr().out
        assert "已更新关键词权重: AI" in out
        assert storage.get_interest_weights()["AI"] == pytest.approx(1.1)

    def test_unknown_article_exit_1(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        assert cmd_feedback(argparse.Namespace(kind="like", article_id=1, keyword=None)) == 1
        err = capsys.readouterr().err
        assert "反馈失败" in err
        assert "不存在" in err

    def test_main_unknown_article_exit_1(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        assert main(["feedback", "like", "1"]) == 1
        assert "不存在" in capsys.readouterr().err

    def test_like_no_hit_with_keyword(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        storage, _ = self._seed(tmp_path)
        # 用不命中标题的文章测 --keyword 路径
        aid2 = storage.upsert_articles([
            Article(source="rss", source_key="k2", title="今天天气不错",
                    url="https://example.com/b")
        ])[0]
        assert cmd_feedback(argparse.Namespace(kind="like", article_id=aid2, keyword="开源")) == 0
        out = capsys.readouterr().out
        assert "已更新关键词权重: 开源" in out
        assert storage.get_interest_weights()["开源"] == pytest.approx(1.1)


class TestStatsCmd:
    """M4 stats 子命令：统计表格含 USD/CNY 成本。"""

    def test_stats_table_with_cost(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        db = tmp_path / "data" / "daily_picks.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        storage = Storage(db)
        storage.init_schema()
        rid = storage.start_digest_run(datetime.now().strftime("%Y-%m-%d"), 0)
        storage.finish_digest_run(rid, picked_count=5, pushed=1, channel="wecom",
                                  tokens_in=1000, tokens_out=100, cost_usd=0.001, fallback_used=False)
        assert cmd_stats(argparse.Namespace(days=7)) == 0
        out = capsys.readouterr().out
        assert "近 7 天统计" in out
        assert "运行次数" in out and "推送次数" in out
        assert "Token 输入" in out and "Token 输出" in out
        assert "$0.001000" in out
        assert "¥0.0072" in out  # 0.001 USD × 7.2 CNY/USD
        assert "7.2" in out

    def test_stats_invalid_days_exit_1(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)
        assert cmd_stats(argparse.Namespace(days=0)) == 1
        assert "days" in capsys.readouterr().err


class TestServeCmd:
    """M4 serve 子命令：不真启动，验证委托 run_forever 与 --help。"""

    def test_serve_delegates_to_run_forever(self, tmp_path, monkeypatch):
        chdir_with_default_config(tmp_path, monkeypatch)
        captured = {}
        monkeypatch.setattr(cli_mod, "run_forever", lambda cfg: captured.setdefault("cfg", cfg))
        assert cmd_serve(argparse.Namespace()) == 0
        assert captured["cfg"].schedule.time == "08:00"

    def test_serve_help(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli_mod.build_parser().parse_args(["serve", "--help"])
        assert excinfo.value.code == 0
        assert "usage: daily-picks serve" in capsys.readouterr().out


class TestTestLLM:
    """M4 test llm 子命令：respx mock /chat/completions（禁真实网络）。"""

    def test_llm_ping_ok(self, tmp_path, monkeypatch, capsys, mock_http):
        chdir_with_default_config(tmp_path, monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(
            200, json={"model": "deepseek-v4-pro",
                       "choices": [{"message": {"content": "pong"}}]}))
        assert cmd_test(argparse.Namespace(target="llm")) == 0
        out = capsys.readouterr().out
        assert "LLM OK" in out
        assert "deepseek-v4-pro" in out
        assert "延迟" in out

    def test_llm_ping_bad_key_http_error(self, tmp_path, monkeypatch, capsys, mock_http):
        chdir_with_default_config(tmp_path, monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "bad-key")
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(
            401, json={"error": {"message": "invalid api key"}}))
        assert cmd_test(argparse.Namespace(target="llm")) == 1
        assert "LLM 自检失败" in capsys.readouterr().err

    def test_llm_ping_non_json_response(self, tmp_path, monkeypatch, capsys, mock_http):
        chdir_with_default_config(tmp_path, monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(200, content=b"not json"))
        assert cmd_test(argparse.Namespace(target="llm")) == 1
        assert "LLM 自检失败" in capsys.readouterr().err

    def test_llm_ping_missing_key(self, tmp_path, monkeypatch, capsys):
        chdir_with_default_config(tmp_path, monkeypatch)  # tmp 目录无 .env，不会重新加载密钥
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert cmd_test(argparse.Namespace(target="llm")) == 1
        err = capsys.readouterr().err
        assert "未配置 DEEPSEEK_API_KEY" in err
