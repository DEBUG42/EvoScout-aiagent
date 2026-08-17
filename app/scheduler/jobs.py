"""APScheduler 任务：常规同步（30 分钟）、S2 低频补充（12 小时）、每日 digest（09:00）。"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.ai.pipeline import run_fetch_pipeline
from app.config.settings import Settings


class JobDeps:
    def __init__(self, settings: Settings, registry, repo, sources, client, memory_dir,
                 alphaxiv=None, push=None, digest_push=None):
        self.settings = settings
        self.registry = registry
        self.repo = repo
        self.sources = sources
        self.client = client
        self.memory_dir = memory_dir
        self.alphaxiv = alphaxiv
        self.push = push
        self.digest_push = digest_push


def _sync_job(deps: JobDeps) -> None:
    try:
        summaries = run_fetch_pipeline(
            deps.sources, deps.registry, deps.repo, deps.client,
            deps.settings, deps.memory_dir, deps.alphaxiv, deps.push,
        )
        logger.info(" | ".join(s.line() for s in summaries) or "sync: 无 bot")
    except Exception:
        logger.exception("sync 任务异常")


def _s2_job(deps: JobDeps) -> None:
    s2 = deps.sources.get("s2")
    if not s2:
        return
    try:
        _sync_job(JobDeps(deps.settings, deps.registry, deps.repo, {"s2": s2},
                          deps.client, deps.memory_dir, deps.alphaxiv, deps.push))
    except Exception:
        logger.exception("s2 任务异常")


def _digest_job(deps: JobDeps) -> None:
    """把过去 24h 推送过的条目打包成 digest 再发一次（M5 用卡片）。"""
    try:
        for bot in deps.registry.bots():
            entries = deps.repo.pushed_since(bot.name, 24)
            if entries and deps.digest_push:
                deps.digest_push(bot.name, entries)
    except Exception:
        logger.exception("digest 任务异常")


def _dream_job(deps: JobDeps) -> None:
    """每日整理：合并重复记忆、归档过时条目（门控，无新活动不烧调用）。"""
    import time
    from app.memory.inject import get_store
    from app.memory.manager import MemoryManager

    try:
        if not deps.client or not deps.client.can_call("master"):
            return
        store = get_store(deps.memory_dir, "master")
        sessions = store.read_sessions()
        last_lines = int(deps.repo.kv_get("dream_session_lines") or 0)
        last_ts = float(deps.repo.kv_get("dream_last_master") or 0)
        new_sessions = len(sessions) - last_lines >= 3
        recent_updates = sum(1 for m in store.list_memories() if m.age_days() <= 1)
        gated = new_sessions or (recent_updates >= 3 and time.time() - last_ts > 6 * 3600)
        if not gated:
            logger.info("[dream] 门控未通过，跳过（无新活动）")
            return
        ops = MemoryManager(deps.memory_dir, "master", deps.client).consolidate()
        deps.repo.kv_set("dream_last_master", str(time.time()))
        deps.repo.kv_set("dream_session_lines", str(len(sessions)))
        logger.info(f"[dream] master 整理 {len(ops)} 项")
    except Exception:
        logger.exception("dream 任务异常")


def build_scheduler(deps: JobDeps) -> BackgroundScheduler:
    sched = BackgroundScheduler(
        timezone=deps.settings.app.timezone,
        job_defaults={
            "misfire_grace_time": deps.settings.scheduler.misfire_grace_time,
            "coalesce": True,
            "max_instances": 1,
        },
    )
    sched.add_job(_sync_job, "interval", minutes=30, args=[deps], id="sync",
                  next_run_time=None)
    sched.add_job(_s2_job, "cron", hour="8,20", minute="23", args=[deps], id="s2")
    hour, minute = deps.settings.push.digest_time.split(":")
    sched.add_job(_digest_job, "cron", hour=int(hour), minute=int(minute),
                  args=[deps], id="digest")
    sched.add_job(_dream_job, "cron", hour=4, minute=17, args=[deps], id="dream")
    return sched
