"""通用 RSS 源：feedparser 统一解析（科技媒体/任意订阅）。"""
from __future__ import annotations

import time

import feedparser
import httpx
from loguru import logger

from app.sources.base import BaseSource, RawItem, RateLimiter


class GenericRssSource(BaseSource):
    name = "rss"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter | None = None):
        super().__init__(cfg, http, limiter)

    def fetch(self) -> list[RawItem]:
        feeds = self.cfg.get("feeds", [])
        out: list[RawItem] = []
        for feed_cfg in feeds:
            name = feed_cfg.get("name", "?")
            url = feed_cfg.get("url", "")
            if not url:
                continue
            try:
                resp = self.http.get(url)
                if resp.status_code >= 400:
                    logger.warning(f"rss {name} -> {resp.status_code}")
                    continue
                feed = feedparser.parse(resp.content)
            except httpx.HTTPError as e:
                logger.warning(f"rss {name} 请求失败: {e}")
                continue
            for e in feed.entries[:20]:
                link = e.get("link", "")
                title = e.get("title", "")
                if not title:
                    continue
                out.append(RawItem(
                    source=f"rss:{name}",
                    external_id=e.get("id") or link or title,
                    title=title,
                    summary=(e.get("summary") or "")[:500],
                    url=link,
                    published_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%S", e.published_parsed
                    ) if e.get("published_parsed") else "",
                    kind="news",
                    extra={"feed": name},
                ))
        logger.info(f"rss: {len(out)} 条（{len(feeds)} 个源）")
        return out
