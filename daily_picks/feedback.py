"""偏好反馈：like/dislike → 关键词权重更新（设计文档 §10 / 开发文档 §4.16）。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from daily_picks.config import ConfigError
from daily_picks.llm import LLMClient, LLMError
from daily_picks.storage import Storage
from daily_picks.weights import _bump_keywords

logger = logging.getLogger("daily_picks.feedback")

# 权重调整步长（设计文档 §10）
LIKE_DELTA = 0.1
DISLIKE_DELTA = -0.05


class FeedbackError(Exception):
    """反馈错误（非法 kind / 文章不存在），CLI 捕获后提示并退出码 1。"""


def hit_keywords(title: str | None, summary: str | None, weights: dict[str, float]) -> list[str]:
    """title+summary 中命中的关键词（大小写不敏感子串匹配，对齐 §7.1 keyword_score）。
    匹配准则基准：weights._bump_keywords 与 like 反馈均按此口径取词。"""
    text = f"{title or ''} {summary or ''}".lower()
    return [kw for kw in weights if kw and kw.lower() in text]


# ---- v3 文字反馈解析（docs/04 §6.3 / docs/05 §3.2）----

FEEDBACK_INTENTS = ("like", "dislike", "expand", "adjust", "none")

# 反馈解析 system prompt（docs/05 §5.2 模板，原样使用）
FEEDBACK_SYSTEM_PROMPT = (
    "你是用户反馈分析师。意图定义：like=对内容满意；dislike=不满；expand=希望扩展某方向"
    "（含新标签）；adjust=调整条数；none=无法归类。"
)


@dataclass
class ParsedFeedback:
    raw: str                    # 原始反馈文字（落 feedback_text.raw_text，docs/04 §6.3 修订）
    intent: str                 # 见 FEEDBACK_INTENTS
    article_id: int | None
    tags: list[str]             # 提取的新标签（intent=expand）
    keywords: list[str]         # 提取的关键词（写 interest_weights）
    top_n: int | None           # intent=adjust 时的新条数


async def parse_feedback(raw: str, llm: LLMClient) -> ParsedFeedback:
    """LLM 解析文字反馈：意图识别 + 标签/关键词提取（docs/05 §5.2 JSON 输出）。

    失败/非法输出 → 启发式兜底（docs/05 §3.2），不抛异常。
    """
    user = (
        "用户反馈：" + raw + "\n"
        '输出 JSON：{"intent": "like|dislike|expand|adjust|none", "article_id": null, '
        '"tags": [提取的新标签，intent=expand 时], "keywords": [从中提取的兴趣关键词], '
        '"top_n": null}（top_n 仅 intent=adjust 时填数字）'
    )
    try:
        text = await llm.chat(FEEDBACK_SYSTEM_PROMPT, user, json_mode=True)
        data = json.loads(text or "{}")
    except (LLMError, ConfigError, json.JSONDecodeError, TypeError) as e:
        # ConfigError（缺 DEEPSEEK_API_KEY）也走启发式——无 LLM 时反馈仍可用（docs/05 §3.2）
        logger.warning("parse_feedback LLM 失败，使用启发式兜底: %s", e)
        return _heuristic_feedback(raw)
    if not isinstance(data, dict) or data.get("intent") not in FEEDBACK_INTENTS:
        return _heuristic_feedback(raw)
    article_id = data.get("article_id")
    top_n = data.get("top_n")
    fb = ParsedFeedback(
        raw=raw,
        intent=data["intent"],
        article_id=article_id if isinstance(article_id, int) else None,
        tags=[str(t) for t in data.get("tags", [])] if isinstance(data.get("tags"), list) else [],
        keywords=[str(k) for k in data.get("keywords", [])] if isinstance(data.get("keywords"), list) else [],
        top_n=top_n if isinstance(top_n, int) else None,
    )
    if fb.intent == "adjust" and not (fb.top_n is not None and 1 <= fb.top_n <= 10):
        fb.top_n = None  # 非法条数按 none 处理（apply 阶段跳过）
    return fb


def _heuristic_feedback(raw: str) -> ParsedFeedback:
    """无 LLM/解析失败兜底（docs/05 §3.2）：
    含"不要/少推/反感" → dislike；含"数字+条" → adjust；含"多推/喜欢/想看" → expand；否则 none。
    """

    def _fb(intent: str, top_n: int | None = None) -> ParsedFeedback:
        return ParsedFeedback(raw=raw, intent=intent, article_id=None,
                              tags=[], keywords=[], top_n=top_n)

    text = raw.strip()
    if not text:
        return _fb("none")
    if any(word in text for word in ("不要", "少推", "反感", "不喜欢")):
        return _fb("dislike")
    m = re.search(r"(\d+)\s*条", text)
    if m and 1 <= int(m.group(1)) <= 10:
        return _fb("adjust", top_n=int(m.group(1)))
    if any(word in text for word in ("多推", "喜欢", "想看")):
        return _fb("expand")
    return _fb("none")


def _apply_like_dislike(storage: Storage, article_id: int, kind: str,
                        extra_keyword: str | None = None) -> dict:
    """应用 like/dislike 反馈，返回 {'updated': [关键词...], 'article_state': 当前状态}。（原 v1 实现，2026-08-31 更名）

    规则（设计文档 §10）：
    - like：命中关键词各 +0.1（上限 2.0）；无命中且给了 extra_keyword → 该词 +0.1 并入库。
    - dislike：命中关键词各 -0.05（下限 0.2）；文章 state='dismissed'。
    - 同文章重复反馈：只保留最后一次（storage.add_feedback 先删旧反馈再插入）。
    """
    if kind not in {"like", "dislike"}:
        raise FeedbackError(f"非法反馈类型: {kind!r}（可选 like | dislike）")

    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        raise FeedbackError(f"文章 id={article_id} 不存在，无法提交反馈")

    weights = storage.get_interest_weights()
    hits = hit_keywords(rows[0]["title"], rows[0]["summary"], weights)

    if kind == "like":
        if hits:
            updated = hits
            for kw in hits:
                storage.bump_keyword_weight(kw, LIKE_DELTA)
        elif extra_keyword:
            updated = [extra_keyword]
            storage.bump_keyword_weight(extra_keyword, LIKE_DELTA)
        else:
            updated = []
    else:  # dislike
        updated = hits
        for kw in hits:
            storage.bump_keyword_weight(kw, DISLIKE_DELTA)

    storage.add_feedback(article_id, kind)

    article_state = rows[0]["state"]
    if kind == "dislike":
        storage.set_state(article_id, "dismissed")
        article_state = "dismissed"

    logger.info("反馈已应用 article_id=%s kind=%s updated=%s state=%s",
                article_id, kind, updated, article_state)
    return {"updated": updated, "article_state": article_state}


# ---- v3 文字反馈应用（docs/04 §6.3 / docs/05 §3.2；同名分派见下）----


def apply_feedback(first, second, third=True, extra_keyword=None, *, extract_keywords=None):
    """同名分派（docs/04 §6.3 修订）：
    - v3 文字反馈：apply_feedback(fb: ParsedFeedback, storage, extract_keywords=True)
    - v1 like/dislike：apply_feedback(storage, article_id, kind, extra_keyword=None)
    """
    if isinstance(first, ParsedFeedback):
        return _apply_text_feedback(
            first, second,
            extract_keywords=third if extract_keywords is None else extract_keywords,
        )
    return _apply_like_dislike(first, second, third, extra_keyword)


def _bump_article_tags(storage: Storage, article_id: int, delta: float) -> list[str]:
    """文章 title+summary 命中的标签（tag_weights 表中的）各 +delta（save_tag_weight 钳制 [0.2,2.0]）。"""
    rows = storage.get_articles_by_ids([article_id])
    if not rows:
        return []
    text = f"{rows[0]['title'] or ''} {rows[0]['summary'] or ''}".lower()
    hits: list[str] = []
    for tag, current in storage.list_tags():
        if tag and tag.lower() in text:
            storage.save_tag_weight(tag, current + delta, "feedback")
            hits.append(tag)
    return hits


def _apply_text_feedback(fb: ParsedFeedback, storage: Storage,
                         extract_keywords: bool = True) -> None:
    """落库 feedback_text + 演化（docs/04 §6.3）：
    - like/dislike + article_id：复用 v1 逻辑写 feedback 表；命中标签 ±0.1（tag_weights）
    - expand：新标签写 tag_weights(1.5, 'feedback')（已存在的保留演化值），并合并进 user_profile.tags
    - adjust：top_n 写 user_profile（保留原 tags/sources；无画像跳过）
    - extract_keywords：keywords 写 interest_weights（+0.1，bump_keyword_weight 钳制 [0.2,2.0]）
    """
    storage.add_feedback_text(raw_text=fb.raw, intent=fb.intent, article_id=fb.article_id,
                              extracted_tags=fb.tags, keywords=fb.keywords, channel="hermes")

    profile = storage.load_profile()
    existing_tags = profile["tags"] if profile else []
    existing_sources = profile["sources"] if profile else []
    existing_top_n = profile["top_n"] if profile else 5

    if fb.intent in ("like", "dislike") and fb.article_id is not None:
        try:
            _apply_like_dislike(storage, fb.article_id, fb.intent)
        except FeedbackError as e:
            logger.warning("文字反馈关联文章不存在 article_id=%s: %s", fb.article_id, e)
        _bump_article_tags(storage, fb.article_id, 0.1 if fb.intent == "like" else -0.1)
    elif fb.intent == "expand":
        existing_tag_weights = dict(storage.list_tags())
        for tag in fb.tags:
            if tag and tag not in existing_tag_weights:
                storage.save_tag_weight(tag, 1.5, "feedback")
        merged_tags = list(dict.fromkeys(existing_tags + fb.tags))
        storage.save_profile(merged_tags, existing_sources, existing_top_n)
    elif fb.intent == "adjust" and fb.top_n is not None and profile is not None:
        storage.save_profile(existing_tags, existing_sources, fb.top_n)

    if extract_keywords:
        for kw in fb.keywords:
            storage.bump_keyword_weight(kw, 0.1)


# ---- v3 权重演化（docs/04 §6.3 / docs/05 §4.1）----

CLICK_CURSOR_KEY = "last_weight_evolve_id"      # clicks.id 演化游标（meta 表）
FEEDBACK_CURSOR_KEY = "last_feedback_evolve_id"  # feedback_text.id 演化游标（meta 表）
CLICK_EVOLVE_DELTA = 0.05                        # 点击弱信号（对齐 tracking.click_delta 默认值）
EXPAND_EVOLVE_DELTA = 0.1                        # 扩展标签强化步长


def evolve_weights(storage: Storage) -> None:
    """点击/反馈驱动的权重演化（docs/04 §6.3）：
    1. clicks 增量（游标 last_weight_evolve_id）→ 文章 title+summary 关键词 +0.05
    2. feedback_text 中 intent='expand' 未处理的（游标 last_feedback_evolve_id）→ 标签关键词 +0.1
    3. 写入全部经 bump_keyword_weight 钳制 [0.2, 2.0]；游标单调推进（幂等，可反复调用）。
    """
    click_cursor = int(storage.get_meta(CLICK_CURSOR_KEY) or 0)
    for row in storage.get_clicks_since(click_cursor):
        _bump_keywords(f"{row['title'] or ''} {row['summary'] or ''}",
                       CLICK_EVOLVE_DELTA, storage)
        click_cursor = max(click_cursor, int(row["id"]))
    if click_cursor:
        storage.set_meta(CLICK_CURSOR_KEY, str(click_cursor))

    fb_cursor = int(storage.get_meta(FEEDBACK_CURSOR_KEY) or 0)
    for row in storage.get_feedback_text_since(fb_cursor):
        if row["intent"] == "expand":
            for tag in row["extracted_tags"]:
                storage.bump_keyword_weight(tag, EXPAND_EVOLVE_DELTA)
        fb_cursor = max(fb_cursor, int(row["id"]))
    if fb_cursor:
        storage.set_meta(FEEDBACK_CURSOR_KEY, str(fb_cursor))
