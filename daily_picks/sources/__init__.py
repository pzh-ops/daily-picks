"""内容源适配器集合（开发文档 §4.11）。"""

from __future__ import annotations

import logging

from daily_picks.config import RootConfig
from daily_picks.sources.base import SourceAdapter, SourceError

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"

# M1 起逐个注册：{"rss": RssAdapter, "bilibili": BilibiliAdapter, ...}
_ADAPTER_REGISTRY: dict[str, type[SourceAdapter]] = {}

__all__ = ["UA", "SourceAdapter", "SourceError", "build_adapters"]


def build_adapters(cfg: RootConfig) -> list[SourceAdapter]:
    """按 cfg.sources.enabled 顺序实例化；未知名字记 WARNING 并跳过。

    M0 阶段注册表为空，返回空列表并打 WARNING（M1 填充适配器）。
    """
    adapters: list[SourceAdapter] = []
    for name in cfg.sources.enabled:
        cls = _ADAPTER_REGISTRY.get(name)
        if cls is None:
            logger.warning("内容源 %r 未注册（M0 阶段尚未实现采集适配器），已跳过", name)
            continue
        adapters.append(cls())
    return adapters
