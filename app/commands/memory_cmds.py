"""记忆命令：/memory show|add|set（管理当前 bot 自己的记忆）。"""
from __future__ import annotations

from app.commands.base import Command, CommandContext, CommandRegistry
from app.memory.store import MemoryStore, MemoryType


def register(reg: CommandRegistry) -> None:
    def memory_cmd(ctx: CommandContext) -> None:
        store = MemoryStore(ctx.memory_dir, ctx.bot_name)
        if not ctx.args or ctx.args[0] == "show":
            metas = store.list_memories()
            if not metas:
                ctx.reply(f"{ctx.bot_name} 暂无记忆（/memory add <文本> 添加偏好）")
            else:
                out = ["记忆条目:"]
                for m in metas:
                    mem = store.read_memory(m.name)
                    out.append(f"- {m.name} [{m.type.value}]: {(mem.content if mem else '')[:120]}")
                ctx.reply("\n".join(out)[:1800])
            return
        action = ctx.args[0]
        if action == "add":
            if len(ctx.args) < 2:
                ctx.reply("用法: /memory add <内容>（追加到 notes 记忆）")
                return
            text = " ".join(ctx.args[1:])
            existing = store.read_memory("notes")
            if existing:
                store.write_memory("notes", existing.content + "\n" + text,
                                   existing.meta.type, existing.meta.description, existing.meta.hook)
            else:
                store.write_memory("notes", text, MemoryType.PROJECT, "手机添加的备注", "备注")
            ctx.reply("已追加记忆")
        elif action == "set":
            if len(ctx.args) < 3:
                ctx.reply("用法: /memory set <名称> <内容>（新建或覆盖一条记忆）")
                return
            name, text = ctx.args[1], " ".join(ctx.args[2:])
            store.write_memory(name, text, MemoryType.PROJECT, "手机设置", "手机设置")
            ctx.reply(f"已写入记忆 {name}")
        else:
            ctx.reply("用法: /memory show | /memory add <文本> | /memory set <名称> <内容>")

    reg.register(Command("memory", "管理本机器人记忆（show/add/set）", memory_cmd))
