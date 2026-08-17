"""alphaxiv 解读：https://alphaxiv.org/overview/{arxiv_id}.md（免认证，需浏览器 UA）。

404 = 尚未生成 → 记时间戳，7 天后才重试；成功内容本地缓存。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
from loguru import logger

from app.sources.base import RateLimiter


class AlphaxivClient:
    def __init__(
        self,
        cfg: dict,
        http: httpx.Client,
        cache_dir: Path,
        kv: object,                      # Repo（kv_get/kv_set）
        limiter: RateLimiter | None = None,
    ):
        self.cfg = cfg
        self.http = http
        self.cache_dir = cache_dir / "alphaxiv"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.kv = kv
        self.limiter = limiter

    def _404_ts_key(self, arxiv_id: str) -> str:
        return f"alphaxiv_404_{arxiv_id}"

    def get_overview(self, arxiv_id: str) -> str | None:
        """返回解读 markdown；未生成/不可用返回 None。"""
        cache_file = self.cache_dir / f"{arxiv_id}.md"
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")
        # 近期 404 过的不重试
        ts = self.kv.kv_get(self._404_ts_key(arxiv_id))
        if ts:
            last = datetime.fromisoformat(ts)
            retry_days = int(self.cfg.get("retry_404_after_days", 7))
            if datetime.now() - last < timedelta(days=retry_days):
                return None
        if self.limiter:
            self.limiter.wait("alphaxiv", float(self.cfg.get("min_interval_s", 2.0)))
        try:
            resp = self.http.get(f"https://alphaxiv.org/overview/{arxiv_id}.md")
        except httpx.HTTPError as e:
            logger.warning(f"alphaxiv {arxiv_id} 请求失败: {e}")
            return None
        if resp.status_code == 404:
            self.kv.kv_set(self._404_ts_key(arxiv_id), datetime.now().isoformat(timespec="seconds"))
            logger.debug(f"alphaxiv {arxiv_id}: 404（解读未生成）")
            return None
        if resp.status_code >= 400:
            logger.warning(f"alphaxiv {arxiv_id} -> {resp.status_code}")
            return None
        text = resp.text.strip()
        if text:
            cache_file.write_text(text, encoding="utf-8")
        return text or None
