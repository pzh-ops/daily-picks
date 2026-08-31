"""M11 用例：深度评分/过滤/关键词（测试文档 docs/06 §2；LLM 全部 mock 不走网络）。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from daily_picks import deep as deep_mod
from daily_picks.deep import DeepResult, deep_analyze, deep_filter
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


def _replies(scores: list[int], extra: str = "") -> list[str]:
    out = []
    for s in scores:
        # R12: brief 原 % 格式化触发 UP031（brief 自要求 ruff 零告警），改 f-string 输出等价
        out.append(f'{{"deep_score": {s}, "keywords": ["A", "B", "C"], "reason": "文中引用具体数据论证观点{extra}"}}')
    return out


class TestDeepFilter:
    # T-DEEP-05 批量过滤保高分
    async def test_keeps_high_scores(self):
        llm = FakeSeqLLM(_replies([78, 55, 30]))
        candidates = [make_scored(article_id=i, score=50.0 - i) for i in (1, 2, 3)]
        # R12（计划缺陷最小修复）：brief 原 threshold=60 会触发降阈值重试（1 < DEEP_MIN_COUNT=5 → 50 放行 55）致断言 [1] 失败；
        # 改 70 后重试仍触发（60 ≥ 0 且 1 < 5）但 55/30 依旧不过，断言与意图不变（保高分）
        filtered, results = await deep_filter(candidates, llm, threshold=70)
        assert [sa.article_id for sa in filtered] == [1]
        assert len(results) == 3          # 全量 DeepResult
        assert [r.article_id for r in results] == [1, 2, 3]  # 回填 article_id

    # T-DEEP-06 fail-open 保留失败篇（超时 mock）
    async def test_fail_open_keeps_failed(self, monkeypatch):
        monkeypatch.setattr(deep_mod, "DEEP_TIMEOUT_S", 0.01)  # 缩短超时避免测试拖慢（freezegun 冻结会挂死，勿加 frozen_now）

        class SlowLLM:
            async def chat(self, system, user, json_mode=True):
                await asyncio.sleep(0.1)  # 超过 0.01s → wait_for 超时
                return VALID_JSON

        filtered, results = await deep_filter([make_scored(article_id=1)], SlowLLM(), threshold=60)
        assert [sa.article_id for sa in filtered] == [1]  # 超时篇保留
        assert results[0].ok is False

    # T-DEEP-07 过滤后不足降阈值重试
    async def test_low_yield_lowers_threshold_once(self):
        llm = FakeSeqLLM(_replies([55, 52, 50]))
        candidates = [make_scored(article_id=i) for i in (1, 2, 3)]
        filtered, _ = await deep_filter(candidates, llm, threshold=60)
        assert [sa.article_id for sa in filtered] == [1, 2, 3]  # 阈值降为 50 后全部保留
        assert llm.calls == 3  # 不重复调用 LLM（复用首轮结果）

    # T-DEEP-08 并发上限 ≤3
    async def test_concurrency_capped_at_three(self):
        llm = FakeSeqLLM(_replies([70] * 5))
        candidates = [make_scored(article_id=i) for i in range(1, 6)]
        await deep_filter(candidates, llm, threshold=60)
        assert llm.max_active <= 3

    async def test_empty_candidates(self):
        llm = FakeSeqLLM([])
        filtered, results = await deep_filter([], llm, threshold=60)
        assert filtered == [] and results == []

    async def test_weights_passed_to_analyze(self, monkeypatch):
        seen: list[dict[str, float]] = []
        original = deep_mod.deep_analyze

        async def spy(article, llm, weights):
            seen.append(weights)
            return await original(article, llm, weights)

        monkeypatch.setattr(deep_mod, "deep_analyze", spy)
        await deep_filter([make_scored(article_id=1)], FakeSeqLLM([VALID_JSON]),
                          threshold=60, weights={"AI": 2.0})
        assert seen == [{"AI": 2.0}]
