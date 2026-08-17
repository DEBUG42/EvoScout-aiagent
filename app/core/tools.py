"""工具注册：Tool dataclass（fail-closed 权限）+ 数组注册（复刻 Claude Code Tool.ts 模式）。

M2 内置基础工具：shell / read_file / write_file / edit_file / list_dir / memory_*
M3 追加：spawn_subagent / send_to_agent / list_agents / modify_agent / send_feishu / query_db
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

from app.memory.store import MemoryStore, MemoryType


@dataclass
class ToolContext:
    """工具执行所需的共享上下文。"""
    root_dir: Path
    memory_dir: Path
    data_dir: Path


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    call: Callable[[dict], str]
    is_read_only: bool = True
    is_destructive: bool = False
    confirm_message: str = ""           # 危险工具确认文案模板

    def __post_init__(self) -> None:
        self.input_schema.setdefault("type", "object")
        self.input_schema.setdefault("properties", {})
        self.input_schema.setdefault("required", [])


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def filter_for(self, allowed: list[str] | None, disallowed: list[str] | None) -> list[Tool]:
        """按 agent 定义的 tools/disallowed_tools 过滤；tools 为空列表 = 全部。"""
        tools = list(self._tools.values())
        if allowed:
            tools = [t for t in tools if t.name in allowed or "*" in allowed]
        if disallowed:
            tools = [t for t in tools if t.name not in disallowed and "*" not in disallowed]
        return tools

    def schemas_for(self, allowed: list[str] | None, disallowed: list[str] | None) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self.filter_for(allowed, disallowed)
        ]


# ---- 基础工具实现 ----

def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n...(截断，共 {len(text)} 字符)"
    return text


def _read_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.root_dir / args["path"]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"错误: 文件不存在 {path}"
    offset = max(0, int(args.get("offset", 0)))
    limit = int(args.get("limit", 2000)) or len(text)
    lines = text.splitlines()
    return "\n".join(f"{i+1}\t{l}" for i, l in enumerate(lines[offset:offset + limit], start=offset))


def _write_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.root_dir / args["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    return f"已写入 {path}"


def _edit_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.root_dir / args["path"]
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误: 文件不存在 {path}"
    old, new = args["old_string"], args["new_string"]
    count = text.count(old)
    if count == 0:
        return "错误: old_string 在文件中不存在"
    if count > 1:
        return f"错误: old_string 出现 {count} 次，请提供更长上下文使其唯一"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"已修改 {path}"


def _list_dir(args: dict, ctx: ToolContext) -> str:
    path = ctx.root_dir / args.get("path", ".")
    if not path.exists():
        return f"错误: 目录不存在 {path}"
    entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines = [f"{'[F]' if p.is_file() else '[D]'} {p.name}" for p in entries]
    return "\n".join(lines) if lines else "(空目录)"


def _shell(args: dict, ctx: ToolContext) -> str:
    from app.core.subprocess import run_command

    cmd = args["command"]
    timeout = float(args.get("timeout", 120))
    result = run_command([cmd], cwd=ctx.root_dir, timeout=timeout, shell=True)
    out = _truncate(result.stdout or "(无输出)")
    if result.timed_out:
        return f"命令超时（{timeout}s）已终止。\n{out}"
    if result.returncode != 0:
        err = _truncate(result.stderr or "")
        return f"退出码 {result.returncode}\n{out}\n{err}"
    return out


def _memory_list(args: dict, ctx: ToolContext) -> str:
    store = MemoryStore(ctx.memory_dir, args["agent"])
    metas = store.list_memories()
    if not metas:
        return f"{args['agent']} 没有记忆条目"
    return "\n".join(m.index_line() for m in metas)


def _memory_read(args: dict, ctx: ToolContext) -> str:
    store = MemoryStore(ctx.memory_dir, args["agent"])
    mem = store.read_memory(args["name"])
    if not mem:
        return f"错误: {args['agent']} 没有名为 {args['name']} 的记忆"
    return _truncate(mem.content)


def _memory_write(args: dict, ctx: ToolContext) -> str:
    store = MemoryStore(ctx.memory_dir, args["agent"])
    try:
        mtype = MemoryType(args.get("type", "project"))
    except ValueError:
        return f"错误: 非法记忆类型（可选 {[t.value for t in MemoryType]}）"
    store.write_memory(
        args["name"], args["content"], mtype, args.get("description", ""), args.get("hook", "")
    )
    return f"已写入 {args['agent']} 的记忆 {args['name']}（类型 {mtype.value}）"


def _system_status_tool(args: dict, ctx: ToolContext) -> str:
    from app.utils.system_status import get_system_status
    return get_system_status()


def _web_search_tool(args: dict, ctx: ToolContext) -> str:
    from app.utils.websearch import web_search_text
    return web_search_text(args["query"], int(args.get("max_results", 8)))


def build_base_tools(ctx: ToolContext) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        name="shell",
        description=(
            "在电脑上执行命令行（Windows cmd），返回输出。用于查状态、跑脚本、系统操作。"
            "注意：最终回复中严禁出现工具调用的代码格式——工具结果会直接返回给你，"
            "你只需基于返回内容用自然语言回答用户"
        ),
        input_schema={"properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "number", "description": "超时秒数，默认 120"},
        }, "required": ["command"]},
        call=lambda a: _shell(a, ctx),
        is_read_only=False,
    ))
    reg.register(Tool(
        name="system_status",
        description="查询电脑系统状态：CPU/内存/磁盘/GPU/开机时长（优先用本工具，不要用 shell 查状态）",
        input_schema={"properties": {}},
        call=lambda a: _system_status_tool(a, ctx),
    ))
    reg.register(Tool(
        name="web_search",
        description="联网搜索（Bing），返回标题/链接/摘要列表。查论文、查资料、查新闻时优先用本工具，不要用 shell 写爬虫脚本",
        input_schema={"properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "结果数，默认 8"},
        }, "required": ["query"]},
        call=lambda a: _web_search_tool(a, ctx),
    ))
    reg.register(Tool(
        name="read_file",
        description="读取文件内容（带行号），支持 offset/limit 分段读取",
        input_schema={"properties": {
            "path": {"type": "string", "description": "相对项目根的文件路径"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        }, "required": ["path"]},
        call=lambda a: _read_file(a, ctx),
    ))
    reg.register(Tool(
        name="write_file",
        description="创建或整体覆盖文件",
        input_schema={"properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
        call=lambda a: _write_file(a, ctx),
        is_read_only=False,
    ))
    reg.register(Tool(
        name="edit_file",
        description="精确字符串替换修改文件（old_string 需唯一）",
        input_schema={"properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        }, "required": ["path", "old_string", "new_string"]},
        call=lambda a: _edit_file(a, ctx),
        is_read_only=False,
    ))
    reg.register(Tool(
        name="list_dir",
        description="列出目录内容",
        input_schema={"properties": {
            "path": {"type": "string", "description": "相对项目根的目录路径，默认 ."},
        }},
        call=lambda a: _list_dir(a, ctx),
    ))
    reg.register(Tool(
        name="memory_list",
        description="列出某个 AI 的记忆条目索引",
        input_schema={"properties": {
            "agent": {"type": "string", "description": "AI 名字，如 master/aipapers"},
        }, "required": ["agent"]},
        call=lambda a: _memory_list(a, ctx),
    ))
    reg.register(Tool(
        name="memory_read",
        description="读取某个 AI 的某条记忆全文",
        input_schema={"properties": {
            "agent": {"type": "string"},
            "name": {"type": "string", "description": "记忆条目名"},
        }, "required": ["agent", "name"]},
        call=lambda a: _memory_read(a, ctx),
    ))
    reg.register(Tool(
        name="memory_write",
        description="写入/覆盖某个 AI 的记忆条目（先读索引判断是否需要新建）",
        input_schema={"properties": {
            "agent": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string"},
            "type": {"type": "string", "description": "user|feedback|project|reference，默认 project"},
            "description": {"type": "string"},
        }, "required": ["agent", "name", "content"]},
        call=lambda a: _memory_write(a, ctx),
        is_read_only=False,
    ))
    logger.debug(f"基础工具注册: {reg.names()}")
    return reg
