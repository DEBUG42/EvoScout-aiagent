"""系统命令：/help /status /shot。"""
from __future__ import annotations

from pathlib import Path

from app.commands.base import Command, CommandContext, CommandRegistry
from app.utils.system_status import get_system_status


def _take_screenshot(ctx: CommandContext) -> Path | None:
    import mss
    with mss.mss() as sct:
        shot = sct.shot(output=str(ctx.settings.data_dir / "logs" / "screenshot.png"))
    return Path(shot)


def register(reg: CommandRegistry) -> None:
    def help_cmd(ctx: CommandContext) -> None:
        ctx.reply("可用命令:\n" + "\n".join(reg.all_help()))

    def status_cmd(ctx: CommandContext) -> None:
        ctx.reply(get_system_status())

    def shot_cmd(ctx: CommandContext) -> None:
        try:
            image_path = _take_screenshot(ctx)
            image_key = ctx.lark.upload_image(image_path)
            ctx.lark.send_image(image_key, ctx.msg.chat_type, ctx.msg.chat_id, ctx.msg.user_id)
            ctx.reply("已回传屏幕截图")
        except Exception as e:
            ctx.reply(f"截图失败: {e}")

    reg.register(Command("help", "显示全部命令", help_cmd))
    reg.register(Command("status", "系统状态（CPU/内存/磁盘/GPU）", status_cmd))
    reg.register(Command("shot", "屏幕截图回传", shot_cmd))
