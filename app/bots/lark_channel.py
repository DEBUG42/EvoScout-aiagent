"""飞书通道：lark-oapi 长连接（ws 收事件）+ HTTP 发消息。

- ws handler 必须 3 秒内返回（只做幂等去重 + 投递线程池），超时飞书会以相同 event_id 重发
- 发送走 SDK HTTP 客户端（线程安全）；图片需先上传拿 image_key
- 长连接模式不需要加密密钥；权限配置见 README
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

MENTION_RE = re.compile(r"@_user_\d+\s*")

CARD_ACTION_DONE = {
    "config": {"width_mode": "default"},
    "elements": [],
}


@dataclass
class IncomingMessage:
    event_id: str
    message_id: str
    user_id: str                  # open_id
    chat_id: str
    chat_type: str                # p2p | group
    text: str                     # 已清洗 @ 标记
    is_card_action: bool = False
    card_value: dict = field(default_factory=dict)


class LarkChannel:
    """单个飞书应用（一个 bot 一个实例）。"""

    def __init__(self, app_id: str, app_secret: str, bot_name: str,
                 on_message: Callable[[IncomingMessage], None],
                 on_card_action: Callable[[IncomingMessage], None] | None = None):
        import lark_oapi as lark

        self.lark = lark
        self.app_id = app_id
        self.app_secret = app_secret
        self.bot_name = bot_name
        self.on_message = on_message
        self.on_card_action = on_card_action
        self._http: lark.Client | None = None
        self._ws = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    # ---- 长连接（收事件）----

    def start(self) -> None:
        lark = self.lark
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_event)
            .register_p2_card_action_trigger(self._handle_card_event)
            .build()
        )
        self._ws = lark.ws.Client(
            self.app_id, self.app_secret,
            event_handler=handler, log_level=lark.LogLevel.WARNING,
        )
        self._thread = threading.Thread(
            target=self._run_ws, name=f"lark-ws-{self.bot_name}", daemon=True
        )
        self._thread.start()
        logger.info(f"[{self.bot_name}] 飞书长连接已启动")

    def _run_ws(self) -> None:
        backoff = 5
        while not self._stopped.is_set():
            try:
                self._ws.start()          # 阻塞；SDK 内部自带心跳与断线重连
            except Exception as e:
                logger.error(f"[{self.bot_name}] ws 异常: {e}，{backoff}s 后重启")
            if self._stopped.wait(backoff):
                break
            backoff = min(backoff * 2, 300)

    def stop(self) -> None:
        self._stopped.set()
        try:
            if self._ws:
                self._ws.stop()
        except Exception:
            pass

    def _handle_message_event(self, data) -> None:
        """3 秒回执：只做解析+投递，重活交给调用方线程池。"""
        try:
            event = data.event
            msg = event.message
            sender = event.sender.sender_id
            user_id = getattr(sender, "open_id", "") or getattr(sender, "union_id", "") or ""
            text = ""
            if msg.message_type == "text":
                try:
                    text = json.loads(msg.content or "{}").get("text", "")
                except json.JSONDecodeError:
                    text = msg.content or ""
            text = MENTION_RE.sub("", text).strip()
            self.on_message(IncomingMessage(
                event_id=data.header.event_id,
                message_id=msg.message_id,
                user_id=user_id,
                chat_id=msg.chat_id,
                chat_type=msg.chat_type,
                text=text,
            ))
        except Exception:
            logger.exception(f"[{self.bot_name}] 消息事件处理异常")

    def _handle_card_event(self, data) -> None:
        try:
            event = data.event
            operator = event.operator
            user_id = getattr(getattr(operator, "operator_id", None), "open_id", "") or ""
            value = {}
            try:
                value = json.loads(event.action.value or "{}")
            except json.JSONDecodeError:
                pass
            if self.on_card_action:
                self.on_card_action(IncomingMessage(
                    event_id=data.header.event_id,
                    message_id=event.context.open_message_id,
                    user_id=user_id,
                    chat_id="",
                    chat_type="p2p",
                    text="",
                    is_card_action=True,
                    card_value=value,
                ))
        except Exception:
            logger.exception(f"[{self.bot_name}] 卡片回调异常")

    # ---- HTTP（发消息）----

    @property
    def http(self):
        if self._http is None:
            self._http = (
                self.lark.Client.builder()
                .app_id(self.app_id)
                .app_secret(self.app_secret)
                .log_level(self.lark.LogLevel.WARNING)
                .build()
            )
        return self._http

    def _target(self, chat_type: str, chat_id: str, user_id: str) -> tuple[str, str]:
        """回复目标：p2p 用 sender open_id，群用 chat_id。"""
        if chat_type == "group":
            return chat_id, "chat_id"
        return user_id or chat_id, "open_id"

    def send_text(self, text: str, chat_type: str = "p2p", chat_id: str = "",
                  user_id: str = "") -> str | None:
        receive_id, id_type = self._target(chat_type, chat_id, user_id)
        if not receive_id:
            logger.warning(f"[{self.bot_name}] 无接收目标，丢弃消息")
            return None
        lark = self.lark
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text[:8000]}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self.http.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def send_post(self, title: str, lines: list[list[str]], chat_type: str = "p2p",
                  chat_id: str = "", user_id: str = "") -> str | None:
        """富文本 post 消息：lines = [[纯文本, ...], ...]（每行一个数组）。"""
        receive_id, id_type = self._target(chat_type, chat_id, user_id)
        if not receive_id:
            return None
        lark = self.lark
        content = {
            "zh_cn": {
                "title": title[:100],
                "content": [[{"tag": "text", "text": seg[:2000]} for seg in line] for line in lines],
            }
        }
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("post")
                .content(json.dumps(content, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self.http.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发送失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def upload_image(self, image_path: Path) -> str:
        """上传图片（≤10MB），返回 image_key。"""
        lark = self.lark
        with open(image_path, "rb") as f:
            request = (
                lark.im.v1.CreateImageRequest.builder()
                .request_body(
                    lark.im.v1.CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                )
                .build()
            )
            resp = self.http.im.v1.image.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书图片上传失败: code={resp.code} msg={resp.msg}")
        return resp.data.image_key

    def send_image(self, image_key: str, chat_type: str = "p2p", chat_id: str = "",
                   user_id: str = "") -> str | None:
        receive_id, id_type = self._target(chat_type, chat_id, user_id)
        if not receive_id:
            return None
        lark = self.lark
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}))
                .build()
            )
            .build()
        )
        resp = self.http.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发图失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def send_card(self, card_json: dict, chat_type: str = "p2p", chat_id: str = "",
                  user_id: str = "") -> str | None:
        receive_id, id_type = self._target(chat_type, chat_id, user_id)
        if not receive_id:
            return None
        lark = self.lark
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card_json, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self.http.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发卡片失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def upload_file(self, file_path: Path, file_type: str = "stream") -> str:
        """上传文件（im/v1/files，需 im:file 权限），返回 file_key。

        file_type: opus|mp4|pdf|doc|xls|ppt|stream（pptx 等新格式用 stream）。
        """
        lark = self.lark
        with open(file_path, "rb") as f:
            request = (
                lark.im.v1.CreateFileRequest.builder()
                .request_body(
                    lark.im.v1.CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(file_path.name)
                    .file(f)
                    .build()
                )
                .build()
            )
            resp = self.http.im.v1.file.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书文件上传失败: code={resp.code} msg={resp.msg}")
        return resp.data.file_key

    def send_file(self, file_key: str, chat_type: str = "p2p", chat_id: str = "",
                  user_id: str = "") -> str | None:
        receive_id, id_type = self._target(chat_type, chat_id, user_id)
        if not receive_id:
            return None
        lark = self.lark
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type(id_type)
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .build()
            )
            .build()
        )
        resp = self.http.im.v1.message.create(request)
        if not resp.success():
            raise RuntimeError(f"飞书发文件失败: code={resp.code} msg={resp.msg}")
        return resp.data.message_id

    def reply_card_action_done(self, open_message_id: str) -> None:
        """卡片回调回执（可选，避免按钮反复转圈）。"""
        try:
            lark = self.lark
            req = (
                lark.card.CallbackRequest.builder()
                .callback_type(lark.card.CallbackType.CARD_CALLBACK)
                .open_message_id(open_message_id)
                .body(json.dumps(CARD_ACTION_DONE, ensure_ascii=False))
                .build()
            )
            self.http.card.callback(req)
        except Exception:
            logger.debug(f"[{self.bot_name}] 卡片回执失败（忽略）")
