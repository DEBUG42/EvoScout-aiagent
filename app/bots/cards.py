"""推送消息构建：论文卡片 / 新闻 post / digest 汇总卡片。"""
from __future__ import annotations

import json


def paper_card(item: dict, bi: dict) -> dict:
    elements = []
    if bi.get("digest_zh"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": bi["digest_zh"][:500]}})
    if bi.get("score") is not None:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": f"相关度 **{bi['score']:.1f}/10**"}})
    actions = []
    if item.get("url"):
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "查看原文"},
            "type": "primary", "url": item["url"],
        })
    actions.append({
        "tag": "button", "text": {"tag": "plain_text", "content": "AI 解读"},
        "type": "default", "url": f"https://alphaxiv.org/abs/{item['external_id']}",
    })
    actions.append({
        "tag": "button", "text": {"tag": "plain_text", "content": "翻译摘要"},
        "type": "default",
        "value": {"action": "translate", "item_id": item["id"]},
    })
    elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": item["title"][:60]},
        },
        "elements": elements,
    }


def news_lines(rows: list[dict]) -> list[list[str]]:
    lines = []
    for r in rows:
        digest = r.get("digest_zh") or ""
        line = [f"• {r['title'][:80]}"]
        if digest:
            line.append(f"\n  {digest}")
        if r.get("url"):
            line.append(f"\n  {r['url']}")
        lines.append(line)
    return lines


def digest_card(bot: str, entries: list[dict]) -> dict:
    elements = []
    for e in entries[:15]:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": f"• [{e['title'][:60]}]({e['url'] or ''})"},
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "turquoise",
            "title": {"tag": "plain_text", "content": f"{bot} 24小时 Digest（{len(entries)} 条）"},
        },
        "elements": elements or [{"tag": "div", "text": {"tag": "lark_md", "content": "(无)"}}],
    }
