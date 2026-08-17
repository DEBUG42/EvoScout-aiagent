"""DeepSeek 客户端：JSON mode 容错（正则提取 + 重试 + 单条降级），每日调用计数护栏。"""
from __future__ import annotations

import json
import re
from datetime import date

from loguru import logger


class AiError(Exception):
    pass


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", repo=None, max_daily_calls: int = 40):
        if not api_key:
            raise AiError("DEEPSEEK_API_KEY 未配置")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.repo = repo
        self.max_daily_calls = max_daily_calls

    # ---- 成本护栏 ----

    def _calls_key(self, bot: str) -> str:
        return f"ai_calls_{bot}_{date.today().isoformat()}"

    def can_call(self, bot: str) -> bool:
        if not self.repo:
            return True
        used = int(self.repo.kv_get(self._calls_key(bot)) or 0)
        return used < self.max_daily_calls

    def _count_call(self, bot: str) -> None:
        if not self.repo:
            return
        key = self._calls_key(bot)
        used = int(self.repo.kv_get(key) or 0)
        self.repo.kv_set(key, str(used + 1))

    def calls_used_today(self, bot: str) -> int:
        return int(self.repo.kv_get(self._calls_key(bot)) or 0) if self.repo else 0

    # ---- 调用 ----

    def chat_json(self, system: str, user: str, bot: str = "", temperature: float = 0.3) -> dict:
        """请求 JSON 输出；失败重试 1 次，仍失败抛 AiError。"""
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                content = resp.choices[0].message.content or ""
                self._count_call(bot)
                return self._extract_json(content)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning(f"DeepSeek JSON 解析失败（第 {attempt + 1} 次）: {e}")
            except Exception as e:
                last_err = e
                logger.warning(f"DeepSeek 调用失败（第 {attempt + 1} 次）: {e}")
                break  # 网络/API 错误重试意义不大
        raise AiError(f"DeepSeek JSON 调用失败: {last_err}")

    def chat_text(self, system: str, user: str, bot: str = "") -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
        self._count_call(bot)
        return resp.choices[0].message.content or ""

    @staticmethod
    def _extract_json(content: str) -> dict:
        m = _JSON_BLOCK_RE.search(content)
        if not m:
            raise json.JSONDecodeError("无 JSON 块", content, 0)
        return json.loads(m.group(0))
