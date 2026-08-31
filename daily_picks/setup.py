"""v3 启动向导：标签/来源/条数 → user_profile + config.yaml（docs/04 §6.1 / docs/05 §1）。"""

from __future__ import annotations

import json
import logging

from daily_picks.config import RootConfig
from daily_picks.llm import LLMClient, LLMError
from daily_picks.storage import Storage

logger = logging.getLogger("daily_picks.setup")

# 默认标签（docs/04 §6.1，锁定）
DEFAULT_TAGS: list[str] = [
    "AI大模型", "编程开发", "创业商业", "投资经济", "人文历史", "个人成长",
]

# 默认标签 → 内置源推荐（docs/04 §6.1，锁定；key 见 §6.5）
TAG_SOURCE_MAP: dict[str, list[str]] = {
    "AI大模型": ["hnews", "infoq", "rss:机器之心"],
    "编程开发": ["juejin", "hnews", "rss:阮一峰"],
    "创业商业": ["rss:36氪", "rss:虎嗅"],
    "投资经济": ["rss:华尔街见闻", "rss:雪球"],
    "人文历史": ["rss:知乎日报", "rss:豆瓣书评"],
    "个人成长": ["rss:少数派", "rss:得到"],
}


def choose_tags(llm: LLMClient | None = None) -> list[str]:
    """展示 DEFAULT_TAGS 多选（逗号分隔序号），支持 '自定义:xxx' 追加。空回车 = 默认前 3 个。

    llm 参数为 docs/04 §6.1 锁定签名预留（本期交互选标签不调用 LLM）。
    """
    print("选择你感兴趣的主题标签（可多选）：")
    for i, tag in enumerate(DEFAULT_TAGS, start=1):
        print(f"  {i}. {tag}")
    raw = input("输入序号（逗号分隔），或 `自定义:xxx` 追加，空回车 = 默认前 3 个: ").strip()
    if not raw:
        return list(DEFAULT_TAGS[:3])
    tags: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("自定义:"):
            custom = part.split(":", 1)[1].strip()
            if custom:
                tags.append(custom)
        elif part.isdigit() and 1 <= int(part) <= len(DEFAULT_TAGS):
            tags.append(DEFAULT_TAGS[int(part) - 1])
    return tags or list(DEFAULT_TAGS[:3])  # 全部非法输入 → 回退默认前 3 个


def choose_top_n() -> int:
    """输入每日条数，默认 5，范围 1-10（越界/非法重输，docs/04 §6.1）。"""
    while True:
        raw = input("每日推送条数（1-10，回车默认 5）: ").strip()
        if not raw:
            return 5
        try:
            top_n = int(raw)
        except ValueError:
            print("请输入数字。")
            continue
        if not 1 <= top_n <= 10:
            print("条数须在 1-10 之间。")
            continue
        return top_n


async def recommend_sources(tags: list[str], llm: LLMClient | None, storage: Storage) -> list[str]:
    """tags ∩ TAG_SOURCE_MAP 的并集为推荐源；llm 非空时 _llm_recommend 补充（注册 source_registry）。

    LLM 失败 fail-open：只记 WARNING，仅用内置映射（docs/04 §10 降级表）。
    """
    sources: list[str] = []
    for tag in tags:
        for key in TAG_SOURCE_MAP.get(tag, []):
            if key not in sources:
                sources.append(key)
    if llm is not None:
        try:
            for key in await _llm_recommend(tags, llm, storage):
                if key not in sources:
                    sources.append(key)
        except LLMError as e:
            logger.warning("LLM 来源推荐失败（仅用内置映射）: %s", e)
    return sources


async def _llm_recommend(tags: list[str], llm: LLMClient, storage: Storage) -> list[str]:
    """LLM 按标签推荐信息源（prompt 见 docs/05 §5.3）；url 须以 http(s):// 开头，
    注册到 source_registry（key=`rss:<名称>`），返回注册成功的源 key 列表。"""
    system = "你是内容源推荐专家。根据用户兴趣标签推荐高质量深度内容源（RSS）。"
    user = (
        f"标签：{'、'.join(tags)}\n"
        '输出 JSON：{"sources": [{"name": "源名", "url": "https://...rss 地址"}]}（每个标签最多 3 个）'
    )
    text = await llm.chat(system, user, json_mode=True)
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as e:
        logger.warning("LLM 来源推荐输出非法 JSON: %s", e)
        return []
    if not isinstance(data, dict):
        return []
    keys: list[str] = []
    for item in data.get("sources", [])[: len(tags) * 3]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url.startswith(("http://", "https://")):
            logger.warning("LLM 推荐源被跳过（名称缺失或 url 非法）: %r", item)
            continue
        key = f"rss:{name}"
        storage.register_source(key, name, url, tags)
        keys.append(key)
    return keys
