"""抓取源抽象：BaseSource + RawItem + 全局限速器。"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx


@dataclass
class RawItem:
    source: str
    external_id: str
    title: str
    kind: str = "news"                 # paper | news
    summary: str = ""
    url: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str = ""
    extra: dict = field(default_factory=dict)

    def to_db_dict(self) -> dict:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "authors": self.authors,
            "published_at": self.published_at,
            "extra": self.extra,
        }


class RateLimiter:
    """按 name 分桶的全局最小间隔限制（跨任务共享，如 arxiv 3s / S2 3.5s）。"""

    def __init__(self):
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, name: str, min_interval: float) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last.get(name, 0.0)
            wait = min_interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last[name] = time.monotonic()


class BaseSource(ABC):
    name: str = "base"

    def __init__(self, cfg: dict, http: httpx.Client, limiter: RateLimiter | None = None):
        self.cfg = cfg
        self.http = http
        self.limiter = limiter

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """抓取 + 规范化；只返回数据不写库。异常由调用方捕获。"""

    def healthy(self) -> bool:
        """源健康状态（Reddit 等脆弱源做降级用），默认 True。"""
        return True
