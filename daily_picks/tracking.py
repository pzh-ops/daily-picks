"""点击追踪：短链注册、点击拉取、偏好回写（设计文档 §15 / 开发文档 §4.19）。

追踪服务（Cloudflare Worker，契约见设计文档 §15.3）：
- GET  /c/{code}         → 302 重定向原始 URL，同时记录点击（公开）
- POST /api/links        → 注册 {code, url, article_id}（Bearer 鉴权）
- GET  /api/clicks?after=N → 返回 id>N 的点击事件（Bearer 鉴权）
"""

from __future__ import annotations

import logging
import secrets

logger = logging.getLogger("daily_picks.tracking")

# 短码字符表（62 进制字母数字）与长度（设计文档 §15.2）
CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CODE_LENGTH = 8
# 与 publisher 一致的网络重试（设计文档 N-003）
TRACK_RETRIES = 3
# 单页最多拉取事件数（worker 端同样限制，设计文档 §15.3）
MAX_CLICKS_PER_PAGE = 1000


class TrackingError(Exception):
    """追踪服务调用失败（HTTP 错误/响应非法/网络错误）。调用方应 fail-open（设计文档 §15.1）。"""


def gen_code(length: int = CODE_LENGTH) -> str:
    """生成随机短码（secrets 加密级随机，62 字符表）。"""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def build_tracking_url(base_url: str, code: str) -> str:
    """短链：{base 去尾斜杠}/c/{code}。"""
    return f"{base_url.rstrip('/')}/c/{code}"
