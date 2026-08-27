"""内容源适配器集合（开发文档 §4.11）。"""

from __future__ import annotations

import logging

from daily_picks.config import RootConfig
from daily_picks.sources.base import SourceAdapter, SourceError

logger = logging.getLogger(__name__)

# 共享 UA（开发文档 §4.5：放本模块共享）。
# 注意：必须在适配器导入之前定义——各适配器 `from daily_picks.sources import UA`，
# 而本模块又导入它们，形成部分初始化导入，UA 先行定义才能解析成功。
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"

from daily_picks.sources.bilibili import BilibiliAdapter
from daily_picks.sources.hnews import HNewsAdapter
from daily_picks.sources.infoq import InfoQAdapter
from daily_picks.sources.juejin import JuejinAdapter
from daily_picks.sources.rss import RssAdapter
from daily_picks.sources.zhihu import ZhihuAdapter

_ADAPTER_REGISTRY: dict[str, type[SourceAdapter]] = {
    "rss": RssAdapter,
    "bilibili": BilibiliAdapter,
    "zhihu": ZhihuAdapter,
    "juejin": JuejinAdapter,
    "hnews": HNewsAdapter,
    "infoq": InfoQAdapter,
}

__all__ = [
    "UA",
    "BilibiliAdapter",
    "HNewsAdapter",
    "InfoQAdapter",
    "JuejinAdapter",
    "RssAdapter",
    "SourceAdapter",
    "SourceError",
    "ZhihuAdapter",
    "build_adapters",
]


def build_adapters(cfg: RootConfig) -> list[SourceAdapter]:
    """按 cfg.sources.enabled 顺序实例化；未知名字记 WARNING 并跳过。"""
    adapters: list[SourceAdapter] = []
    for name in cfg.sources.enabled:
        cls = _ADAPTER_REGISTRY.get(name)
        if cls is None:
            logger.warning("内容源 %r 未注册，已跳过", name)
            continue
        adapters.append(cls())
    return adapters
