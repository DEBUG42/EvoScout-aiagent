"""通用 agent 循环：LLM → 工具调用 → 执行 → 下一轮。

终止机制（参考 Claude Code / OpenAI Agents SDK / AutoGPT 的社区最佳实践）：
- max_turns 硬顶 + abort_event 墙钟中止
- 剩余 2 轮时注入收尾提醒（Claude Code token nudge 模式）
- 相同工具+参数重复 ≥3 次判为死循环，提前中止（AutoGPT/deer-flow 模式）
- 轮数用尽/循环检测后追加一次**无工具** LLM 调用，强制基于已有信息给出最终回答
  （替代生硬的"达到上限"文本）
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from app.core.model import Provider, ProviderError, Response, resolve_model
from app.core.tools import Tool, ToolRegistry

REPEAT_LIMIT = 3          # 相同工具调用重复次数阈值（判死循环）
NUDGE_TURNS_LEFT = 2      # 剩余轮数阈值（注入收尾提醒）
LEAK_FIX_ATTEMPTS = 2     # 工具语法泄漏修正轮上限

# 模型把工具调用格式当作文本输出（未走 function calling）的泄漏特征
LEAK_PATTERNS = [
    re.compile(r"<\s*invoke\b", re.IGNORECASE),
    re.compile(r"<\s*tool_call\b", re.IGNORECASE),
    re.compile(r"<\s*function_call\b", re.IGNORECASE),
    re.compile(r"<\s*param(?:eter)?\b", re.IGNORECASE),
    re.compile(r"\{\s*\"tool\"\s*:", re.IGNORECASE),
    re.compile(r"&lt;\s*(?:invoke|tool_call|function_call)\b", re.IGNORECASE),
]

LEAK_CORRECTION_MSG = (
    "你刚才的回复把工具调用格式当成了文本输出——那些调用并未真正执行。"
    "请基于已有信息直接给出最终的自然语言回答，不要再输出任何工具调用的代码/XML/JSON 格式。"
)


def _has_leak(text: str) -> bool:
    return any(p.search(text) for p in LEAK_PATTERNS)


@dataclass
class LoopConfig:
    provider_map: dict[str, Provider]
    tools: ToolRegistry
    default_model: str = "deepseek-chat"
    max_turns: int = 20
    system_prompt: str = ""
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    confirm: Callable[[Tool, dict], bool] | None = None   # 返回 False = 拒绝执行
    on_tool_call: Callable[[str, dict, str], None] | None = None  # (tool_name, args, result)
    abort_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class LoopResult:
    text: str
    turns: int
    tool_calls: list[tuple[str, dict, str]] = field(default_factory=list)  # (name, args, result)
    aborted: bool = False
    stop_reason: str = "answer"     # answer | max_turns | aborted | loop_detected


def run_loop(cfg: LoopConfig, user_msg: str, model_spec: str = "inherit") -> LoopResult:
    provider_name, model_id = resolve_model(model_spec, cfg.default_model)
    if provider_name not in cfg.provider_map:
        raise ProviderError(f"provider {provider_name!r} 不可用（现有 {sorted(cfg.provider_map)}）")
    provider = cfg.provider_map[provider_name]

    tool_schemas = cfg.tools.schemas_for(cfg.allowed_tools, cfg.disallowed_tools)
    messages: list[dict] = [
        {"role": "system", "content": cfg.system_prompt},
        {"role": "user", "content": user_msg},
    ]
    made: list[tuple[str, dict, str]] = []
    repeat_counter: dict[str, int] = {}

    def wrap_up(turn: int, reason: str) -> LoopResult:
        """轮数用尽/死循环后的收尾：无工具强制总结，失败降级为说明文本。"""
        wrap_messages = messages + [{
            "role": "system",
            "content": (
                "工具调用阶段已结束。请基于已完成的工作直接给出最终回答，"
                "不要再调用工具；若信息不足，如实说明已掌握的部分。"
            ),
        }]
        try:
            final_resp = provider.chat(wrap_messages, None, model_id)
            text = final_resp.text or "(无回复)"
            if text:
                if _has_leak(text):
                    text = _fix_leaked_reply(provider, model_id, wrap_messages, text)
                return LoopResult(text=text, turns=turn, tool_calls=made, stop_reason=reason)
        except ProviderError as e:
            logger.warning(f"wrap-up 总结调用失败: {e}")
        return LoopResult(
            text=f"（已执行 {len(made)} 次工具调用后结束，未形成最终结论）",
            turns=turn, tool_calls=made, stop_reason=reason,
        )

    for turn in range(1, cfg.max_turns + 1):
        if cfg.abort_event.is_set():
            return LoopResult(text=wrap_up(turn, "aborted").text or "(已中止)",
                              turns=turn, tool_calls=made, aborted=True, stop_reason="aborted")

        turns_left = cfg.max_turns - turn
        if turns_left == NUDGE_TURNS_LEFT:
            messages.append({
                "role": "system",
                "content": "剩余轮数不多：请尽快基于已有信息给出最终回答，避免新的工具调用。",
            })

        resp: Response = provider.chat(messages, tool_schemas, model_id)

        if not resp.has_tool_calls:
            text = resp.text or "(无回复)"
            if _has_leak(text):
                text = _fix_leaked_reply(provider, model_id, messages, text)
            return LoopResult(text=text, turns=turn,
                              tool_calls=made, stop_reason="answer")

        # 死循环检测：相同工具+参数重复 REPEAT_LIMIT 次
        for tc in resp.tool_calls:
            key = tc.name + ":" + json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False)
            repeat_counter[key] = repeat_counter.get(key, 0) + 1
            if repeat_counter[key] >= REPEAT_LIMIT:
                logger.warning(f"检测到重复工具调用 {tc.name} ×{REPEAT_LIMIT}，触发收尾总结")
                return wrap_up(turn, "loop_detected")

        # 回填 assistant 的 tool_calls
        assistant_msg: dict = {"role": "assistant", "content": resp.text}
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            }
            for tc in resp.tool_calls
        ]
        messages.append(assistant_msg)

        # 逐个执行
        for tc in resp.tool_calls:
            result_text = _execute_tool(cfg, tc.name, tc.arguments)
            made.append((tc.name, tc.arguments, result_text))
            if cfg.on_tool_call:
                try:
                    cfg.on_tool_call(tc.name, tc.arguments, result_text)
                except Exception:
                    logger.exception("on_tool_call 回调异常")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text[:4000]})

    return wrap_up(cfg.max_turns, "max_turns")


def _fix_leaked_reply(provider: Provider, model_id: str,
                      messages: list[dict], leaked_text: str) -> str:
    """最终回复泄漏工具调用语法 → 追加无工具修正轮强制纯文本；仍泄漏则替换文案。"""
    fix_messages = messages + [
        {"role": "assistant", "content": leaked_text[:3000]},
        {"role": "user", "content": LEAK_CORRECTION_MSG},
    ]
    for _ in range(LEAK_FIX_ATTEMPTS):
        try:
            resp = provider.chat(fix_messages, None, model_id)
        except ProviderError:
            break
        text = (resp.text or "").strip()
        if text and not _has_leak(text):
            return text
        fix_messages += [
            {"role": "assistant", "content": text[:3000]},
            {"role": "user", "content": LEAK_CORRECTION_MSG},
        ]
    logger.warning("模型回复持续泄漏工具调用语法，已替换为占位文案")
    return "（结果格式异常，已修正——请重新提问或换个说法）"


def _execute_tool(cfg: LoopConfig, name: str, args: dict) -> str:
    try:
        tool = cfg.tools.get(name)
    except KeyError:
        return f"错误: 未知工具 {name}（可用: {cfg.tools.names()}）"
    if (tool.is_destructive or not tool.is_read_only) and cfg.confirm and not cfg.confirm(tool, args):
        return "用户拒绝了该操作"
    try:
        return tool.call(args)
    except Exception as e:
        logger.exception(f"工具 {name} 执行异常")
        return f"工具执行异常: {type(e).__name__}: {e}"
