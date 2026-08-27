"""微信 markdown 简报生成（设计文档 §8 / 开发文档 §4.14）。"""

from __future__ import annotations

from daily_picks.models import Article

# 来源 → 简报显示标签（开发文档 §4.14）
SOURCE_LABELS = {
    "rss": "RSS",
    "bilibili": "B站",
    "zhihu": "知乎",
    "juejin": "掘金",
    "hnews": "HN",
    "infoq": "InfoQ",
}

# 摘要截断字符数（设计文档 §8）
SUMMARY_MAX_CHARS = 60

# 企业微信 text 消息字节上限（设计文档 §9.1）
WECOM_MAX_BYTES = 2048


def truncate(s: str | None, n: int = SUMMARY_MAX_CHARS) -> str:
    """按字符截断：超过 n 字符截断并追加 '…'；None → ''。"""
    if s is None:
        return ""
    if len(s) <= n:
        return s
    return s[:n] + "…"


def split_digest_blocks(text: str, max_bytes: int = WECOM_MAX_BYTES) -> list[str]:
    """按完整条目分组拆分：以行首 'N. ' 为条目边界，逐行累计 UTF-8 字节，
    超 max_bytes 时另起一块；标题行并入第一块。保证每条消息条目完整（不切断半条）。"""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    lines = text.splitlines()
    blocks: list[str] = []
    cur: list[str] = []
    cur_bytes = 0
    for line in lines:
        line_bytes = len(line.encode("utf-8")) + 1  # +1 换行符
        if line_bytes > max_bytes:
            # 单行本身超限（无换行可切）：先 flush 当前块，再对该行 UTF-8 安全截断兜底
            if cur:
                blocks.append("\n".join(cur))
                cur, cur_bytes = [], 0
            blocks.append(truncate_bytes(line, max_bytes))
            continue
        if cur and cur_bytes + line_bytes > max_bytes:
            blocks.append("\n".join(cur))
            cur, cur_bytes = [], 0
        cur.append(line)
        cur_bytes += line_bytes
    if cur:
        blocks.append("\n".join(cur))
    return blocks or [""]


def truncate_bytes(s: str, n: int = WECOM_MAX_BYTES) -> str:
    """UTF-8 安全截断到 n 字节：不切断多字节字符，截断后追加 '…' 且总字节数仍 ≤ n。"""
    if len(s.encode("utf-8")) <= n:
        return s
    budget = n - len("…".encode())
    if budget <= 0:
        return ""
    raw = s.encode("utf-8")[:budget]
    while raw:
        try:
            return raw.decode("utf-8") + "…"
        except UnicodeDecodeError:
            raw = raw[:-1]  # 截断点落在多字节字符中间：回退 1 字节重试
    return "…"


def build_digest_text(items: list[tuple[int, Article, str]], run_date: str) -> str:
    """生成微信 markdown 简报（设计文档 §8）。

    items: (rank, article, reason)，按 rank 升序渲染；空列表返回"今日无精选内容"提示文案。
    """
    lines = [f"📌 今日精选 · {run_date}"]
    for rank, article, reason in sorted(items, key=lambda item: item[0]):
        label = SOURCE_LABELS.get(article.source, article.source)
        lines.append("")
        lines.append(f"{rank}. 【{label}】{article.title}")
        if article.summary:
            lines.append(f"   摘要：{truncate(article.summary, SUMMARY_MAX_CHARS)}")
        if reason:
            lines.append(f"   理由：{reason}")
        if article.author:
            lines.append(f"   作者：{article.author} ｜ 链接：{article.url}")
        else:
            lines.append(f"   链接：{article.url}")
    if not items:
        lines.append("")
        lines.append("今日无精选内容。")
    return "\n".join(lines)
