"""M11 用例：深度评分/过滤/关键词（测试文档 docs/06 §2；LLM 全部 mock 不走网络）。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from daily_picks.deep import DeepResult, deep_analyze
from daily_picks.models import Article, ScoredArticle


def make_article(source: str = "rss", title: str = "AI 编程工具实战",
                 summary: str | None = "用 AI 写代码的十个技巧") -> Article:
    return Article(source=source, source_key="k1", title=title,
                   url="https://example.com/k1", summary=summary)


def make_scored(article_id: int = 1, title: str = "AI 编程工具实战",
                score: float = 10.0) -> ScoredArticle:
    return ScoredArticle(article=make_article(title=title), score=score, article_id=article_id)


class FakeSeqLLM:
    """按调用顺序返回预置 JSON 文本；记录 user 消息与并发度（T-DEEP-08 用）。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.users: list[str] = []

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            self.users.append(user)
            return self.replies[min(self.calls, len(self.replies) - 1)]
        finally:
            self.calls += 1
            self.active -= 1


VALID_JSON = ('{"deep_score": 78, "keywords": ["A", "B", "C"],'
              ' "reason": "文中用具体数据对比了三种方案，缓存实测尤其有参考价值。"}')


class TestDeepAnalyze:
    # T-DEEP-01 深度评分有效
    async def test_valid_result(self):
        llm = FakeSeqLLM([VALID_JSON])
        result = await deep_analyze(make_article(), llm, {"AI": 2.0})
        assert result.deep_score == 78
        assert result.ok is True
        assert result.keywords == ["A", "B", "C"]
        assert "AI" in llm.users[0] and result.reason  # 兴趣关键词注入 user 消息 + 输出完整

    # T-DEEP-02 评分越界回退
    async def test_score_out_of_range(self):
        llm = FakeSeqLLM(['{"deep_score": 150, "keywords": ["A", "B", "C"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    async def test_score_non_numeric(self):
        llm = FakeSeqLLM(['{"deep_score": "78", "keywords": ["A", "B", "C"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    # T-DEEP-03 关键词不足
    async def test_keywords_too_few_keeps_original(self):
        llm = FakeSeqLLM(['{"deep_score": 60, "keywords": ["A"], "reason": "r"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False
        assert result.keywords == ["A"]  # 保留原值

    # T-DEEP-04 理由含禁用词
    async def test_banned_reason_word(self):
        llm = FakeSeqLLM(['{"deep_score": 80, "keywords": ["A", "B", "C"], "reason": "本文深入浅出"}'])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False

    async def test_empty_reason_falls_back(self):
        llm = FakeSeqLLM(['{"deep_score": 80, "keywords": ["A", "B", "C"], "reason": ""}'])
        result = await deep_analyze(make_article(title="Rust 异步实战"), llm, {})
        assert result.ok is True  # 回退文案，非失败
        assert "Rust 异步实战" in result.reason

    async def test_invalid_json_returns_not_ok(self):
        llm = FakeSeqLLM(["不是JSON"])
        result = await deep_analyze(make_article(), llm, {})
        assert result.ok is False
