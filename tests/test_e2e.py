"""E2E 全流程测试（测试文档 §4.10 T-E2E-01~07；全部外部 HTTP 用 respx mock，禁真实网络）。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx
import pytest

from daily_picks.cli import run_once
from daily_picks.models import Article
from daily_picks.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"

RSS_URL = "https://sspai.com/feed"
RSS_URL2 = "https://www.ruanyifeng.com/blog/atom.xml"
BILI_URL = "https://api.bilibili.com/x/web-interface/popular"
ZHIHU_URL = "https://api.zhihu.com/topstory/hot-lists/total"
JUEJIN_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
HN_URL = "https://hn.algolia.com/api/v1/search"
INFOQ_URL = "https://www.infoq.cn/feed"
WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
TRACK_BASE = "https://track.example.workers.dev"
LINKS_URL = f"{TRACK_BASE}/api/links"
CLICKS_URL = f"{TRACK_BASE}/api/clicks"
LLM_URL = "https://api.deepseek.com/chat/completions"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def mock_sources(mock_http) -> None:
    """注册全部 6 个源的路由（测试文档 §3 fixtures）。"""
    mock_http.get(RSS_URL).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(RSS_URL2).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
    mock_http.get(BILI_URL).mock(
        return_value=httpx.Response(200, json=json.loads(load("bilibili_sample.json"))))
    mock_http.get(ZHIHU_URL).mock(
        return_value=httpx.Response(200, json=json.loads(load("zhihu_sample.json"))))
    mock_http.post(JUEJIN_URL).mock(
        return_value=httpx.Response(200, json=json.loads(load("juejin_sample.json"))))
    mock_http.get(HN_URL).mock(
        return_value=httpx.Response(200, json=json.loads(load("hnews_sample.json"))))
    mock_http.get(INFOQ_URL).mock(return_value=httpx.Response(200, content=load("infoq_sample.xml")))


@pytest.fixture
def e2e_cfg(sample_config, tmp_path):
    """E2E 配置：dry_run_file 指向 tmp（避免污染仓库 logs/）。"""
    cfg = sample_config
    cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
    return cfg


def get_run(e2e_cfg, run_date: str = "2026-08-27") -> dict:
    """读取 run_once 落库的 digest_run 记录（start_digest_run 幂等返回已有 id）。"""
    storage = Storage(Path(e2e_cfg.storage.db_path))
    run_id = storage.start_digest_run(run_date, 0)
    return storage.get_digest_run(run_id)


def entry_count(text: str) -> int:
    """简报中的条目数（形如 `1. 【来源】标题` 的行）。"""
    return len([line for line in text.splitlines() if re.match(r"^\d+\. 【", line)])


class TestRunOnce:
    # T-E2E-01：dry-run 全流程（无 LLM key → 规则分降级；简报含全部候选）
    async def test_dry_run_full_flow(self, e2e_cfg, mock_http, frozen_now, capsys):
        mock_sources(mock_http)
        assert await run_once(e2e_cfg, dry_run=True) == 0
        digest_file = Path(e2e_cfg.push.dry_run_file)
        assert digest_file.is_file()
        text = digest_file.read_text(encoding="utf-8")
        assert "📌 今日精选 · 2026-08-27" in text
        assert entry_count(text) == 8  # 6 源 fixtures 共 8 条去重后新文章
        assert "链接：" in text
        out = capsys.readouterr().out
        assert "精选 8 条" in out
        run = get_run(e2e_cfg)
        assert run["channel"] == "dry-run"
        assert run["pushed"] == 0
        assert run["fallback_used"] == 1  # 无 DEEPSEEK_API_KEY → 规则分降级

    # T-E2E-02：真实推送全流程（mock wecom ok）→ 返回 0，pushed=1，channel=wecom
    async def test_push_full_flow(self, e2e_cfg, mock_http, frozen_now, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
        mock_sources(mock_http)
        wecom = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("wecom_ok.json"))))
        assert await run_once(e2e_cfg) == 0
        assert wecom.call_count == 1
        body = json.loads(wecom.calls[0].request.content)
        assert body["msgtype"] == "text"
        assert "📌 今日精选" in body["text"]["content"]
        run = get_run(e2e_cfg)
        assert run["pushed"] == 1
        assert run["channel"] == "wecom"

    # T-E2E-03：单源故障不影响整体（知乎 500，dry-run 仍成功且含其余源内容）
    async def test_single_source_failure_isolated(self, e2e_cfg, mock_http, frozen_now):
        mock_sources(mock_http)
        mock_http.get(ZHIHU_URL).mock(return_value=httpx.Response(500))
        assert await run_once(e2e_cfg, dry_run=True) == 0
        text = Path(e2e_cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert entry_count(text) >= 1
        assert not any("【知乎】" in line for line in text.splitlines())  # 知乎条目缺失（单源失败隔离）

    # T-E2E-04：LLM 故障降级推送（LLM 500 → fallback_used=1，仍推送成功）
    async def test_llm_failure_falls_back_and_pushes(self, e2e_cfg, mock_http, frozen_now, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
        mock_sources(mock_http)
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(500))
        wecom = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("wecom_ok.json"))))
        assert await run_once(e2e_cfg) == 0
        assert wecom.call_count == 1
        run = get_run(e2e_cfg)
        assert run["fallback_used"] == 1
        assert run["pushed"] == 1

    # T-E2E-05：推送失败（wecom 93000）→ 返回 1，ERROR 日志，pushed=0
    async def test_push_failure_return_code(self, e2e_cfg, mock_http, frozen_now, monkeypatch, caplog):
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
        mock_sources(mock_http)
        mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("wecom_badkey.json"))))
        with caplog.at_level(logging.ERROR, logger="daily_picks.cli"):
            assert await run_once(e2e_cfg) == 1
        assert any("推送失败" in r.message for r in caplog.records)
        run = get_run(e2e_cfg)
        assert run["pushed"] == 0
        assert run["channel"] == "wecom"

    # T-E2E-06：全源失败 → 返回 1，简报提示无内容
    async def test_all_sources_failed(self, e2e_cfg, mock_http, frozen_now):
        mock_sources(mock_http)
        mock_http.get(RSS_URL).mock(return_value=httpx.Response(500))
        mock_http.get(RSS_URL2).mock(return_value=httpx.Response(500))
        mock_http.get(BILI_URL).mock(return_value=httpx.Response(500))
        mock_http.get(ZHIHU_URL).mock(return_value=httpx.Response(500))
        mock_http.post(JUEJIN_URL).mock(return_value=httpx.Response(500))
        mock_http.get(HN_URL).mock(return_value=httpx.Response(500))
        mock_http.get(INFOQ_URL).mock(return_value=httpx.Response(500))
        assert await run_once(e2e_cfg, dry_run=True) == 1
        text = Path(e2e_cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert "今日无精选内容" in text

    # T-E2E-07：同日幂等——第二次 run 不重复推送（webhook 仅调用 1 次）
    async def test_same_day_idempotent(self, e2e_cfg, mock_http, frozen_now, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
        mock_sources(mock_http)
        wecom = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("wecom_ok.json"))))
        assert await run_once(e2e_cfg) == 0
        assert await run_once(e2e_cfg) == 0
        assert wecom.call_count == 1  # 第二次跳过推送
        run = get_run(e2e_cfg)
        assert run["pushed"] == 1  # 保持已推送状态
        assert run["channel"] == "wecom"


class TestTrackingIntegration:
    # T-E2E-08：tracking 全链路（dry-run）——点击回写权重 + 简报链接替换为短链 + 游标推进
    async def test_tracking_full_flow(self, e2e_cfg, mock_http, frozen_now, monkeypatch, capsys):
        monkeypatch.setenv("TRACKING_API_TOKEN", "test-token")
        e2e_cfg.tracking.base_url = TRACK_BASE
        # 预置一篇关键词命中的文章，让 worker 返回它的点击事件
        # （db 父目录由 run_once 创建；此处先于 run_once 建库，需先行 mkdir，对齐 _open_storage）
        Path(e2e_cfg.storage.db_path).parent.mkdir(parents=True, exist_ok=True)
        storage = Storage(Path(e2e_cfg.storage.db_path))
        storage.init_schema()
        storage.bump_keyword_weight("AI", 0)  # AI = 1.0
        pre_id = storage.upsert_articles([
            Article(source="rss", source_key="pre-seed", title="AI 大事记",
                    url="https://example.com/ai-post", summary="AI 领域今日进展")
        ])[0]
        mock_sources(mock_http)
        mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(200, json={
            "clicks": [{"id": 1, "article_id": pre_id, "click_date": "2026-08-27", "count": 2}],
            "has_more": False,
        }))
        mock_http.post(LINKS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert await run_once(e2e_cfg, dry_run=True) == 0
        text = Path(e2e_cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert text.count("链接：") == 8                       # 简报 8 条（fixtures 新文章）
        # 全部链接行都已替换为短链（预置文章已在库中、不进 new_ids，故不参与本次推送）
        assert text.count(f"链接：{TRACK_BASE}/c/") == 8
        storage2 = Storage(Path(e2e_cfg.storage.db_path))
        assert storage2.get_click_cursor() == 1               # 游标已推进
        assert storage2.get_interest_weights()["AI"] == pytest.approx(1.05)  # 点击回写 +0.05
        assert storage2.count_clicks() == 1
        out = capsys.readouterr().out
        assert "点击同步" in out

    # T-E2E-09：短链注册失败 → fail-open：简报保留原始 URL，run 仍返回 0
    async def test_tracking_register_failure_keeps_original_urls(
            self, e2e_cfg, mock_http, frozen_now, monkeypatch):
        monkeypatch.setenv("TRACKING_API_TOKEN", "test-token")
        e2e_cfg.tracking.base_url = TRACK_BASE
        mock_sources(mock_http)
        mock_http.get(CLICKS_URL).mock(
            return_value=httpx.Response(200, json={"clicks": [], "has_more": False}))
        mock_http.post(LINKS_URL).mock(return_value=httpx.Response(500))
        assert await run_once(e2e_cfg, dry_run=True) == 0
        text = Path(e2e_cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert "/c/" not in text
        assert "链接：" in text

    # T-E2E-10：点击同步失败 → fail-open：短链仍注册，run 仍返回 0，游标不动
    async def test_tracking_sync_failure_still_pushes(
            self, e2e_cfg, mock_http, frozen_now, monkeypatch):
        monkeypatch.setenv("TRACKING_API_TOKEN", "test-token")
        e2e_cfg.tracking.base_url = TRACK_BASE
        mock_sources(mock_http)
        mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(500))
        mock_http.post(LINKS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert await run_once(e2e_cfg, dry_run=True) == 0
        text = Path(e2e_cfg.push.dry_run_file).read_text(encoding="utf-8")
        assert "/c/" in text
        assert Storage(Path(e2e_cfg.storage.db_path)).get_click_cursor() == 0

    # T-E2E-11：tracking 关闭（base_url 空）→ 零追踪请求（v1 行为回归）
    async def test_tracking_disabled_makes_no_requests(self, e2e_cfg, mock_http, frozen_now):
        e2e_cfg.tracking.base_url = ""
        mock_sources(mock_http)
        links_route = mock_http.post(LINKS_URL).mock(
            return_value=httpx.Response(200, json={"ok": True}))
        clicks_route = mock_http.get(CLICKS_URL).mock(
            return_value=httpx.Response(200, json={"clicks": [], "has_more": False}))
        assert await run_once(e2e_cfg, dry_run=True) == 0
        assert links_route.call_count == 0
        assert clicks_route.call_count == 0
