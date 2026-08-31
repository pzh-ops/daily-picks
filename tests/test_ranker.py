"""排序模块测试（测试文档 §4.5 T-RANK-01~12；LLM 用 mock 对象，不走网络）。"""

from __future__ import annotations

from datetime import datetime

from daily_picks.models import Article, Pick, RankResult, ScoredArticle
from daily_picks.ranker import rank_and_pick, rule_score, select_candidates


def make_article(source: str = "rss", title: str = "AI 编程", summary: str | None = None,
                 published_at: datetime | None = None, source_key: str = "k1") -> Article:
    return Article(source=source, source_key=source_key, title=title,
                   url=f"https://example.com/{source_key}", summary=summary, published_at=published_at)


def make_scored(source: str = "rss", title: str = "AI 编程", score: float = 0.0,
                article_id: int = 1, published_at: datetime | None = None) -> ScoredArticle:
    article = make_article(source=source, title=title, published_at=published_at,
                           source_key=str(article_id))
    return ScoredArticle(article=article, score=score, article_id=article_id)


class FakeLLM:
    """mock LLMClient：直接返回预置 RankResult，不走网络（测试文档 T-RANK-10/11/12）。"""

    def __init__(self, result: RankResult):
        self.result = result

    async def rank(self, candidates, profile, top_n):
        return self.result


class TestRuleScore:
    # T-RANK-01
    def test_keyword_hit(self):
        article = make_article(title="AI 编程工具实战")
        score = rule_score(article, {"AI": 2.0}, datetime(2026, 8, 27, 8, 0))
        assert score == 2.0  # 1 次命中 × 2.0，来源/时效/反馈均为 0

    # T-RANK-02
    def test_keyword_capped_at_3_hits(self):
        article = make_article(title="AI AI AI AI AI")
        score = rule_score(article, {"AI": 2.0}, datetime(2026, 8, 27, 8, 0))
        assert score == 6.0  # 5 处命中只计 3 次

    def test_keyword_case_insensitive_and_summary(self):
        article = make_article(title="大模型 应用", summary="聊聊 大模型 落地 与 大模型 成本")
        score = rule_score(article, {"大模型": 1.5}, datetime(2026, 8, 27, 8, 0))
        assert score == 1.5 * 3  # title 1 次 + summary 2 次 = 3 次（封顶）

    # T-RANK-03
    def test_recency_within_24h(self, frozen_now):
        article = make_article(title="无关键词", published_at=datetime(2026, 8, 26, 20, 0))
        assert rule_score(article, {}, frozen_now) == 2.0

    # T-RANK-04
    def test_recency_24_to_48h(self, frozen_now):
        article = make_article(title="无关键词", published_at=datetime(2026, 8, 25, 12, 0))
        assert rule_score(article, {}, frozen_now) == 1.0

    # T-RANK-05
    def test_recency_over_48h(self, frozen_now):
        article = make_article(title="无关键词", published_at=datetime(2026, 8, 20, 8, 0))
        assert rule_score(article, {}, frozen_now) == 0.0

    # T-RANK-06
    def test_source_weight(self):
        article = make_article(title="无关键词")
        assert rule_score(article, {}, datetime(2026, 8, 27, 8, 0), source_weight=1.5) == 1.5

    # T-RANK-07
    def test_feedback_bias_like_and_dislike(self):
        article = make_article(title="无关键词")
        now = datetime(2026, 8, 27, 8, 0)
        assert rule_score(article, {}, now, feedback_kinds=["like"]) == 1.0
        assert rule_score(article, {}, now, feedback_kinds=["dislike"]) == -1.0
        assert rule_score(article, {}, now, feedback_kinds=[]) == 0.0


class TestSelectCandidates:
    # T-RANK-08
    def test_top_n_descending(self):
        scored = [make_scored(article_id=i, title=f"t{i}", score=float(51 - i)) for i in range(1, 51)]
        result = select_candidates(scored, 40)
        assert len(result) == 40
        assert [sa.score for sa in result] == [float(s) for s in range(50, 10, -1)]

    # T-RANK-09
    def test_all_zero_fallback_one_per_source(self):
        scored = [
            make_scored(source="rss", article_id=1),
            make_scored(source="rss", article_id=2),
            make_scored(source="bilibili", article_id=3),
            make_scored(source="bilibili", article_id=4),
            make_scored(source="zhihu", article_id=5),
            make_scored(source="zhihu", article_id=6),
        ]
        result = select_candidates(scored, 40)
        assert len(result) == 3
        assert {sa.article.source for sa in result} == {"rss", "bilibili", "zhihu"}

    def test_empty_list(self):
        assert select_candidates([], 40) == []

    # T-RANK-13（min_score 生效，2026-08-31 修复 D-03）
    def test_min_score_filters_below_threshold(self):
        scored = [make_scored(article_id=i, title=f"t{i}", score=float(i)) for i in range(1, 6)]
        result = select_candidates(scored, 40, min_score=3.0)
        assert [sa.article_id for sa in result] == [5, 4, 3]  # 3.0 及以上
        assert all(sa.score >= 3.0 for sa in result)

    def test_min_score_inclusive_boundary(self):
        scored = [make_scored(article_id=1, score=3.0), make_scored(article_id=2, score=2.9)]
        result = select_candidates(scored, 40, min_score=3.0)
        assert [sa.article_id for sa in result] == [1]  # == 阈值保留，< 剔除

    def test_min_score_zero_is_noop(self):
        scored = [make_scored(article_id=i, title=f"t{i}", score=float(i)) for i in range(1, 6)]
        assert len(select_candidates(scored, 40, min_score=0.0)) == 5  # 与默认行为一致

    def test_min_score_fallback_ignores_threshold(self):
        # 全部 0 分触发保底策略，即使 min_score > 0 也返回每源 1 条
        scored = [
            make_scored(source="rss", article_id=1),
            make_scored(source="zhihu", article_id=2),
        ]
        result = select_candidates(scored, 40, min_score=5.0)
        assert len(result) == 2

    def test_min_score_overfilter_returns_unfiltered(self, caplog):
        # 过滤后为空（min_score 过高）→ 回退不过滤并告警，LLM 仍有材料
        scored = [make_scored(article_id=1, score=1.0), make_scored(article_id=2, score=2.0)]
        result = select_candidates(scored, 40, min_score=10.0)
        assert len(result) == 2
        assert any("回退为不过滤" in r.message for r in caplog.records)


class TestRankAndPick:
    def _candidates(self, n: int = 12) -> list[ScoredArticle]:
        # 分数降序：article_id 越大分数越高
        return [make_scored(article_id=i, title=f"t{i}", score=float(i)) for i in range(1, n + 1)]

    # T-RANK-10
    async def test_llm_success_path(self):
        picks = [Pick(article_id=i, rank=i, reason=f"理由{i}") for i in range(1, 11)]
        llm = FakeLLM(RankResult(picks=picks, ok=True))
        result, fallback = await rank_and_pick(self._candidates(), llm, {"AI": 2.0}, 10, 12000)
        assert fallback is False
        assert result == picks

    # T-RANK-11
    async def test_llm_failure_falls_back_to_rule_score(self):
        llm = FakeLLM(RankResult(picks=[], ok=False))
        result, fallback = await rank_and_pick(self._candidates(), llm, {"AI": 2.0}, 10, 12000)
        assert fallback is True
        assert len(result) == 10
        assert result[0].article_id == 12  # 规则分最高的候选
        assert [p.rank for p in result] == list(range(1, 11))
        assert result[0].reason.startswith("规则分")

    # T-RANK-12
    async def test_llm_partial_invalid_discarded(self):
        # LLM 返回 ok=False（即使携带有部分 picks）→ 按设计降级规则处理：整体走规则分
        llm = FakeLLM(RankResult(picks=[Pick(article_id=1, rank=1, reason="残留")], ok=False,
                                 raw_text="非法输出"))
        result, fallback = await rank_and_pick(self._candidates(), llm, {"AI": 2.0}, 10, 12000)
        assert fallback is True
        assert result[0].article_id == 12  # 规则分 top1，而非 LLM 残留的 1 号
        assert all(p.reason.startswith("规则分") for p in result)

    async def test_empty_candidates_skip_llm(self):
        llm = FakeLLM(RankResult(picks=[], ok=True))
        result, fallback = await rank_and_pick([], llm, {}, 10, 12000)
        assert result == [] and fallback is False
