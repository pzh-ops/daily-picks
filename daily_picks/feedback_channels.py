"""反馈通道抽象（docs/04 §8）：Hermes 为 CLI 直写占位，后期企微回调复用同一演化链路。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RawFeedback:
    text: str
    article_id: int | None = None
    channel: str = "hermes"


class FeedbackChannel(ABC):
    name: str = ""

    @abstractmethod
    async def receive(self) -> list[RawFeedback]:
        """拉取未处理反馈。"""

    @abstractmethod
    async def acknowledge(self, fb_id: str) -> None:
        """标记已处理。"""


class HermesChannel(FeedbackChannel):
    """Hermes 通道：v3 仅通过 CLI 直写 feedback_text，零部署（docs/05 §3.1）。"""

    name = "hermes"

    async def receive(self) -> list[RawFeedback]:
        return []

    async def acknowledge(self, fb_id: str) -> None:
        return None
