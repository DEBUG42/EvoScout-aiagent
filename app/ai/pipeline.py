"""信息管线：全局抓取入库 → 逐 bot 本地匹配 → DeepSeek 打分摘要 → 推送。

三层去重：抓取层 UNIQUE(source, external_id)、处理层 bot_items 状态、推送层 pushed_log。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

from app.ai.client import AiError, DeepSeekClient
from app.ai.prompts import build_batch_user, news_score_system, paper_score_system
from app.memory.inject import get_store
from app.sources.registry import item_matches_bot


@dataclass
class PipelineSummary:
    bot: str
    ingested: int = 0
    processed: int = 0
    ready: int = 0
    skipped: int = 0
    failed: int = 0
    pushed: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"[{self.bot}] 入库 {self.ingested} 处理 {self.processed} "
            f"ready {self.ready} 跳过 {self.skipped} 推送 {self.pushed} 失败 {self.failed}"
        )


PushCallback = Callable[[str, dict, dict], str | None]  # (bot, item, bot_item) -> message_id


def fetch_and_ingest(sources: dict, registry, repo) -> dict[str, int]:
    """全部源抓取一轮，去重入库，为每个 bot 匹配建立 bot_items。返回 {source: 新增数}。"""
    counts: dict[str, int] = {}
    for name, source in sources.items():
        try:
            raw_items = source.fetch()
        except Exception as e:
            logger.exception(f"源 {name} 抓取异常")
            counts[name] = -1
            continue
        if not raw_items:
            counts[name] = 0
            continue
        new_ids = repo.insert_items([r.to_db_dict() for r in raw_items])
        counts[name] = len(new_ids)
        if new_ids:
            item_rows = repo.get_items(new_ids)
            for bot in registry.bots():
                matched = [r["id"] for r in item_rows if item_matches_bot(bot, r)]
                if matched:
                    repo.create_bot_items(bot.name, matched)
        if not source.healthy():
            logger.error(f"源 {name} 已不健康（自动禁用），需告警上报")
    return counts


def _interests_of(bot_name: str, bot_def, memory_dir: Path, fallback: str) -> str:
    parts = [fallback]
    subs = bot_def.subscriptions
    if subs.get("keywords"):
        parts.append("关键词: " + ", ".join(map(str, subs["keywords"])))
    memory_prompt = get_store(memory_dir, bot_name).build_prompt(max_entries=3)
    if memory_prompt:
        parts.append(memory_prompt[:800])
    return "\n".join(parts)


def _score_batch(repo, client, bot_name, bot_def, settings, memory_dir,
                 new_rows, summary: PipelineSummary) -> None:
    """对 status='new' 的条目分批 DeepSeek 打分，更新 bot_items 状态。"""
    ai_cfg = settings.ai
    batch_size = ai_cfg.batch_size
    for start in range(0, len(new_rows), batch_size):
        if not client.can_call(bot_name):
            logger.warning(f"{bot_name}: 调用预算用尽，剩余 {len(new_rows) - start} 条待处理")
            break
        batch = new_rows[start:start + batch_size]
        kinds = {r["kind"] for r in batch}
        kind = "paper" if kinds == {"paper"} else ("news" if kinds == {"news"} else "mixed")
        system = paper_score_system(_interests_of(bot_name, bot_def, memory_dir, ai_cfg.interests)) \
            if kind == "paper" else news_score_system(_interests_of(bot_name, bot_def, memory_dir, ai_cfg.interests))
        try:
            data = client.chat_json(system, build_batch_user(batch, kind), bot=bot_name)
        except AiError as e:
            summary.failed += len(batch)
            summary.errors.append(str(e))
            for r in batch:
                repo.update_bot_item(r["id"], status="failed", retry_count=r["retry_count"] + 1)
            continue

        results = data.get("results", []) if isinstance(data, dict) else []
        by_id = {str(res.get("id")): res for res in results if isinstance(res, dict)}
        for idx, row in enumerate(batch):
            res = by_id.get(str(idx))
            summary.processed += 1
            if not res or not isinstance(res.get("score"), (int, float)):
                # 单条解析失败：降级为原文截断摘要，不推送
                summary.failed += 1
                repo.update_bot_item(
                    row["id"], status="failed", retry_count=row["retry_count"] + 1,
                    digest_zh=(row.get("summary") or row["title"])[:200],
                )
                continue
            score = float(res["score"])
            digest = str(res.get("digest", ""))[:300]
            tags = json.dumps(res.get("tags", []), ensure_ascii=False)
            if score >= ai_cfg.min_relevance:
                repo.update_bot_item(row["id"], score=score, digest_zh=digest, tags=tags, status="ready")
                summary.ready += 1
            else:
                repo.update_bot_item(row["id"], score=score, digest_zh=digest, tags=tags, status="skipped")
                summary.skipped += 1


def process_bot(
    bot_name: str,
    bot_def,
    repo,
    client: DeepSeekClient,
    settings,
    memory_dir: Path,
    alphaxiv=None,
    push: PushCallback | None = None,
    limit: int | None = None,
) -> PipelineSummary:
    summary = PipelineSummary(bot=bot_name)
    if client is None:
        logger.warning(f"{bot_name}: DeepSeek 客户端未配置，跳过 LLM 处理（仅入库）")
        return summary
    ai_cfg = settings.ai
    new_rows = repo.get_bot_items(bot_name, status="new", limit=limit or 200)

    if new_rows:
        if not client.can_call(bot_name):
            logger.warning(f"{bot_name}: 今日 LLM 调用已达上限 {client.max_daily_calls}，跳过处理")
            return summary
        _score_batch(repo, client, bot_name, bot_def, settings, memory_dir, new_rows, summary)

    # alphaxiv 解读（ready 的论文）
    if alphaxiv:
        for r in repo.get_bot_items(bot_name, status="ready", limit=50):
            if r["kind"] != "paper" or r["source"] not in ("arxiv", "s2"):
                continue
            arxiv_id = r["external_id"]
            try:
                md = alphaxiv.get_overview(arxiv_id)
                if md:
                    repo.update_bot_item(r["id"], alphaxiv_md=md[:600])
            except Exception:
                logger.exception(f"alphaxiv {arxiv_id} 异常")

    # 即时推送（score >= instant_threshold）
    if push and ai_cfg.instant_push:
        for r in repo.get_bot_items(bot_name, status="ready", limit=50):
            if r["score"] is not None and r["score"] >= ai_cfg.instant_threshold \
                    and not repo.is_pushed(bot_name, r["item_id"]):
                item = repo.get_item(r["item_id"])
                if not item:
                    continue
                try:
                    msg_id = push(bot_name, item, r)
                    repo.log_push(bot_name, r["item_id"], "lark", msg_id, "instant")
                    repo.update_bot_item(r["id"], status="pushed")
                    summary.pushed += 1
                except Exception as e:
                    logger.warning(f"{bot_name} 推送失败: {e}（下轮重试）")
    logger.info(summary.line())
    return summary


def run_fetch_pipeline(
    sources: dict, registry, repo, client, settings, memory_dir, alphaxiv=None,
    push: PushCallback | None = None,
) -> list[PipelineSummary]:
    """一次完整同步：抓取 + 全部 bot 处理。"""
    fetch_and_ingest(sources, registry, repo)
    summaries = []
    for bot in registry.bots():
        summaries.append(process_bot(
            bot.name, bot, repo, client, settings, memory_dir, alphaxiv, push
        ))
    return summaries
