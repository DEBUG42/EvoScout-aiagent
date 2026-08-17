"""飞书纯文本消息的 markdown 清洗：text 消息不渲染 markdown，需转成易读纯文本。"""
from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`\n]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HR_RE = re.compile(r"^\s{0,3}(-{3,}|\*{3,})\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)
_NUM_RE = re.compile(r"^(\s*\d+[.)])\s+", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """把常见 markdown 转成手机端易读的纯文本（保留换行与列表结构）。"""
    if not text:
        return text
    t = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    t = _BOLD_RE.sub(r"\1", t)
    t = _ITALIC_RE.sub(r"\1", t)
    t = _CODE_RE.sub(r"\1", t)
    t = _HEADING_RE.sub("", t)
    t = _HR_RE.sub("─────", t)
    t = _BULLET_RE.sub(r"\1• ", t)
    t = _NUM_RE.sub(r"\1 ", t)
    t = _QUOTE_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
