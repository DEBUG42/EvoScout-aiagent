"""终端 REPL：与主控 AI 对话，验证 agent 循环/工具/子代理（无飞书时用）。

用法: .venv/Scripts/python.exe scripts/repl.py [--no-confirm]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.mailbox import Mailbox
from app.agents.master_tools import build_master_tools
from app.agents.registry import AgentRegistry
from app.agents.subagent import SubagentManager
from app.config.settings import ROOT_DIR, load_settings
from app.core.agent_loop import LoopConfig, run_loop
from app.core.model import CodexCliProvider, build_providers
from app.core.tools import ToolContext, build_base_tools
from app.memory.inject import build_memory_prompt
from app.utils.log import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-confirm", action="store_true", help="非只读工具不询问直接执行")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.data_dir)

    registry = AgentRegistry(settings.agents_dir)
    master = registry.master()
    ctx = ToolContext(root_dir=ROOT_DIR, memory_dir=settings.memory_dir, data_dir=settings.data_dir)
    tools = build_base_tools(ctx)
    providers = build_providers()
    codex = CodexCliProvider() if shutil.which("codex") else None
    mailbox = Mailbox(settings.data_dir / "mailboxes")
    executor = ThreadPoolExecutor(max_workers=4)
    subagents = SubagentManager(
        executor, registry, tools, providers, settings.data_dir, settings.ai.model, codex
    )
    for t in build_master_tools(ctx, registry, subagents, mailbox):
        tools.register(t)

    system_prompt = (
        master.prompt
        + "\n\n"
        + build_memory_prompt(settings.memory_dir, master.name)
    )
    cfg = LoopConfig(
        provider_map=providers,
        tools=tools,
        default_model=settings.ai.model,
        max_turns=master.max_turns,
        system_prompt=system_prompt,
        allowed_tools=master.tools or None,
        disallowed_tools=master.disallowed_tools or None,
        on_tool_call=lambda n, a, r: print(f"  [tool] {n}({str(a)[:100]}) -> {str(r)[:150]}"),
    )
    if not args.no_confirm:
        def confirm(tool, tool_args) -> bool:
            if tool.is_read_only:
                return True
            ans = input(f"  [确认] 执行 {tool.name} {str(tool_args)[:100]}? [y/N] ")
            return ans.strip().lower() in ("y", "yes")
        cfg.confirm = confirm

    model = master.model if master.model != "inherit" else settings.ai.model
    print(f"主控 {master.name} 就绪 | 模型 {model} | 工具 {len(tools.names())} 个")
    print(f"记忆条目: {len(settings.memory_dir / master.name)} | 输入 /quit 退出\n")
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("/quit", "/exit"):
            break
        if not line:
            continue
        result = run_loop(cfg, line, model_spec=master.model)
        print(f"\n{master.name}> {result.text}\n")


if __name__ == "__main__":
    main()
