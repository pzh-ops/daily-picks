"""M13 用例：权重演化（测试文档 docs/06 §5；LLM 无关，纯存储操作）。

注意：evolve_weights 在 Task 15 才实现，本文件头部暂不导入（Task 15 追加导入行）。
"""

from __future__ import annotations

import pytest

from daily_picks.feedback import CLICK_CURSOR_KEY, FEEDBACK_CURSOR_KEY, evolve_weights
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
        db = tmp_path / "data" / "daily_picks.db"
        db.parent.mkdir(parents=True, exist_ok=True)  # 对齐 test_cli.py::TestStatsCmd 的建目录写法
        storage = Storage(db)
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
