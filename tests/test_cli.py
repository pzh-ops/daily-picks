"""CLI 补充用例：init 交互确认与 EOFError 优雅取消（M1 修复项；cli.py 不在覆盖率统计范围）。"""

from __future__ import annotations

import argparse

from daily_picks.cli import cmd_init


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
