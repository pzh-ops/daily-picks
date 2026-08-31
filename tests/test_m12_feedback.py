"""M12 用例：反馈通道/意图解析/演化落库（测试文档 docs/06 §4；LLM 全部 mock）。"""

from __future__ import annotations

import pytest

from daily_picks.feedback_channels import FeedbackChannel, HermesChannel, RawFeedback


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
