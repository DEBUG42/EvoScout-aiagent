"""源注册与按 bot 订阅匹配：全局抓取一次，逐 bot 本地过滤（省 DeepSeek 成本）。"""
from __future__ import annotations

import json

import httpx

from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.sources.alphaxiv import AlphaxivClient
from app.sources.arxiv_source import ArxivSource
from app.sources.base import BaseSource, RateLimiter
from app.sources.hackernews import HackerNewsSource
from app.sources.reddit import RedditSource
from app.sources.rss_generic import GenericRssSource
from app.sources.semantic_scholar import SemanticScholarSource


def _union_subscription(bots, key: str, default=None):
    values = []
    for b in bots:
        v = b.subscriptions.get(key)
        if isinstance(v, list):
            values.extend(v)
        elif v:
            values.append(v)
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out or default


def build_sources(
    settings: Settings,
    registry: AgentRegistry,
    http: httpx.Client,
    limiter: RateLimiter,
    kv,
) -> dict[str, BaseSource]:
    bots = registry.bots()
    src_cfg = settings.sources
    sources: dict[str, BaseSource] = {}

    arxiv_cats = _union_subscription(bots, "arxiv", [])
    arxiv_kw = _union_subscription(bots, "keywords", [])
    if src_cfg.arxiv.enabled and arxiv_cats:
        sources["arxiv"] = ArxivSource({
            "categories": arxiv_cats,
            "keywords": arxiv_kw,
            "fetch_count": src_cfg.arxiv.fetch_count,
            "min_interval_s": src_cfg.arxiv.min_interval_s,
        }, http, limiter)

    s2_queries = arxiv_kw[:3] or ["visual odometry"]
    if src_cfg.semantic_scholar.enabled:
        sources["s2"] = SemanticScholarSource({
            "queries": s2_queries,
            "min_interval_s": src_cfg.semantic_scholar.min_interval_s,
        }, http, limiter)

    if src_cfg.hackernews.enabled and any(b.subscriptions.get("hackernews") for b in bots):
        sources["hn"] = HackerNewsSource({
            "min_score": src_cfg.hackernews.min_score,
            "top_n": src_cfg.hackernews.top_n,
        }, http, limiter)

    reddit_subs = _union_subscription(bots, "reddit", [])
    if src_cfg.reddit.enabled and reddit_subs:
        sources["reddit"] = RedditSource({
            "subreddits": reddit_subs,
            "min_interval_s": src_cfg.reddit.min_interval_s,
            "max_consecutive_failures": src_cfg.reddit.max_consecutive_failures,
        }, http, limiter)

    rss_feeds = [f.model_dump() for f in src_cfg.rss.feeds]
    bot_rss_names = _union_subscription(bots, "rss", [])
    if src_cfg.rss.enabled and rss_feeds:
        feeds = [f for f in rss_feeds if not bot_rss_names or f["name"] in bot_rss_names]
        if feeds:
            sources["rss"] = GenericRssSource({"feeds": feeds}, http, limiter)

    return sources


def item_matches_bot(bot_def, item: dict) -> bool:
    """本地匹配：条目是否属于该 bot 的订阅范围。"""
    subs = bot_def.subscriptions
    source, kind = item["source"], item["kind"]
    title_summary = f"{item['title']} {item.get('summary') or ''}".lower()
    keywords = [k.lower() for k in subs.get("keywords", [])]
    if keywords and any(k in title_summary for k in keywords):
        return True
    if kind == "paper" and source == "arxiv":
        cats = subs.get("arxiv", [])
        if not cats:
            return False
        extra = json.loads(item.get("extra_json") or "{}")
        return extra.get("category") in cats
    if source == "hn":
        return bool(subs.get("hackernews"))
    if source.startswith("reddit:"):
        sub = source.split(":", 1)[1]
        return sub in subs.get("reddit", [])
    if source.startswith("rss:"):
        feed = source.split(":", 1)[1]
        return feed in subs.get("rss", [])
    return False
