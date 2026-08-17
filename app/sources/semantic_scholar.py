"""Semantic Scholar 源：/paper/search 按关键词补充检索（无 key 共享池，每日 2 次低频）。"""
from __future__ import annotations

import time

import httpx
from loguru import logger

from app.sources.base import BaseSource, RawItem, RateLimiter

API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,abstract,url,authors,year,publicationDate,externalIds"


class SemanticScholarSource(BaseSource):
    name = "s2"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter | None = None):
        super().__init__(cfg, http, limiter)

    def fetch(self) -> list[RawItem]:
        queries = self.cfg.get("queries", [])
        if isinstance(queries, str):
            queries = [queries]
        min_interval = float(self.cfg.get("min_interval_s", 3.5))
        out: list[RawItem] = []
        for q in queries:
            if self.limiter:
                self.limiter.wait("s2", min_interval)
            try:
                resp = self.http.get(
                    f"{API}/paper/search",
                    params={
                        "query": q,
                        "limit": 20,
                        "sort": "publicationDate:desc",
                        "fields": FIELDS,
                    },
                )
                if resp.status_code >= 400:
                    logger.warning(f"s2 查询 {q!r} -> {resp.status_code}: {resp.text[:200]}")
                    continue
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(f"s2 查询 {q!r} 失败: {e}")
                continue
            for p in data.get("data", []):
                arxiv_id = (p.get("externalIds") or {}).get("ArXiv")
                if not arxiv_id:
                    continue
                out.append(RawItem(
                    source="s2",
                    external_id=arxiv_id,
                    title=p.get("title") or "",
                    summary=(p.get("abstract") or "")[:800],
                    url=p.get("url") or f"https://arxiv.org/abs/{arxiv_id}",
                    authors=[a.get("name", "") for a in p.get("authors", [])],
                    published_at=p.get("publicationDate") or "",
                    kind="paper",
                    extra={"query": q, "year": p.get("year")},
                ))
        logger.info(f"s2: {len(out)} 条（{len(queries)} 个查询）")
        return out
