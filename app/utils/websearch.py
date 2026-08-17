"""联网搜索：Bing HTML 解析（国内可直连、免 key）。

返回 [{title, url, snippet}]；失败返回 []（调用方降级，不抛出）。
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote_plus

import httpx
from loguru import logger

_BING_URL = "https://cn.bing.com/search"
_BLOCK_SPLIT = '<li class="b_algo"'
_H2_LINK_RE = re.compile(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_P_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(html_text: str) -> str:
    return unescape(_TAG_RE.sub("", html_text)).strip()


def web_search(query: str, max_results: int = 8, http: httpx.Client | None = None,
               timeout: float = 15.0) -> list[dict]:
    if not query.strip():
        return []
    own_client = http is None
    if own_client:
        from app.utils.http import make_client
        http = make_client()
    try:
        resp = http.get(
            _BING_URL,
            params={"q": query, "setlang": "zh-CN", "count": max_results},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            logger.warning(f"Bing 搜索 {resp.status_code}: {query[:40]}")
            return []
        html = resp.text
    except httpx.HTTPError as e:
        logger.warning(f"Bing 搜索请求失败: {e}")
        return []
    finally:
        if own_client:
            http.close()

    results: list[dict] = []
    for block in html.split(_BLOCK_SPLIT)[1:]:     # 逐块解析，避免跨块越界
        link_m = _H2_LINK_RE.search(block)
        if not link_m:
            continue
        url, title = link_m.group(1), _clean(link_m.group(2))
        if not url.startswith("http") or not title:
            continue
        snippet_m = _P_RE.search(block)
        snippet = _clean(snippet_m.group(1)) if snippet_m else ""
        results.append({"title": title[:120], "url": url, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    logger.info(f"web_search: {query[:40]!r} -> {len(results)} 条")
    return results


def web_search_text(query: str, max_results: int = 8) -> str:
    """格式化为工具返回文本。"""
    results = web_search(query, max_results)
    if not results:
        return f"未搜索到结果：{query}（网络或解析失败）"
    lines = [f"搜索「{query}」结果："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)
