"""arXiv 源：PyPI arxiv 包，按分类订阅 + 关键词本地粗筛（省 LLM token）。"""
from __future__ import annotations

import re

import httpx
from loguru import logger

from app.sources.base import BaseSource, RawItem, RateLimiter

VERSION_RE = re.compile(r"v\d+$")


class ArxivSource(BaseSource):
    name = "arxiv"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter):
        super().__init__(cfg, http, limiter)
        import arxiv

        self._client = arxiv.Client()

    def fetch(self) -> list[RawItem]:
        import arxiv

        categories = self.cfg.get("categories", [])
        fetch_count = int(self.cfg.get("fetch_count", 50))
        min_interval = float(self.cfg.get("min_interval_s", 3.0))
        keywords = [k.lower() for k in self.cfg.get("keywords", [])]
        out: list[RawItem] = []
        for cat in categories:
            if self.limiter:
                self.limiter.wait("arxiv", min_interval)
            search = arxiv.Search(
                query=f"cat:{cat}",
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
                max_results=fetch_count,
            )
            try:
                results = list(self._client.results(search))
            except Exception as e:
                logger.warning(f"arxiv {cat} 抓取失败: {e}")
                continue
            for r in results:
                text = f"{r.title} {r.summary}".lower()
                if keywords and not any(k in text for k in keywords):
                    continue
                out.append(RawItem(
                    source="arxiv",
                    external_id=VERSION_RE.sub("", r.entry_id.rsplit("/", 1)[-1]),
                    title=r.title.strip().replace("\n", " "),
                    summary=r.summary.strip().replace("\n", " "),
                    url=r.entry_id,
                    authors=[a.name for a in r.authors],
                    published_at=r.published.isoformat() if r.published else "",
                    kind="paper",
                    extra={"category": cat, "comment": getattr(r, "comment", "") or ""},
                ))
        logger.info(f"arxiv: {len(out)} 条（分类 {categories}，粗筛后）")
        return out
