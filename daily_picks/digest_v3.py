"""v3 深度精选推送模板（docs/04 §7 / docs/05 §2.3）。"""

from __future__ import annotations

from daily_picks.deep import DeepResult, format_keywords
from daily_picks.digest import SUMMARY_MAX_CHARS, truncate
from daily_picks.models import Article, Pick

# 内置源显示名（docs/04 §6.5）
SOURCE_DISPLAY_NAMES = {
    "hnews": "Hacker News",
    "infoq": "InfoQ",
    "juejin": "掘金",
    "bilibili": "B站",
    "zhihu": "知乎热榜",
    "rss": "RSS",
}

DIGEST_SEPARATOR = "─" * 14


def source_display_name(source_key: str) -> str:
    """内置源中文名映射；'rss:<名称>' 自定义源 → 名称（key 内嵌名，无需查 registry）；未知 → 原 key。"""
    if source_key in SOURCE_DISPLAY_NAMES:
        return SOURCE_DISPLAY_NAMES[source_key]
    if source_key.startswith("rss:"):
        return source_key[len("rss:"):]
    return source_key


def build_digest_v3(items: list[Pick], articles: dict[int, Article],
                    deep_map: dict[int, DeepResult]) -> str:
    """v3 模板（docs/04 §7）：
    📚 今日深度精选（n条）／──────────────／每条目：【来源】标题/关键词/推荐理由/链接/作者。
    deep_map 缺该条目（fail-open 保留）→ 用原摘要兜底行 `摘要：…`（docs/05 §2.3）。
    空列表返回"今日无精选内容。"提示文案。
    """
    lines = [f"📚 今日深度精选（{len(items)}条）"]
    for pick in sorted(items, key=lambda p: p.rank):
        article = articles.get(pick.article_id)
        if article is None:
            continue
        lines.append(DIGEST_SEPARATOR)
        lines.append(f"【{source_display_name(article.source)}】{article.title}")
        deep = deep_map.get(pick.article_id)
        if deep is not None and deep.ok:
            lines.append(f"关键词：{format_keywords(deep.keywords)}")
            lines.append(f"推荐理由：{deep.reason}")
        else:
            summary = truncate(article.summary, SUMMARY_MAX_CHARS)
            if summary:
                lines.append(f"摘要：{summary}")
        lines.append(f"链接：{article.url}")
        if article.author:
            lines.append(f"作者：{article.author}")
    if not items:
        lines.append("今日无精选内容。")
    return "\n".join(lines)
