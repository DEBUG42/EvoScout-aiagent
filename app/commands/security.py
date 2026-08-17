"""安全策略：用户白名单（自动绑定）+ shell 前缀白名单 + 危险模式确认令牌。"""
from __future__ import annotations

import re

from loguru import logger

from app.storage.repo import Repo


class SecurityPolicy:
    def __init__(self, repo: Repo, settings):
        self.repo = repo
        self.cfg = settings.security
        self._danger_res = [re.compile(p, re.IGNORECASE) for p in self.cfg.danger_patterns]

    # ---- 用户 ----

    def check_user(self, bot: str, user_id: str) -> bool:
        """allowed_users 为空时绑定首个发消息用户并持久化；此后仅该用户可用。"""
        if not user_id:
            return False
        allowed = self.cfg.allowed_users
        if allowed:
            return user_id in allowed
        bound = self.repo.kv_get(f"bound_user_{bot}")
        if bound is None:
            self.repo.kv_set(f"bound_user_{bot}", user_id)
            logger.info(f"[{bot}] 绑定首个用户 open_id={user_id}")
            return True
        return bound == user_id

    # ---- 命令 ----

    def check_shell(self, command: str) -> tuple[str, str | None]:
        """返回 (verdict, token)。verdict: ok | confirm | reject；confirm 附带 token。"""
        cmd_lower = command.strip().lower()
        for pat in self._danger_res:
            if pat.search(cmd_lower):
                return "confirm", None
        if not self.cfg.shell_allow_prefixes:
            return "reject", None
        if any(cmd_lower.startswith(p.lower()) for p in self.cfg.shell_allow_prefixes):
            return "ok", None
        return "confirm", None

    def create_confirmation(self, bot: str, user_id: str, command: str) -> str:
        return self.repo.create_confirmation(bot, user_id, command, "")

    def consume_confirmation(self, token: str, user_id: str) -> dict | None:
        return self.repo.consume_confirmation(token, user_id)
