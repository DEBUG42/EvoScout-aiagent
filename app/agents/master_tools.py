"""主控专属工具（M3）：管理其它 AI + 调度子代理 + 邮箱通信。

注册进 ToolRegistry 后，master 定义里 tools 列表声明即可用。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from app.agents.defs import AgentDefinition, dump_agent_md
from app.agents.mailbox import Mailbox
from app.agents.registry import AgentRegistry
from app.agents.subagent import SubagentManager
from app.core.tools import Tool, ToolContext, ToolRegistry


def build_master_tools(
    ctx: ToolContext,
    registry: AgentRegistry,
    subagents: SubagentManager,
    mailbox: Mailbox,
) -> list[Tool]:
    agents_dir = ctx.root_dir / "agents"

    def _list_agents(args: dict) -> str:
        lines = []
        for name in registry.list_names():
            a = registry.get(name)
            subs = json.dumps(a.subscriptions, ensure_ascii=False) if a.subscriptions else "-"
            lines.append(
                f"{name} | role={a.role} | model={a.model} | channel={a.channel} | "
                f"memory={a.memory} | 订阅={subs}"
            )
        return "\n".join(lines)

    def _modify_agent(args: dict) -> str:
        name, field = args["name"], args["field"]
        agent = registry.get(name)
        if agent.role == "master" and name == registry.master().name:
            # 允许修改 master 自身定义，但提示谨慎
            pass
        value = args.get("value")
        if field in ("description", "prompt", "model", "max_turns", "memory", "channel"):
            if field == "max_turns":
                value = int(value)
            setattr(agent, field, value)
        elif field == "subscriptions":
            agent.subscriptions = json.loads(value) if isinstance(value, str) else dict(value)
        elif field == "add_tool":
            if value not in agent.tools:
                agent.tools.append(value)
        elif field == "remove_tool":
            agent.tools = [t for t in agent.tools if t != value]
        else:
            return f"错误: 不支持的字段 {field}（可选: description/prompt/model/max_turns/subscriptions/add_tool/remove_tool）"
        source = agent.source_file or (agents_dir / f"{agent.name}.md")
        source.write_text(dump_agent_md(agent), encoding="utf-8")
        registry.reload()
        return f"已修改 {name} 的 {field} 并热重载"

    def _send_to_agent(args: dict) -> str:
        mail = mailbox.send(args["to"], args["text"], sender=args.get("sender", "master"))
        return f"已投递到 {args['to']} 的邮箱（{mail.id}）"

    def _poll_mailbox(args: dict) -> str:
        mails = mailbox.poll(args.get("agent", "master"))
        if not mails:
            return "(邮箱为空)"
        return "\n".join(f"[{m.sender}@{m.timestamp}] {m.text[:500]}" for m in mails)

    def _spawn_subagent(args: dict) -> str:
        background = bool(args.get("background", False))
        task = subagents.spawn(
            args.get("name", "general"),
            args["prompt"],
            model=args.get("model", "inherit"),
            background=background,
        )
        if background:
            return (
                f"子代理已后台启动: id={task.id} name={task.name}。"
                f"完成后结果会自动推送给用户，无需等待。"
            )
        return (
            f"子代理 {task.name} 完成（status={task.status}, turns={task.turns}）:\n{task.result}"
            + (f"\n错误: {task.error}" if task.error else "")
        )

    def _spawn_codex(args: dict) -> str:
        background = bool(args.get("background", False))
        task = subagents.spawn_cli(
            args["prompt"],
            model=args.get("model", "inherit"),
            background=background,
            workdir=Path(args["workdir"]) if args.get("workdir") else None,
        )
        if background:
            return (
                f"codex 子代理已后台启动: id={task.id}。"
                f"完成后结果会自动推送给用户，无需等待。"
            )
        return (
            f"codex 子代理完成（status={task.status}）:\n{task.result}"
            + (f"\n错误: {task.error}" if task.error else "")
        )

    def _get_task(args: dict) -> str:
        task = subagents.get(args["task_id"])
        if not task:
            return f"错误: 找不到任务 {args['task_id']}"
        return (
            f"任务 {task.id} [{task.name}] status={task.status}\n"
            f"结果: {task.result or task.error or '(运行中)'}"
        )

    return [
        Tool(
            name="list_agents",
            description="列出全部 AI（主控与机器人）的定义：role/model/订阅等",
            input_schema={"properties": {}},
            call=_list_agents,
        ),
        Tool(
            name="modify_agent",
            description="修改某个 AI 的定义并热重载。field 可选: description/prompt/model/max_turns/subscriptions(JSON)/add_tool/remove_tool",
            input_schema={"properties": {
                "name": {"type": "string"},
                "field": {"type": "string"},
                "value": {"type": "string"},
            }, "required": ["name", "field", "value"]},
            call=_modify_agent,
            is_read_only=False,
        ),
        Tool(
            name="send_to_agent",
            description="经文件邮箱给其它 AI 发消息（下轮运行会读取）",
            input_schema={"properties": {
                "to": {"type": "string"},
                "text": {"type": "string"},
            }, "required": ["to", "text"]},
            call=_send_to_agent,
            is_read_only=False,
        ),
        Tool(
            name="poll_mailbox",
            description="读取某 AI 邮箱中的未读消息",
            input_schema={"properties": {
                "agent": {"type": "string", "description": "默认 master"},
            }},
            call=_poll_mailbox,
        ),
        Tool(
            name="spawn_subagent",
            description="派发任务给内置子代理（DeepSeek 驱动，检索/分析类）。耗时任务必须用 background=true：立即返回任务 id，完成后结果自动推送给用户，主控不等待",
            input_schema={"properties": {
                "name": {"type": "string", "description": "子代理名，默认 general"},
                "prompt": {"type": "string", "description": "任务描述必须自包含（子代理看不到你的对话）"},
                "model": {"type": "string"},
                "background": {"type": "boolean", "description": "耗时任务设为 true"},
            }, "required": ["prompt"]},
            call=_spawn_subagent,
            is_read_only=False,
        ),
        Tool(
            name="spawn_codex",
            description="派发编码任务给 codex CLI（子进程）。仅在用户明确要求使用 codex 时调用；其余任务一律用 spawn_subagent",
            input_schema={"properties": {
                "prompt": {"type": "string", "description": "编码任务描述，含文件路径与验收标准"},
                "workdir": {"type": "string", "description": "工作目录，默认项目根"},
                "model": {"type": "string"},
                "background": {"type": "boolean"},
            }, "required": ["prompt"]},
            call=_spawn_codex,
            is_read_only=False,
        ),
        Tool(
            name="get_task",
            description="查询子代理任务状态与结果",
            input_schema={"properties": {
                "task_id": {"type": "string"},
            }, "required": ["task_id"]},
            call=_get_task,
        ),
    ]
