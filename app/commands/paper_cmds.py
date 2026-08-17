"""信息命令：/papers /news /sub /translate。"""
from __future__ import annotations

from loguru import logger

from app.ai.prompts import TRANSLATE_SYSTEM
from app.bots.cards import paper_card, news_lines
from app.commands.base import Command, CommandContext, CommandRegistry


def register(reg: CommandRegistry) -> None:
    def papers_cmd(ctx: CommandContext) -> None:
        n = min(int(ctx.args[0]) if ctx.args else 5, 10)
        rows = ctx.repo.get_bot_items(ctx.bot_name, status="ready", limit=n)
        if not rows:
            ctx.reply("暂无待推送论文（/sub 检查订阅，稍后再试）")
            return
        for r in rows:
            if r["kind"] != "paper":
                continue
            item = ctx.repo.get_item(r["item_id"])
            ctx.lark.send_card(paper_card(item, r), ctx.msg.chat_type, ctx.msg.chat_id, ctx.msg.user_id)
        ctx.reply(f"已发送 {len(rows)} 篇论文卡片")

    def news_cmd(ctx: CommandContext) -> None:
        n = min(int(ctx.args[0]) if ctx.args else 10, 20)
        rows = ctx.repo.get_bot_items(ctx.bot_name, status="ready", limit=n * 2)
        news = [r for r in rows if r["kind"] == "news"][:n]
        if not news:
            ctx.reply("暂无新闻（/sub 检查订阅）")
            return
        ctx.reply_post("新闻速递", news_lines(news))

    def sub_cmd(ctx: CommandContext) -> None:
        if not ctx.args or ctx.args[0] == "list":
            subs = ctx.repo.list_subscriptions(ctx.bot_name)
            if not subs:
                ctx.reply("无订阅。用法: /sub add <arxiv分类|reddit子版|rss名> 或 /sub add hn")
            else:
                ctx.reply("订阅列表:\n" + "\n".join(f"- #{s['id']} {s['kind']}: {s['value']}" for s in subs))
            return
        action = ctx.args[0]
        if action == "add" and len(ctx.args) >= 2:
            value = ctx.args[1]
            if value.lower() in ("hn", "hackernews"):
                kind = "hn"
            elif value.lower().startswith(("cs.", "math", "physics", "eess", "q-")):
                kind = "arxiv"
            else:
                kind = "rss"
            ok = ctx.repo.add_subscription(ctx.bot_name, kind, value)
            ctx.reply("已添加" if ok else "已存在")
        elif action == "del" and len(ctx.args) >= 2:
            ok = ctx.repo.remove_subscription(ctx.bot_name, int(ctx.args[1]))
            ctx.reply("已删除" if ok else "未找到")
        else:
            ctx.reply("用法: /sub list | /sub add <值> | /sub del <id>")

    def translate_cmd(ctx: CommandContext) -> None:
        if not ctx.args:
            ctx.reply("用法: /translate <item id 或 arXiv id>")
            return
        if not ctx.ai_client:
            ctx.reply("DEEPSEEK_API_KEY 未配置")
            return
        q = ctx.args[0]
        if q.isdigit():
            item = ctx.repo.get_item(int(q))
        else:
            rows = ctx.repo.recent_items("paper", 200)
            item = next((r for r in rows if r["external_id"] == q), None)
        if not item:
            ctx.reply(f"未找到 {q}")
            return
        if not item.get("summary"):
            ctx.reply("该条目无英文摘要可翻译")
            return
        text = ctx.ai_client.chat_text(TRANSLATE_SYSTEM, item["summary"][:3000], bot=ctx.bot_name)
        ctx.reply(f"【{item['title'][:60]}】\n{text[:1800]}")

    reg.register(Command("papers", "最近待推送论文卡片（/papers 5）", papers_cmd))
    reg.register(Command("news", "最近新闻列表（/news 10）", news_cmd))
    reg.register(Command("sub", "管理订阅 /sub list|add|del", sub_cmd))
    reg.register(Command("translate", "翻译论文摘要（/translate <id>）", translate_cmd))
