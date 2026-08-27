"""推送测试（测试文档 §4.7 T-PUSH-01~09；全部 respx mock，禁真实网络）。"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import respx

from daily_picks.config import PushConfig
from daily_picks.publisher import (
    NoopPublisher,
    ServerChanPublisher,
    WecomPublisher,
    create_publisher,
)

FIXTURES = Path(__file__).parent / "fixtures"
WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
SERVERCHAN_URL = "https://sctapi.ftqq.com/sk123.send"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestWecom:
    # T-PUSH-01：成功（errcode==0）
    async def test_success(self, mock_http):
        route = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("wecom_ok.json"))
        )
        result = await WecomPublisher("test-key").push("今日精选", "内容")
        assert result.ok
        assert result.channel == "wecom"
        assert "errcode" in result.detail and "0" in result.detail
        body = json.loads(route.calls[0].request.content)
        assert body == {"msgtype": "text", "text": {"content": "内容"}}

    # T-PUSH-02：无效 key（errcode 93000）→ ok=False，detail 提示 key
    async def test_bad_key(self, mock_http):
        mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("wecom_badkey.json"))
        )
        result = await WecomPublisher("bad-key").push("t", "内容")
        assert not result.ok
        assert "key" in result.detail
        assert "93000" in result.detail

    # T-PUSH-03：5xx 重试（502→502→200），最终成功，请求 3 次
    async def test_retry_on_5xx(self, mock_http):
        route = mock_http.post(WECOM_URL).mock(side_effect=[
            httpx.Response(502), httpx.Response(502),
            httpx.Response(200, json=load_fixture("wecom_ok.json")),
        ])
        result = await WecomPublisher("test-key").push("t", "内容")
        assert result.ok
        assert route.call_count == 3

    # T-PUSH-04：超长内容截断 → 请求体 content 字节 ≤2048
    async def test_content_truncated_to_2048_bytes(self, mock_http):
        route = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("wecom_ok.json"))
        )
        content = "中" * 3000  # 9000 字节
        result = await WecomPublisher("test-key").push("t", content)
        assert result.ok
        sent = json.loads(route.calls[0].request.content)["text"]["content"]
        assert len(sent.encode("utf-8")) <= 2048

    # 补充：4xx 不重试（开发文档 §4.15）
    async def test_4xx_not_retried(self, mock_http):
        route = mock_http.post(WECOM_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        result = await WecomPublisher("test-key").push("t", "内容")
        assert not result.ok
        assert "403" in result.detail
        assert route.call_count == 1

    # 补充：5xx 重试耗尽 → ok=False 而非抛异常
    async def test_retry_exhausted_returns_not_ok(self, mock_http):
        route = mock_http.post(WECOM_URL).mock(return_value=httpx.Response(500))
        result = await WecomPublisher("test-key").push("t", "内容")
        assert not result.ok
        assert route.call_count == 3

    # 补充：超时重试耗尽 → ok=False 而非抛异常
    async def test_timeout_returns_not_ok(self):
        async def _hang(_request):
            raise httpx.ReadTimeout("mock read timeout")

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as m:
            route = m.post(WECOM_URL).mock(side_effect=_hang)
            result = await WecomPublisher("test-key", timeout_s=1.0).push("t", "内容")
        assert not result.ok
        assert "超时" in result.detail
        assert route.call_count == 3

    # T-PUSH-09：密钥缺失 → ok=False 而非抛异常
    async def test_missing_key_returns_not_ok(self, monkeypatch):
        monkeypatch.delenv("WECOM_WEBHOOK_KEY", raising=False)
        result = await WecomPublisher("").push("t", "内容")
        assert not result.ok
        assert "未配置 WECOM_WEBHOOK_KEY" in result.detail


class TestServerChan:
    # T-PUSH-05：成功（code==0），form 字段 title/desp
    async def test_success(self, mock_http):
        route = mock_http.post(SERVERCHAN_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("serverchan_ok.json"))
        )
        content = "1. 【掘金】标题\n   摘要：x\n   链接：https://example.com/1"
        result = await ServerChanPublisher("sk123").push("今日精选", content)
        assert result.ok
        assert result.channel == "serverchan"
        form = dict(parse_qsl(route.calls[0].request.content.decode()))
        assert form["title"] == "今日精选"
        assert "[链接](https://example.com/1)" in form["desp"]

    # T-PUSH-06：失败码（code!=0）→ ok=False
    async def test_failure_code(self, mock_http):
        mock_http.post(SERVERCHAN_URL).mock(
            return_value=httpx.Response(200, json={"code": 1, "message": "fail"})
        )
        result = await ServerChanPublisher("sk123").push("t", "内容")
        assert not result.ok
        assert "code=1" in result.detail

    # 补充：desp 链接行转 markdown，作者行内链接不转换（设计文档 §8）
    async def test_markdown_links_conversion(self, mock_http):
        route = mock_http.post(SERVERCHAN_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("serverchan_ok.json"))
        )
        content = (
            "1. 【掘金】标题\n"
            "   摘要：x\n"
            "   作者：深小乐 ｜ 链接：https://juejin.cn/post/1\n"
            "   链接：https://example.com/x"
        )
        await ServerChanPublisher("sk123").push("今日精选", content)
        desp = dict(parse_qsl(route.calls[0].request.content.decode()))["desp"]
        assert "[链接](https://example.com/x)" in desp  # 独立链接行转换
        assert "作者：深小乐 ｜ 链接：https://juejin.cn/post/1" in desp  # 作者行保持纯文本

    # 补充：密钥缺失 → ok=False 而非抛异常
    async def test_missing_sendkey_returns_not_ok(self, monkeypatch):
        monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)
        result = await ServerChanPublisher("").push("t", "内容")
        assert not result.ok
        assert "未配置 SERVERCHAN_SENDKEY" in result.detail

    # 补充：HTTP 错误 → ok=False 而非抛异常
    async def test_http_error_returns_not_ok(self, mock_http):
        mock_http.post(SERVERCHAN_URL).mock(return_value=httpx.Response(500))
        result = await ServerChanPublisher("sk123").push("t", "内容")
        assert not result.ok
        assert "500" in result.detail


class TestNoop:
    # T-PUSH-07：文件内容 == content，ok=True
    async def test_writes_file(self, tmp_path):
        target = tmp_path / "out" / "last_digest.md"
        result = await NoopPublisher(str(target)).push("t", "# 内容")
        assert result.ok
        assert result.channel == "noop"
        assert target.read_text(encoding="utf-8") == "# 内容"

    # 补充：目录不存在时自动创建
    async def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.md"
        result = await NoopPublisher(str(target)).push("t", "x")
        assert result.ok
        assert target.exists()


class TestFactory:
    # T-PUSH-08：provider 路由
    def test_routing(self):
        assert isinstance(create_publisher(PushConfig(provider="wecom")), WecomPublisher)
        assert isinstance(create_publisher(PushConfig(provider="serverchan")), ServerChanPublisher)
        assert isinstance(create_publisher(PushConfig(provider="none")), NoopPublisher)

    def test_wecom_reads_key_from_env(self, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "wk123")
        pub = create_publisher(PushConfig(provider="wecom"))
        assert isinstance(pub, WecomPublisher)
        assert pub.key == "wk123"

    def test_custom_env_name_honored(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "custom")
        pub = create_publisher(PushConfig(provider="wecom", wecom_webhook_key_env="MY_KEY"))
        assert pub.key == "custom"
