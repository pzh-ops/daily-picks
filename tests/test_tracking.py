"""点击追踪测试（测试文档 §4.12 T-TRACK-01~13；全部 respx mock，禁真实网络）。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx  # noqa: F401  # respx mock 路由器经 conftest 的 mock_http fixture 激活（brief 要求保留该 import）

from daily_picks.models import Article, ClickEvent
from daily_picks.tracking import (
    CODE_ALPHABET,
    CODE_LENGTH,
    TrackingClient,
    TrackingError,
    apply_click,
    build_tracking_url,
    gen_code,
    sync_clicks,
)


class TestPureFunctions:
    # T-TRACK-01：短码格式——8 位、仅 base62 字符、随机不重复
    def test_gen_code_format(self):
        code = gen_code()
        assert len(code) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in code)
        assert gen_code() != gen_code()  # 随机性（碰撞概率可忽略）

    # T-TRACK-01b：gen_code 尊重显式长度
    def test_gen_code_custom_length(self):
        assert len(gen_code(6)) == 6

    # T-TRACK-02：短链构造——base 去尾斜杠 + /c/{code}
    def test_build_tracking_url(self):
        assert build_tracking_url("https://track.example.workers.dev/", "abcd1234") == \
            "https://track.example.workers.dev/c/abcd1234"
        assert build_tracking_url("https://track.example.workers.dev", "abcd1234") == \
            "https://track.example.workers.dev/c/abcd1234"

    def test_tracking_error_is_exception(self):
        assert issubclass(TrackingError, Exception)


BASE = "https://track.example.workers.dev"
LINKS_URL = f"{BASE}/api/links"
CLICKS_URL = f"{BASE}/api/clicks"


class TestRegisterLinks:
    # T-TRACK-03：注册成功——Bearer 头 + 契约 body + 返回短链
    async def test_register_links_ok(self, mock_http):
        route = mock_http.post(LINKS_URL).mock(
            return_value=httpx.Response(200, json={"ok": True}))
        client = TrackingClient(BASE, "test-token")
        result = await client.register_links([(42, "https://example.com/a")])
        assert list(result) == [42]
        assert result[42].startswith(BASE + "/c/")
        code = result[42].rsplit("/", 1)[-1]
        assert len(code) == CODE_LENGTH and all(c in CODE_ALPHABET for c in code)
        req = route.calls[0].request
        assert req.headers["Authorization"] == "Bearer test-token"
        body = json.loads(req.content)
        assert body == {"code": code, "url": "https://example.com/a", "article_id": 42}

    # T-TRACK-04：部分失败（400）→ 只返回成功项，不抛异常
    async def test_register_links_partial_failure(self, mock_http, caplog):
        mock_http.post(LINKS_URL).mock(side_effect=[
            httpx.Response(200, json={"ok": True}),
            httpx.Response(400),
        ])
        client = TrackingClient(BASE, "test-token")
        result = await client.register_links(
            [(42, "https://example.com/a"), (43, "https://example.com/b")])
        assert list(result) == [42]

    # T-TRACK-05：5xx 重试（502→502→200）后成功，共请求 3 次
    async def test_register_links_retry_on_5xx(self, mock_http):
        route = mock_http.post(LINKS_URL).mock(side_effect=[
            httpx.Response(502), httpx.Response(502),
            httpx.Response(200, json={"ok": True}),
        ])
        client = TrackingClient(BASE, "test-token")
        result = await client.register_links([(42, "https://example.com/a")])
        assert list(result) == [42]
        assert route.call_count == 3


class TestFetchClicks:
    # T-TRACK-06：正常响应解析为 ClickEvent 列表 + has_more
    async def test_fetch_clicks_ok(self, mock_http):
        route = mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(200, json={
            "clicks": [{"id": 3, "article_id": 42, "click_date": "2026-08-28", "count": 2}],
            "has_more": False,
        }))
        client = TrackingClient(BASE, "test-token")
        events, has_more = await client.fetch_clicks(0)
        assert events == [ClickEvent(remote_id=3, article_id=42,
                                     click_date="2026-08-28", count=2)]
        assert has_more is False
        assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"

    # T-TRACK-07：401 → TrackingError（4xx 不重试）
    async def test_fetch_clicks_unauthorized(self, mock_http):
        mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(401))
        client = TrackingClient(BASE, "test-token")
        with pytest.raises(TrackingError, match="401"):
            await client.fetch_clicks(0)

    # T-TRACK-08：响应结构非法 → TrackingError
    async def test_fetch_clicks_malformed(self, mock_http):
        mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(200, json={"clicks": "oops"}))
        client = TrackingClient(BASE, "test-token")
        with pytest.raises(TrackingError, match="响应非法"):
            await client.fetch_clicks(0)


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    """插入一篇文章并返回 id（复用 T-FBK 模式）。"""
    return storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])[0]


class TestApplyClick:
    # T-TRACK-09：命中关键词 +delta，且不写 feedback 表（点击 ≠ 显式反馈）
    def test_apply_click_bumps_hit_keywords(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)  # AI = 1.0
        aid = seed_article(tmp_db)
        result = apply_click(tmp_db, aid, 0.05)
        assert result == {"updated": ["AI"], "missing": False}
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.05)
        assert tmp_db.get_feedback_kinds(aid) == []  # 不写 feedback

    # T-TRACK-10：未命中 → 权重不变；文章不存在 → missing=True（均不抛异常）
    def test_apply_click_no_hit_or_missing(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db, title="区块链入门", summary="去中心化原理")
        assert apply_click(tmp_db, aid, 0.05) == {"updated": [], "missing": False}
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.0)
        assert apply_click(tmp_db, 999, 0.05) == {"updated": [], "missing": True}


class TestSyncClicks:
    def _client(self):
        return TrackingClient(BASE, "test-token")

    def _page(self, clicks, has_more=False):
        return httpx.Response(200, json={"clicks": clicks, "has_more": has_more})

    # T-TRACK-11：幂等——同一批事件同步两次，第二次不重复回写
    async def test_sync_clicks_idempotent(self, tmp_db, mock_http):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        mock_http.get(CLICKS_URL).mock(return_value=self._page(
            [{"id": 1, "article_id": aid, "click_date": "2026-08-28", "count": 2}]))
        client = self._client()
        first = await sync_clicks(tmp_db, client, 0.05)
        second = await sync_clicks(tmp_db, client, 0.05)
        assert first == {"synced": 1, "applied": 1}
        assert second == {"synced": 1, "applied": 0}  # 幂等：不重复回写
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.05)
        assert tmp_db.get_click_cursor() == 1

    # T-TRACK-12：分页游标——has_more 两页，第二页 after=第一页末 id，游标推进到末位
    async def test_sync_clicks_pagination_cursor(self, tmp_db, mock_http):
        tmp_db.bump_keyword_weight("AI", 0)
        a1 = seed_article(tmp_db)
        route = mock_http.get(CLICKS_URL).mock(side_effect=[
            self._page([{"id": 1, "article_id": a1, "click_date": "2026-08-28", "count": 1}],
                       has_more=True),
            self._page([{"id": 2, "article_id": a1, "click_date": "2026-08-28", "count": 1}]),
        ])
        result = await sync_clicks(tmp_db, self._client(), 0.05)
        assert result == {"synced": 2, "applied": 2}
        assert route.calls[1].request.url.params["after"] == "1"
        assert tmp_db.get_click_cursor() == 2
        assert tmp_db.count_clicks() == 2

    # T-TRACK-13：网络失败 → TrackingError 上抛（游标不动，调用方 fail-open）
    async def test_sync_clicks_raises_on_error(self, tmp_db, mock_http):
        mock_http.get(CLICKS_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(TrackingError):
            await sync_clicks(tmp_db, self._client(), 0.05)
        assert tmp_db.get_click_cursor() == 0
