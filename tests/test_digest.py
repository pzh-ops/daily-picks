"""简报生成测试（测试文档 §4.6 T-DIGEST-01~06）。"""

from __future__ import annotations

from daily_picks.digest import SOURCE_LABELS, build_digest_text, truncate, truncate_bytes
from daily_picks.models import Article


def make_article(source: str = "juejin", title: str = "Cursor 转 Codex 大半个月，聊聊我的真实感受",
                 summary: str | None = "Cursor 让我第一次强烈感受到 AI 编程的震撼",
                 author: str | None = "深小乐",
                 url: str = "https://juejin.cn/post/7637856870833635343") -> Article:
    return Article(source=source, source_key="k", title=title, url=url,
                   author=author, summary=summary)


class TestTruncate:
    def test_short_string_unchanged(self):
        assert truncate("短摘要", 60) == "短摘要"

    # T-DIGEST-02：200 字摘要 → 60 字 + '…'
    def test_long_string_truncated_with_ellipsis(self):
        s = "字" * 200
        result = truncate(s, 60)
        assert result == "字" * 60 + "…"
        assert len(result) == 61

    def test_none_returns_empty(self):
        assert truncate(None) == ""

    def test_exact_limit_unchanged(self):
        s = "字" * 60
        assert truncate(s, 60) == s  # 恰好 60 字不加省略号


class TestTruncateBytes:
    def test_short_string_unchanged(self):
        assert truncate_bytes("hello") == "hello"

    # T-DIGEST-05：中文/emoji 长文 → 字节数 ≤2048 且无乱码
    def test_utf8_safe_within_limit(self):
        s = ("中文内容" * 500) + "😀" * 50  # 6200 字节，远超 2048
        result = truncate_bytes(s, 2048)
        assert len(result.encode("utf-8")) <= 2048
        assert result != s
        assert result.encode("utf-8").decode("utf-8") == result  # 无乱码（能完整解码）

    def test_ascii_boundary(self):
        # 截断点不落在多字节字符上：前 2045 字节全为 ASCII，追加省略号
        s = "a" * 2047 + "中" * 10
        result = truncate_bytes(s, 2048)
        assert result == "a" * 2045 + "…"

    def test_multibyte_boundary_not_split(self):
        # 截断点落在多字节字符中间：2045 字节预算，"中"(3 字节) 被回退到完整边界
        s = "中" * 1000  # 3000 字节
        result = truncate_bytes(s, 2048)
        assert result == "中" * 681 + "…"  # 681*3 + 3 = 2046 ≤ 2048
        assert len(result.encode("utf-8")) <= 2048

    def test_tiny_limit_returns_empty(self):
        # n 小于省略号字节数（3）时无空间，返回空串
        assert truncate_bytes("中文中文", 2) == ""


class TestBuildDigestText:
    # T-DIGEST-01：完整格式（标题行、序号、【来源】、链接）
    def test_full_format(self):
        items = [
            (1, make_article(), "AI 编程深度体验"),
            (2, make_article(source="bilibili", title="世界伊始", author="伊莫",
                             url="https://www.bilibili.com/video/BV1wwhG6JEnc"), "游戏定档"),
            (3, make_article(source="zhihu", title="热榜话题", summary=None, author=None,
                             url="https://www.zhihu.com/questions/1"), None),
        ]
        text = build_digest_text(items, "2026-08-27")
        assert "📌 今日精选 · 2026-08-27" in text
        assert "1. 【掘金】Cursor 转 Codex 大半个月，聊聊我的真实感受" in text
        assert "2. 【B站】世界伊始" in text
        assert "3. 【知乎】热榜话题" in text
        assert "作者：深小乐 ｜ 链接：https://juejin.cn/post/7637856870833635343" in text
        assert "   链接：https://www.zhihu.com/questions/1" in text
        assert text.count("作者：") == 2

    # T-DIGEST-03：来源标签映射
    def test_source_labels(self):
        expected = {"rss": "RSS", "bilibili": "B站", "zhihu": "知乎", "juejin": "掘金"}
        for source, label in expected.items():
            text = build_digest_text([(1, make_article(source=source, title="t"), None)], "2026-08-27")
            assert f"【{label}】t" in text
        assert SOURCE_LABELS["hnews"] == "HN"
        assert SOURCE_LABELS["infoq"] == "InfoQ"

    # T-DIGEST-04：空列表 → "今日无精选内容"提示
    def test_empty_list(self):
        text = build_digest_text([], "2026-08-27")
        assert "今日无精选内容" in text
        assert "📌 今日精选 · 2026-08-27" in text

    # T-DIGEST-06：reason 为 None → 不崩溃，该行省略
    def test_reason_none_omitted(self):
        items = [
            (1, make_article(), "有理由"),
            (2, make_article(source="bilibili", title="无理由"), None),
        ]
        text = build_digest_text(items, "2026-08-27")
        assert "理由：有理由" in text
        assert text.count("理由：") == 1

    def test_summary_truncated_to_60_chars(self):
        text = build_digest_text([(1, make_article(summary="字" * 200), None)], "2026-08-27")
        assert f"摘要：{'字' * 60}…" in text
        assert "字" * 61 not in text

    def test_summary_none_omitted(self):
        text = build_digest_text([(1, make_article(summary=None), None)], "2026-08-27")
        assert "摘要：" not in text

    def test_rank_sorted_ascending(self):
        items = [
            (3, make_article(title="c"), None),
            (1, make_article(title="a"), None),
            (2, make_article(title="b"), None),
        ]
        text = build_digest_text(items, "2026-08-27")
        assert text.index("1. 【") < text.index("2. 【") < text.index("3. 【")

    def test_unknown_source_falls_back_to_name(self):
        text = build_digest_text([(1, make_article(source="foo", title="t"), None)], "2026-08-27")
        assert "【foo】t" in text
