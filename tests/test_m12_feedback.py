"""M12 用例：反馈通道/意图解析/演化落库（测试文档 docs/06 §4；LLM 全部 mock）。"""

from __future__ import annotations

import json as json_mod

import pytest

from daily_picks.feedback import FEEDBACK_INTENTS, parse_feedback
from daily_picks.feedback_channels import FeedbackChannel, HermesChannel, RawFeedback
from daily_picks.llm import LLMError


class TestFeedbackChannel:
    def test_raw_feedback_defaults(self):
        fb = RawFeedback(text="多推点AI硬件")
        assert fb.text == "多推点AI硬件"
        assert fb.article_id is None
        assert fb.channel == "hermes"

    def test_abstract_class_not_instantiable(self):
        with pytest.raises(TypeError):
            FeedbackChannel()  # 含抽象方法，禁止实例化

    async def test_hermes_receive_returns_empty(self):
        channel = HermesChannel()
        assert channel.name == "hermes"
        assert await channel.receive() == []

    async def test_hermes_acknowledge_noop(self):
        await HermesChannel().acknowledge("fb-1")  # 不抛错即通过


class FakeFeedbackLLM:
    """mock LLMClient.chat：返回预置 JSON 文本，或按配置抛 LLMError。"""

    def __init__(self, reply: str | None = None, raise_error: bool = False):
        self.reply = reply or ""
        self.raise_error = raise_error
        self.calls = 0

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.calls += 1
        if self.raise_error:
            raise LLMError("boom")
        return self.reply


def _llm_json(intent: str, **overrides) -> str:
    data = {"intent": intent, "article_id": None, "tags": [], "keywords": [], "top_n": None}
    data.update(overrides)
    return json_mod.dumps(data, ensure_ascii=False)


class TestParseFeedback:
    # T-FB-01 意图 like
    async def test_intent_like(self):
        llm = FakeFeedbackLLM(_llm_json("like"))
        fb = await parse_feedback("这条不错", llm)
        assert fb.intent == "like"
        assert fb.raw == "这条不错"

    # T-FB-02 意图 expand（含标签提取）
    async def test_intent_expand_with_tags(self):
        llm = FakeFeedbackLLM(_llm_json("expand", tags=["AI硬件"], keywords=["AI硬件"]))
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"
        assert fb.tags == ["AI硬件"]

    # T-FB-03 意图 adjust
    async def test_intent_adjust(self):
        llm = FakeFeedbackLLM(_llm_json("adjust", top_n=3))
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    # T-FB-04 无 LLM 启发式兜底
    async def test_heuristic_dislike_without_llm(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("不要推游戏了", llm)
        assert fb.intent == "dislike"

    async def test_heuristic_adjust(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    async def test_heuristic_expand(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"

    async def test_heuristic_none(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("今天天气不错", llm)
        assert fb.intent == "none"

    # T-FB-12 解析失败不抛
    async def test_invalid_json_falls_back_to_heuristic(self):
        llm = FakeFeedbackLLM("这不是JSON{{{")
        fb = await parse_feedback("随便说点啥", llm)
        assert fb.intent == "none"

    def test_intents_constant(self):
        assert FEEDBACK_INTENTS == ("like", "dislike", "expand", "adjust", "none")
