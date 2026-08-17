"""模型层：Provider 抽象（Record[Provider, ModelId] 映射 + env 开关，复刻 Claude Code utils/model 模式）。

- DeepSeekProvider：主循环模型（OpenAI 兼容 API，支持 function calling）
- ClaudeApiProvider：可选备用（ANTHROPIC_API_KEY 存在时启用）
- CodexCliProvider：外部 CLI 子代理（codex exec 非交互模式，不支持工具参数）
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Response:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ProviderError(Exception):
    pass


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None, model: str) -> Response:
        ...


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": t}
        for t in tools
    ]


class DeepSeekProvider(Provider):
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        if not api_key:
            raise ProviderError("DEEPSEEK_API_KEY 未配置")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(self, messages: list[dict], tools: list[dict] | None, model: str) -> Response:
        kwargs: dict = dict(model=model, messages=messages, temperature=0.3)
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise ProviderError(f"DeepSeek 调用失败: {e}") from e
        choice = resp.choices[0]
        tool_calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = dict(resp.usage) if resp.usage else {}
        return Response(text=choice.message.content, tool_calls=tool_calls, usage=usage)


class ClaudeApiProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY 未配置")
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None, model: str) -> Response:
        kwargs: dict = dict(model=model, messages=messages, max_tokens=4096)
        if tools:
            kwargs["tools"] = tools  # Anthropic 原生格式
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            raise ProviderError(f"Claude 调用失败: {e}") from e
        tool_calls = [
            ToolCall(id=b.id, name=b.name, arguments=(b.input or {}))
            for b in (resp.content or [])
            if getattr(b, "type", "") == "tool_use"
        ]
        text = "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")
        return Response(text=text or None, tool_calls=tool_calls)


class CodexCliProvider:
    """外部 AI CLI 子代理：子进程调用 codex exec，输出落盘，不支持工具循环。

    用途：主控把编码任务交给 Codex（Tier 3），而非作为主循环 provider。
    """

    name = "codex"

    def __init__(self, cli_path: str = "codex", default_timeout: int = 600):
        self.cli_path = cli_path
        self.default_timeout = default_timeout

    @staticmethod
    def _resolve_invocation(cli_path: str) -> list[str]:
        """npm 全局安装的 CLI 是 .cmd 包装（Windows Popen 不能直接执行），
        解析为 [node, .../bin/xxx.js]；找不到包装则原样返回。"""
        import re
        import shutil
        import sys

        found = shutil.which(cli_path)
        if not found:
            return [cli_path]
        if sys.platform == "win32" and found.lower().endswith(".cmd"):
            try:
                content = Path(found).read_text(encoding="utf-8", errors="replace")
                m = re.search(r"node_modules[\\/].+?\.js", content)
                if m:
                    js = (Path(found).parent / m.group(0).replace("\\", "/")).resolve()
                    if js.exists():
                        return ["node", str(js)]
            except OSError:
                pass
        return [found]

    def run_task(
        self,
        prompt: str,
        workdir: Path,
        timeout: int | None = None,
        model: str | None = None,
        output_file: Path | None = None,
    ) -> "ProcResult":  # noqa: F821
        from app.core.subprocess import run_command

        cmd = self._resolve_invocation(self.cli_path) + [
            "exec", "--json", "--skip-git-repo-check", prompt
        ]
        if model:
            cmd[1:1] = ["-m", model]
        return run_command(
            cmd, cwd=workdir, timeout=timeout or self.default_timeout, output_file=output_file
        )

    @staticmethod
    def parse_output(stdout: str) -> str:
        """codex exec --json 输出为 JSONL 事件流；从尾部找 agent_message 的最终回复。

        解析失败降级返回原文尾部。
        """
        lines = [l for l in stdout.splitlines() if l.strip()]
        for line in reversed(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(data, dict)
                and data.get("type") == "item.completed"
                and isinstance(data.get("item"), dict)
                and data["item"].get("type") == "agent_message"
            ):
                return data["item"].get("text", "")
        if lines:
            try:
                data = json.loads(lines[-1])
                if isinstance(data, dict) and "text" in data:
                    return data["text"]
            except json.JSONDecodeError:
                pass
        return stdout


def build_providers() -> dict[str, Provider]:
    """按环境变量构建可用 provider 映射（Claude Code env 开关模式）。"""
    providers: dict[str, Provider] = {}
    if os.environ.get("DEEPSEEK_API_KEY"):
        providers["deepseek"] = DeepSeekProvider(os.environ["DEEPSEEK_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers["claude"] = ClaudeApiProvider(os.environ["ANTHROPIC_API_KEY"])
    if not providers:
        raise ProviderError("未配置任何模型 API key（DEEPSEEK_API_KEY / ANTHROPIC_API_KEY）")
    logger.info(f"可用模型 providers: {sorted(providers)}")
    return providers


def resolve_model(model_spec: str, default_model: str) -> tuple[str, str]:
    """'provider/model' 或 'inherit' 解析为 (provider_name, model_id)。"""
    if model_spec in ("inherit", "", None):
        model_spec = default_model
    if "/" in model_spec:
        provider, model_id = model_spec.split("/", 1)
        return provider, model_id
    return "deepseek", model_spec
