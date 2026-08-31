"""v3 推送模板用例（测试文档 docs/06 §3 T-DG3-01~04）。"""

from __future__ import annotations

from daily_picks.deep import DeepResult
from daily_picks.digest_v3 import build_digest_v3, source_display_name
from daily_picks.models import Article, Pick


def make_article(title: str = "AI 编程工具实战", author: str | None = "作者甲",
                 summary: str | None = "用 AI 写代码的十个技巧，从零到一") -> Article:
    return Article(source="hnews", source_key="k1", title=title,
                   url="https://example.com/1", author=author, summary=summary)


def make_deep(article_id: int = 1, score: int = 78) -> DeepResult:
    return DeepResult(article_id=article_id, deep_score=score,
                      keywords=["AI", "大模型", "工具链"],
                      reason="文章用具体数据对比了三种方案的落地成本，缓存实测尤其有参考价值。",
                      ok=True)


class TestBuildDigestV3:
    def _items(self, n: int = 2) -> list[Pick]:
        return [Pick(article_id=i, rank=i, reason=f"理由{i}") for i in range(1, n + 1)]

    def _articles(self, n: int = 2) -> dict[int, Article]:
        return {i: make_article(title=f"深度文章{i}") for i in range(1, n + 1)}

    # T-DG3-01 完整条目格式
    def test_full_entry_format(self):
        text = build_digest_v3(self._items(2), self._articles(2),
                               {1: make_deep(1), 2: make_deep(2)})
        assert "【Hacker News】深度文章1" in text
        assert "关键词：AI、大模型、工具链" in text
        assert "推荐理由：文章用具体数据" in text
        assert "链接：https://example.com/1" in text
        assert "作者：作者甲" in text

    # T-DG3-02 条数头部
    def test_header_count(self):
        text = build_digest_v3(self._items(2), self._articles(2),
                               {1: make_deep(1), 2: make_deep(2)})
        assert text.startswith("📚 今日深度精选（2条）")

    # T-DG3-03 作者行
    def test_author_line_omitted_when_empty(self):
        articles = {1: make_article(author=None)}
        text = build_digest_v3(self._items(1), articles, {1: make_deep(1)})
        assert "作者：" not in text
        assert "链接：" in text

    # T-DEEP-10 模板兜底摘要（deep_map 缺该 article_id）
    def test_missing_deep_falls_back_to_summary(self):
        text = build_digest_v3(self._items(1), self._articles(1), {})
        assert "摘要：" in text
        assert "关键词：" not in text

    def test_fail_open_entry_uses_summary(self):
        bad = DeepResult(article_id=1, deep_score=0, keywords=[], reason="", ok=False)
        text = build_digest_v3(self._items(1), self._articles(1), {1: bad})
        assert "摘要：" in text

    def test_empty_items_prompt(self):
        text = build_digest_v3([], {}, {})
        assert "今日无精选内容" in text


class TestSourceDisplayName:
    # T-DG3-04 source_display_name
    def test_builtin_names(self):
        assert source_display_name("hnews") == "Hacker News"
        assert source_display_name("infoq") == "InfoQ"
        assert source_display_name("juejin") == "掘金"
        assert source_display_name("bilibili") == "B站"
        assert source_display_name("zhihu") == "知乎热榜"

    def test_rss_custom_name(self):
        assert source_display_name("rss:机器之心") == "机器之心"
        assert source_display_name("rss") == "RSS"

    def test_unknown_key_falls_back_to_key(self):
        assert source_display_name("unknown") == "unknown"
