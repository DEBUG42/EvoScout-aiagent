"""共享 httpx.Client：统一 UA、超时、重试。"""
from __future__ import annotations

import httpx
from loguru import logger

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def make_client(ua: str = DEFAULT_UA, timeout: float = 20.0) -> httpx.Client:
    transport = httpx.HTTPTransport(retries=2)
    return httpx.Client(
        headers={"User-Agent": ua, "Accept": "*/*"},
        timeout=timeout,
        transport=transport,
        follow_redirects=True,
    )


def get_text(client: httpx.Client, url: str, **kwargs) -> str | None:
    """GET 文本；4xx/5xx 返回 None（调用方决定降级）。"""
    try:
        resp = client.get(url, **kwargs)
        if resp.status_code >= 400:
            logger.debug(f"GET {url} -> {resp.status_code}")
            return None
        return resp.text
    except httpx.HTTPError as e:
        logger.warning(f"GET {url} 失败: {e}")
        return None
