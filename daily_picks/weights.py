"""权重演化公共工具（docs/05 §4.1）：tracking 点击回写与 feedback 权重演化共用。"""

from __future__ import annotations

from daily_picks.storage import Storage


def _bump_keywords(text: str, delta: float, storage: Storage) -> list[str]:
    """text 中命中的关键词各 +delta（bump_keyword_weight 钳制 [0.2, 2.0]），返回命中列表。

    命中口径与 feedback.hit_keywords 一致：大小写不敏感子串匹配（docs/05 §4.1）。
    """
    weights = storage.get_interest_weights()
    lower = (text or "").lower()
    hits = [kw for kw in weights if kw and kw.lower() in lower]
    for kw in hits:
        storage.bump_keyword_weight(kw, delta)
    return hits
