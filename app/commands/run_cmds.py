"""执行命令：/run（预设脚本白名单）与 /shell（前缀白名单 + 危险确认卡片）。"""
from __future__ import annotations

from loguru import logger

from app.commands.base import Command, CommandContext, CommandRegistry
from app.config.settings import ROOT_DIR
from app.core.subprocess import run_command


def _confirm_card(token: str, command: str, user_id: str) -> dict:
    """危险命令确认卡片：确认(danger) / 取消 按钮，回调走 card.action.trigger。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "危险命令确认"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**即将执行：**\n```\n{command[:500]}\n```\n令牌 10 分钟内有效"}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认执行"},
                        "type": "danger",
                        "value": {"action": "confirm_shell", "token": token, "user_id": user_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "type": "default",
                        "value": {"action": "cancel_shell", "token": token, "user_id": user_id},
                    },
                ],
            },
        ],
    }


def register(reg: CommandRegistry) -> None:
    def run_cmd(ctx: CommandContext) -> None:
        scripts = ctx.settings.security.scripts
        if not ctx.args or ctx.args[0] == "list":
            if not scripts:
                ctx.reply("无已注册脚本（config.yaml security.scripts）")
            else:
                ctx.reply("已注册脚本:\n" + "\n".join(f"- {k}（{'需确认' if v.confirm else '直接执行'}）" for k, v in scripts.items()))
            return
        name = ctx.args[0]
        if name not in scripts:
            ctx.reply(f"脚本 {name} 不存在，/run list 查看")
            return
        script = scripts[name]
        path = ROOT_DIR / script.path
        if script.confirm:
            token = ctx.security.create_confirmation(ctx.bot_name, ctx.msg.user_id, f"run {name}")
            ctx.lark.send_card(_confirm_card(token, f"/run {name}", ctx.msg.user_id),
                               ctx.msg.chat_type, ctx.msg.chat_id, ctx.msg.user_id)
            ctx.reply("该脚本需确认：请点击上方卡片")
            return
        _execute_script(ctx, path, name)

    def shell_cmd(ctx: CommandContext) -> None:
        command = " ".join(ctx.args).strip()
        if not command:
            ctx.reply("用法: /shell <命令>")
            return
        verdict, _ = ctx.security.check_shell(command)
        if verdict == "reject":
            ctx.reply("命令不在白名单内且被策略拒绝")
            return
        if verdict == "confirm":
            token = ctx.security.create_confirmation(ctx.bot_name, ctx.msg.user_id, command)
            ctx.lark.send_card(_confirm_card(token, command, ctx.msg.user_id),
                               ctx.msg.chat_type, ctx.msg.chat_id, ctx.msg.user_id)
            ctx.reply("该命令需确认：请点击上方卡片")
            return
        _execute_shell(ctx, command)

    reg.register(Command("run", "执行预设脚本（/run list 查看）", run_cmd))
    reg.register(Command("shell", "白名单命令行（危险命令需确认）", shell_cmd))


def _execute_script(ctx: CommandContext, path, name: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".py":
        cmd = ["python", str(path)]
        shell = False
    elif suffix in (".ps1",):
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(path)]
        shell = False
    else:
        cmd = [str(path)]
        shell = False
    result = run_command(cmd, cwd=path.parent, timeout=300, shell=shell)
    ctx.reply(f"/run {name} 完成（{result.duration}s）:\n{(result.stdout or result.stderr or '(无输出)')[:1800]}")


def _execute_shell(ctx: CommandContext, command: str) -> None:
    result = run_command([command], timeout=120, shell=True)
    out = (result.stdout or "(无输出)")[:1800]
    if result.timed_out:
        out += "\n(超时已终止)"
    if result.returncode != 0:
        out += f"\n退出码 {result.returncode}: {(result.stderr or '')[:300]}"
    ctx.reply(out)
