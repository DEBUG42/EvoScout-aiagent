"""飞书冒烟测试：验证凭证 + 长连接建连 + 三种消息发送。

前置：.env 已配置 LARK_APP_ID_MASTER / LARK_APP_SECRET_MASTER，且你在飞书给机器人发过一条消息（绑定 open_id）。

用法: .venv/Scripts/python.exe scripts/feishu_smoke.py [bot名，默认 master]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bots.lark_channel import LarkChannel
from app.config.settings import load_settings
from app.storage.db import DB
from app.storage.repo import Repo
from app.utils.log import setup_logging


def main() -> None:
    bot_name = sys.argv[1] if len(sys.argv) > 1 else "master"
    settings = load_settings()
    setup_logging(settings.data_dir)
    repo = Repo(DB(settings.data_dir / "hub.db"))

    app_id, secret = settings.lark_credentials(bot_name)
    print(f"[{bot_name}] 凭证: app_id={app_id[:10]}...")

    received: list = []

    def on_message(msg) -> None:
        received.append(msg)
        print(f"收到消息: user={msg.user_id[:12]}... chat={msg.chat_type} text={msg.text[:40]!r}")

    channel = LarkChannel(app_id, secret, bot_name, on_message=on_message)
    channel.start()
    print(f"[{bot_name}] 长连接已启动，请在飞书给该机器人发一条消息（60 秒内）...")

    deadline = time.time() + 60
    while time.time() < deadline and not received:
        time.sleep(1)
    if not received:
        print("超时未收到消息：请检查 1) .env 凭证 2) 飞书后台事件订阅「长连接」+ im.message.receive_v1 3) 应用已发布且可用范围包含你")
        channel.stop()
        return
    msg = received[0]
    print(f"收到消息成功！回发三种消息到 user={msg.user_id[:12]}...")
    msg_id = channel.send_text("冒烟测试：文本消息 OK", msg.chat_type, msg.chat_id, msg.user_id)
    print(f"text -> message_id={msg_id}")
    msg_id = channel.send_post("冒烟测试", [["富文本 post OK", " 第二行"]],
                               msg.chat_type, msg.chat_id, msg.user_id)
    print(f"post -> message_id={msg_id}")
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green", "title": {"tag": "plain_text", "content": "冒烟测试卡片"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "卡片消息 **OK**"}}],
    }
    msg_id = channel.send_card(card, msg.chat_type, msg.chat_id, msg.user_id)
    print(f"card -> message_id={msg_id}")
    print("全部成功！手机端应看到 3 条消息。")
    channel.stop()


if __name__ == "__main__":
    main()
