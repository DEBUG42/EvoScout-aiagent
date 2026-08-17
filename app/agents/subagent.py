"""Subagent 生命周期：spawn → 独立上下文运行 → 结果回传（task-notification 风格）。

两类子代理：
- 内置子代理：进程内 run_loop（DeepSeek 驱动），零对话历史、自带 system prompt
- CLI 子代理：子进程 codex exec（编码任务），输出落盘
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.core.agent_loop import LoopConfig, LoopResult, run_loop
from app.core.model import CodexCliProvider
from app.core.tools import ToolRegistry

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"


@dataclass
class SubagentTask:
    id: str
    name: str
    prompt: str
    model: str
    kind: str = "builtin"           # builtin | cli
    status: str = STATUS_RUNNING
    result: str = ""
    error: str = ""
    turns: int = 0
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str = ""
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def notification_xml(self) -> str:
        """以 Claude Code task-notification 风格回传主控。"""
        return (
            f"<task-notification>\n"
            f"  <task-id>{self.id}</task-id>\n"
            f"  <status>{self.status}</status>\n"
            f"  <agent>{self.name}</agent>\n"
            f"  <summary>{self.result[:300]}</summary>\n"
            f"</task-notification>"
        )


class SubagentManager:
    def __init__(
        self,
        executor: ThreadPoolExecutor,
        registry,                       # AgentRegistry
        tools: ToolRegistry,
        provider_map,
        data_dir: Path,
        default_model: str,
        codex: CodexCliProvider | None = None,
        on_complete=None,               # Callable[[SubagentTask], None]：任务完成回调（推飞书等）
    ):
        self.executor = executor
        self.registry = registry
        self.tools = tools
        self.provider_map = provider_map
        self.data_dir = data_dir
        self.default_model = default_model
        self.codex = codex
        self.on_complete = on_complete
        self._builtin_timeout = 480.0      # 内置子代理墙钟超时（8 分钟）
        self.tasks: dict[str, SubagentTask] = {}

    def spawn(self, name: str, prompt: str, model: str = "inherit",
              background: bool = False) -> SubagentTask:
        """按名字 spawn：agents/*.md 中 role=subagent 的定义，或内置 general，或 cli。"""
        if name in ("codex", "cli"):
            return self.spawn_cli(prompt, model, background)
        task = SubagentTask(id=uuid.uuid4().hex[:12], name=name, prompt=prompt, model=model)
        self.tasks[task.id] = task
        if background:
            self.executor.submit(self._run_builtin, task)
        else:
            self._run_builtin(task)
        return task

    def spawn_cli(self, prompt: str, model: str = "inherit",
                  background: bool = False, workdir: Path | None = None) -> SubagentTask:
        task = SubagentTask(id=uuid.uuid4().hex[:12], name="codex", prompt=prompt,
                            model=model, kind="cli")
        self.tasks[task.id] = task
        if background:
            self.executor.submit(self._run_cli, task, workdir)
        else:
            self._run_cli(task, workdir)
        return task

    def _run_builtin(self, task: SubagentTask) -> None:
        abort = threading.Event()
        wall_timer = threading.Timer(self._builtin_timeout, abort.set)
        wall_timer.start()
        try:
            try:
                agent = self.registry.get(task.name)
                if not agent.role == "subagent":
                    agent = self._fallback_subagent(task.name)
            except KeyError:
                agent = self._fallback_subagent(task.name)
            cfg = LoopConfig(
                provider_map=self.provider_map,
                tools=self.tools,
                default_model=self.default_model,
                max_turns=agent.max_turns,
                system_prompt=agent.prompt or "你是通用子代理，完成主控派发的任务，输出简洁结果。",
                allowed_tools=agent.tools or None,
                disallowed_tools=agent.disallowed_tools or ["spawn_subagent"],
                abort_event=abort,
            )
            result: LoopResult = run_loop(cfg, task.prompt, model_spec=task.model)
            task.result = result.text
            task.turns = result.turns
            if result.aborted and not result.text:
                task.result = f"子代理超时中止（>{int(self._builtin_timeout // 60)} 分钟，已执行 {len(result.tool_calls)} 次工具调用）"
            if "轮上限" in (result.text or "") and result.tool_calls:
                task.result += (
                    f"\n（未在 {result.turns} 轮内完成；已执行 {len(result.tool_calls)} 次工具调用，"
                    f"最后动作: {result.tool_calls[-1][0]}）"
                )
            task.status = STATUS_COMPLETED
        except Exception as e:
            logger.exception(f"subagent {task.name} 失败")
            task.error = str(e)
            task.status = STATUS_FAILED
        finally:
            wall_timer.cancel()
            task.finished_at = datetime.now().isoformat(timespec="seconds")
            task._event.set()
            logger.info(f"subagent[{task.id}] {task.name} -> {task.status}")
            self._notify_complete(task)

    def _fallback_subagent(self, name: str):
        from app.agents.defs import AgentDefinition
        return AgentDefinition(
            name=name, description="通用子代理",
            prompt=(
                "你是通用子代理，完成主控派发的任务，输出简洁结果。不要反问，直接执行。\n"
                "效率规则：\n"
                "1. 读取文件时一次读全所需内容（limit 给大值），不要反复分块读同一文件\n"
                "2. 工具失败最多重试 2 次，仍失败就基于已有信息继续，不要死磕\n"
                "3. 网络不可用时不要反复测试网络，直接说明并输出已收集的信息\n"
                "4. 优先直接给出最终结论，减少不必要的工具调用\n"
                "输出规则：\n"
                "你处于真实运行环境，工具会被真实执行并返回结果。"
                "最终回答中严禁出现任何工具调用的代码/XML/JSON 格式"
                "（如 <invoke name=...>、<tool_call> 之类），只输出面向用户的自然语言结果。"
            ),
            role="subagent", max_turns=40,
        )

    def _run_cli(self, task: SubagentTask, workdir: Path | None) -> None:
        try:
            if not self.codex:
                raise RuntimeError("codex CLI 未配置")
            out_file = self.data_dir / "logs" / f"codex_{task.id}.out.txt"
            proc = self.codex.run_task(
                task.prompt, workdir or Path("."), output_file=out_file,
                model=None if task.model == "inherit" else task.model,
            )
            text = self.codex.parse_output(proc.stdout)
            task.result = text or "(codex 无输出)"
            if proc.timed_out:
                task.status = STATUS_FAILED
                task.error = f"codex 超时（输出见 {out_file}）"
            elif proc.returncode != 0:
                task.status = STATUS_FAILED
                task.error = f"codex 退出码 {proc.returncode}: {proc.stderr[:500]}"
            else:
                task.status = STATUS_COMPLETED
        except Exception as e:
            logger.exception("codex 子代理失败")
            task.error = str(e)
            task.status = STATUS_FAILED
        finally:
            task.finished_at = datetime.now().isoformat(timespec="seconds")
            task._event.set()
            self._notify_complete(task)

    def _notify_complete(self, task: SubagentTask) -> None:
        if not self.on_complete:
            return
        try:
            self.on_complete(task)
        except Exception:
            logger.exception("on_complete 回调异常")

    def get(self, task_id: str) -> SubagentTask | None:
        return self.tasks.get(task_id)
