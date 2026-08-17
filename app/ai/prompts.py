"""提示词模板：论文打分摘要 / 新闻筛选 / 翻译（兴趣从 bot 订阅与记忆注入）。"""
from __future__ import annotations

import json

PAPER_SCORE_SYSTEM = (
    "你是{interests}领域的资深研究者。对给定论文逐篇评分并写中文摘要。"
    "只输出 JSON 对象：{{\"results\": [{{\"id\": 序号, \"score\": 0到10的相关度, "
    "\"digest\": \"中文摘要（研究问题/方法/创新点，80字内）\", \"tags\": [\"标签\"]}}]}}。"
    "严格按给定 id 输出，不要遗漏。"
)

NEWS_SCORE_SYSTEM = (
    "你是{interests}领域的资讯编辑。对给定新闻逐条筛选并写中文一句话摘要。"
    "只输出 JSON 对象：{{\"results\": [{{\"id\": 序号, \"score\": 0到10的重要性, "
    "\"digest\": \"中文一句话摘要（40字内）\"}}]}}。"
    "营销软文、纯广告给低分。严格按给定 id 输出。"
)

TRANSLATE_SYSTEM = (
    "你是学术翻译助手，把英文论文摘要翻译成流畅的中文，保留 AI 领域术语（LLM、RAG、agent、benchmark 等）。"
    "只输出译文，不要解释。"
)


def build_batch_user(items: list[dict], kind: str) -> str:
    lines = []
    for i, it in enumerate(items):
        extra = ""
        if kind == "paper":
            extra = f" | 作者: {it.get('authors') or ''}"
        elif it.get("extra_json"):
            try:
                e = json.loads(it["extra_json"])
                extra = f" | 分数: {e.get('score', '')} 评论: {e.get('comments', '')}"
            except (json.JSONDecodeError, TypeError):
                pass
        summary = (it.get("summary") or "").replace("\n", " ")[:600]
        lines.append(
            f"id={i} | {it['title']}{extra}\n摘要: {summary or '（无）'}"
        )
    return "\n\n".join(lines)


def paper_score_system(interests: str) -> str:
    return PAPER_SCORE_SYSTEM.format(interests=interests or "学术研究")


def news_score_system(interests: str) -> str:
    return NEWS_SCORE_SYSTEM.format(interests=interests or "科技与AI")
