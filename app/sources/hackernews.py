"""Hacker News 源：官方 Firebase API（免 key），topstories + score 过滤。"""
from __future__ import annotations

import json

import httpx
from loguru import logger

from app.sources.base import BaseSource, RawItem, RateLimiter

API = "https://hacker-news.firebaseio.com/v0"


class HackerNewsSource(BaseSource):
    name = "hackernews"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter | None = None):
        super().__init__(cfg, http, limiter)

    def _get_json(self, path: str) -> dict | list | None:
        try:
            resp = self.http.get(f"{API}/{path}")
            if resp.status_code >= 400:
                return None
            return resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.warning(f"hn {path} 失败: {e}")
            return None

    def fetch(self) -> list[RawItem]:
        min_score = int(self.cfg.get("min_score", 150))
        top_n = int(self.cfg.get("top_n", 30))
        ids = self._get_json("topstories.json")
        if not isinstance(ids, list):
            return []
        out: list[RawItem] = []
        for story_id in ids[: max(top_n * 3, 100)]:
            item = self._get_json(f"item/{story_id}.json")
            if not isinstance(item, dict) or item.get("type") != "story":
                continue
            if (item.get("score") or 0) < min_score:
                continue
            out.append(RawItem(
                source="hn",
                external_id=str(story_id),
                title=item.get("title", ""),
                summary=item.get("text") or "",
                url=item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                published_at="",
                kind="news",
                extra={
                    "score": item.get("score", 0),
                    "comments": item.get("descendants", 0),
                    "by": item.get("by", ""),
                },
            ))
            if len(out) >= top_n:
                break
        logger.info(f"hn: {len(out)} 条（score>={min_score}）")
        return out
