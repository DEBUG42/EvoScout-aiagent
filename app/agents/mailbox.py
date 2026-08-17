"""文件邮箱：agent 间消息传递（复刻 Claude Code swarm mailbox 模式）。

单进程内用 threading.Lock + 原子替换写 JSON；每 agent 一个 inbox 文件。
消息结构: {from, text, timestamp, read, summary}
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger


@dataclass
class Mail:
    id: str
    sender: str
    text: str
    timestamp: str
    read: bool = False
    summary: str = ""


class Mailbox:
    """mailboxes 目录管理器：send / poll(取未读并标已读)。"""

    def __init__(self, boxes_dir: Path):
        self.dir = boxes_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._lock_guard = threading.Lock()

    def _lock_for(self, agent: str) -> threading.Lock:
        with self._lock_guard:
            if agent not in self._locks:
                self._locks[agent] = threading.Lock()
            return self._locks[agent]

    def _path(self, agent: str) -> Path:
        return self.dir / f"{agent}.json"

    def _load(self, agent: str) -> list[dict]:
        p = self._path(agent)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("messages", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, OSError):
            logger.warning(f"邮箱文件损坏，重置: {p}")
            return []

    def _save(self, agent: str, messages: list[dict]) -> None:
        tmp = self._path(agent).with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"messages": messages}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(self._path(agent))  # 原子替换

    def send(self, to: str, text: str, sender: str = "system", summary: str = "") -> Mail:
        mail = Mail(
            id=uuid.uuid4().hex[:12],
            sender=sender,
            text=text,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            summary=summary,
        )
        with self._lock_for(to):
            messages = self._load(to)
            messages.append({
                "id": mail.id, "from": mail.sender, "text": mail.text,
                "timestamp": mail.timestamp, "read": False, "summary": mail.summary,
            })
            self._save(to, messages)
        logger.debug(f"mail: {sender} -> {to}: {text[:60]}")
        return mail

    def poll(self, agent: str, mark_read: bool = True) -> list[Mail]:
        """取未读消息（默认取后标已读）。"""
        with self._lock_for(agent):
            messages = self._load(agent)
            unread = [m for m in messages if not m.get("read")]
            if unread and mark_read:
                for m in unread:
                    m["read"] = True
                self._save(agent, messages)
        return [
            Mail(id=m["id"], sender=m.get("from", ""), text=m["text"],
                 timestamp=m.get("timestamp", ""), read=bool(m.get("read")),
                 summary=m.get("summary", ""))
            for m in unread
        ]
