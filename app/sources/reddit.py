"""Reddit 源：子版块 .rss（2026-05 起 .json 已封，.rss 可能下线 → 连续失败自动禁用）。"""
from __future__ import annotations

import time

import feedparser
import httpx
from loguru import logger

from app.sources.base import BaseSource, RawItem, RateLimiter


class RedditSource(BaseSource):
    name = "reddit"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter | None = None):
        super().__init__(cfg, http, limiter)
        self._fail_streak = 0
        self._disabled = False

    def fetch(self) -> list[RawItem]:
        if self._disabled:
            return []
        subreddits = self.cfg.get("subreddits", [])
        min_interval = float(self.cfg.get("min_interval_s", 2.0))
        out: list[RawItem] = []
        ok = False
        for sub in subreddits:
            if self.limiter:
                self.limiter.wait("reddit", min_interval)
            url = f"https://www.reddit.com/r/{sub}/.rss"
            try:
                resp = self.http.get(url)
                if resp.status_code >= 400:
                    logger.warning(f"reddit r/{sub} -> {resp.status_code}")
                    continue
                feed = feedparser.parse(resp.content)
                for e in feed.entries[:20]:
                    link = e.get("link", "")
                    title = e.get("title", "")
                    if not link or not title:
                        continue
                    out.append(RawItem(
                        source=f"reddit:{sub}",
                        external_id=link,
                        title=title,
                        summary="",
                        url=link,
                        published_at=time.strftime(
                            "%Y-%m-%dT%H:%M:%S", e.published_parsed
                        ) if e.get("published_parsed") else "",
                        kind="news",
                        extra={"subreddit": sub, "score": 0},
                    ))
                ok = True
            except httpx.HTTPError as e:
                logger.warning(f"reddit r/{sub} 请求失败: {e}")
        if ok:
            self._fail_streak = 0
        else:
            self._fail_streak += 1
            max_fail = int(self.cfg.get("max_consecutive_failures", 5))
            if self._fail_streak >= max_fail:
                self._disabled = True
                logger.error(f"reddit 连续失败 {self._fail_streak} 次，自动禁用（发告警由调用方处理）")
        logger.info(f"reddit: {len(out)} 条")
        return out

    def healthy(self) -> bool:
        return not self._disabled

    def re_enable(self) -> None:
        self._disabled = False
        self._fail_streak = 0
