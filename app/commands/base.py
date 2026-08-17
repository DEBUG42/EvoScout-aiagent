"""命令框架：Command 抽象 + Registry + /cmd 解析分发。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from app.bots.lark_channel import IncomingMessage


@dataclass
class CommandContext:
    msg: IncomingMessage
    args: list[str]
    bot_name: str
    reply: Callable[[str], None]          # 文本回复到来源会话
    reply_post: Callable[[str, list], None]  # (title, lines)
    repo: object
    registry: object
    settings: object
    lark: object                          # LarkChannel
    ai_client: object                     # DeepSeekClient | None
    memory_dir: object
    executor: object
    security: object


@dataclass
class Command:
    name: str
    help: str
    handler: Callable[[CommandContext], None]
    description: str = ""


class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd

    def all_help(self) -> list[str]:
        return [f"/{c.name} — {c.help}" for c in sorted(self._commands.values(), key=lambda c: c.name)]

    def dispatch(self, ctx: CommandContext) -> str:
        """解析并执行；返回状态（供 command_log 记录）。"""
        text = ctx.msg.text.strip()
        if not text.startswith("/"):
            return "ignored"
        parts = text.split()
        name = parts[0][1:].lower()
        cmd = self._commands.get(name)
        if not cmd:
            ctx.reply(f"未知命令 /{name}，输入 /help 查看全部命令")
            return "unknown"
        ctx.args = parts[1:]
        try:
            cmd.handler(ctx)
            return "ok"
        except Exception as e:
            logger.exception(f"命令 /{name} 执行异常")
            ctx.reply(f"命令执行异常: {type(e).__name__}: {e}")
            return "failed"
