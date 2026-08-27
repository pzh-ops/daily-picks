"""LLM 客户端测试（测试文档 §4.4 T-LLM-01~13；全部 respx mock，禁真实网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from daily_picks.config import LLMConfig
from daily_picks.llm import (
    INPUT_USD_PER_1M,
    OUTPUT_USD_PER_1M,
    LLMClient,
    LLMError,
    estimate_cost,
)
from daily_picks.models import Article, ScoredArticle

FIXTURES = Path(__file__).parent / "fixtures"
LLM_URL = "https://llm.test/chat/completions"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_scored(article_id: int = 1, source: str = "rss",
                title: str = "AI 编程工具", summary: str = "AI 摘要", score: float = 1.0) -> ScoredArticle:
    article = Article(source=source, source_key=str(article_id), title=title,
                      url=f"https://example.com/{article_id}", summary=summary)
    return ScoredArticle(article=article, score=score, article_id=article_id)


@pytest.fixture
def llm_cfg(monkeypatch) -> LLMConfig:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    return LLMConfig(base_url="https://llm.test", timeout_s=2.0, max_input_chars=12000)


@pytest.fixture
def llm_client(llm_cfg) -> LLMClient:
    return LLMClient(llm_cfg)


def ok_response() -> dict:
    return json.loads(load_fixture("llm_response_ok.json"))


class TestCandidateJson:
    # T-LLM-01
    async def test_rank_builds_candidates_json(self, mock_http, llm_client):
        route = mock_http.post(LLM_URL).mock(return_value=httpx.Response(200, json=ok_response()))
        candidates = [make_scored(1), make_scored(2, source="bilibili", title="B站视频")]
        result = await llm_client.rank(candidates, profile="{}", top_n=3)
        assert result.ok
        body = json.loads(route.calls[0].request.content)
        sent = json.loads(body["messages"][1]["content"])["candidates"]
        assert len(sent) == 2
        assert set(sent[0]) == {"article_id", "title", "summary", "source"}
        assert sent[0]["article_id"] == 1 and sent[1]["article_id"] == 2
        system = body["messages"][0]["content"]
        assert "3 条内容" in system and "{}" in system  # {top_n} 与 {profile_json} 已替换


class TestParseResponse:
    # T-LLM-02
    def test_valid_json(self):
        content = ok_response()["choices"][0]["message"]["content"]
        result = LLMClient.parse_response(content, valid_ids={1, 2}, top_n=3)
        assert result.ok
        assert len(result.picks) == 1
        assert result.picks[0].article_id == 1
        assert result.picks[0].rank == 1

    # T-LLM-03
    def test_fence_stripped(self):
        content = json.loads(load_fixture("llm_response_fence.txt"))["choices"][0]["message"]["content"]
        result = LLMClient.parse_response(content, valid_ids={1}, top_n=3)
        assert result.ok
        assert result.picks == []

    # T-LLM-04
    def test_non_json_content(self):
        content = json.loads(load_fixture("llm_response_badjson.txt"))["choices"][0]["message"]["content"]
        result = LLMClient.parse_response(content, valid_ids={1}, top_n=3)
        assert not result.ok

    # T-LLM-05
    def test_article_id_not_in_candidates(self):
        text = '{"picks":[{"article_id":999,"rank":1,"reason":"x"}]}'
        result = LLMClient.parse_response(text, valid_ids={1}, top_n=3)
        assert not result.ok

    # T-LLM-06
    def test_duplicate_rank(self):
        text = ('{"picks":[{"article_id":1,"rank":1,"reason":"a"},'
                '{"article_id":2,"rank":1,"reason":"b"}]}')
        result = LLMClient.parse_response(text, valid_ids={1, 2}, top_n=3)
        assert not result.ok

    # T-LLM-07
    def test_more_than_top_n(self):
        picks = [{"article_id": i, "rank": i, "reason": "x"} for i in range(1, 6)]
        result = LLMClient.parse_response(json.dumps({"picks": picks}), valid_ids=set(range(1, 6)), top_n=3)
        assert not result.ok

    def test_invalid_item_structure(self):
        result = LLMClient.parse_response('{"picks":[{"article_id":"1","rank":1}]}',
                                          valid_ids={1}, top_n=3)
        assert not result.ok


class TestChat:
    # T-LLM-08
    async def test_request_body_and_transparent_response(self, mock_http, llm_client):
        route = mock_http.post(LLM_URL).mock(return_value=httpx.Response(200, json=ok_response()))
        data = await llm_client._chat([{"role": "user", "content": "hi"}])
        assert data == ok_response()
        request = route.calls[0].request
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["model"] == "deepseek-v4-pro"
        assert request.headers["Authorization"] == "Bearer test-key"

    # T-LLM-09
    async def test_retry_on_5xx_then_success(self, mock_http, llm_client):
        route = mock_http.post(LLM_URL).mock(
            side_effect=[httpx.Response(502), httpx.Response(200, json=ok_response())]
        )
        data = await llm_client._chat([{"role": "user", "content": "hi"}])
        assert data == ok_response()
        assert route.call_count == 2

    # T-LLM-10
    async def test_retry_exhausted_on_429(self, mock_http, llm_client):
        route = mock_http.post(LLM_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(LLMError):
            await llm_client._chat([{"role": "user", "content": "hi"}])
        assert route.call_count == 3

    # T-LLM-11
    async def test_timeout_raises_llm_error(self, llm_cfg):
        client = LLMClient(llm_cfg)

        async def _timeout(_request):
            raise httpx.ReadTimeout("mock read timeout")

        with respx.mock(assert_all_mocked=True, assert_all_called=False) as m:
            route = m.post(LLM_URL).mock(side_effect=_timeout)
            with pytest.raises(LLMError):
                await client._chat([{"role": "user", "content": "hi"}])
        assert route.call_count == 3  # tenacity 重试 3 次后放弃

    # 补充：4xx 不重试，直接抛 LLMError（开发文档 §4.12）
    async def test_4xx_not_retried(self, mock_http, llm_client):
        route = mock_http.post(LLM_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(LLMError):
            await llm_client._chat([{"role": "user", "content": "hi"}])
        assert route.call_count == 1

    # 补充：rank() 网络异常返回 ok=False 不抛（设计文档 §7.3 降级）
    async def test_rank_returns_not_ok_on_http_error(self, mock_http, llm_client):
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(500))
        result = await llm_client.rank([make_scored(1)], profile="{}", top_n=3)
        assert not result.ok
        assert result.picks == []


class TestCost:
    # T-LLM-12
    def test_cost_formula(self):
        assert INPUT_USD_PER_1M == 0.66
        assert OUTPUT_USD_PER_1M == 1.98
        assert estimate_cost(1000, 80) == pytest.approx(0.0008184)

    async def test_usage_accounting_through_rank(self, mock_http, llm_client):
        mock_http.post(LLM_URL).mock(return_value=httpx.Response(200, json=ok_response()))
        result = await llm_client.rank([make_scored(1)], profile="{}", top_n=3)
        assert result.ok
        assert result.tokens_in == 1000
        assert result.tokens_out == 80
        assert llm_client.last_tokens_in == 1000  # 供 run_once 记账
        assert llm_client.last_tokens_out == 80


class TestInputTruncation:
    # T-LLM-13
    async def test_input_truncated_to_max_chars(self, mock_http, llm_client):
        llm_client.cfg.max_input_chars = 12000
        route = mock_http.post(LLM_URL).mock(return_value=httpx.Response(200, json=ok_response()))
        candidates = [
            make_scored(i, title=f"第{i}条", summary="字" * 500) for i in range(1, 41)  # 候选文本约 20k 字符
        ]
        result = await llm_client.rank(candidates, profile="{}", top_n=3)
        assert result.ok  # 响应 picks 引用第 1 条（在保留的靠前候选内）
        user_content = json.loads(route.calls[0].request.content)["messages"][1]["content"]
        assert len(user_content) <= 12000
        assert "第1条" in user_content
        assert "第40条" not in user_content  # 靠后的候选被截断
