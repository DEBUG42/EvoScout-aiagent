"""M5 测试：安全策略 + 命令注册与分发（mock 飞书）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.lark_channel import IncomingMessage
from app.bots.runtime import build_command_registry
from app.commands.base import CommandContext, CommandRegistry
from app.commands.security import SecurityPolicy
from app.config.settings import Settings
from app.storage.db import DB
from app.storage.repo import Repo
from app.storage.schema import init_db


def make_msg(text: str) -> IncomingMessage:
    return IncomingMessage(
        event_id="e1", message_id="m1", user_id="u1", chat_id="", chat_type="p2p", text=text
    )


class FakeLark:
    def __init__(self):
        self.sent: list = []

    def send_text(self, text, chat_type="p2p", chat_id="", user_id=""):
        self.sent.append(("text", text))
        return "msg_x"

    def send_post(self, title, lines, chat_type="p2p", chat_id="", user_id=""):
        self.sent.append(("post", title))
        return "msg_x"

    def send_card(self, card, chat_type="p2p", chat_id="", user_id=""):
        self.sent.append(("card", card))
        return "msg_x"


class TestSecurity:
    @pytest.fixture
    def policy(self, tmp_path: Path):
        repo = Repo(DB(tmp_path / "h.db"))
        init_db(DB(tmp_path / "h.db"))
        settings = Settings(security={
            "allowed_users": [],
            "shell_allow_prefixes": ["git status", "nvidia-smi"],
            "danger_patterns": ["rm ", "git reset --hard", "shutdown", "del "],
        })
        return SecurityPolicy(repo, settings), repo

    def test_bind_first_user(self, policy):
        policy_obj, _ = policy
        assert policy_obj.check_user("b1", "userA")
        assert policy_obj.check_user("b1", "userA")
        assert not policy_obj.check_user("b1", "userB")   # 绑定后他人拒绝
        assert policy_obj.check_user("b2", "userB")       # 不同 bot 独立绑定

    def test_shell_verdicts(self, policy):
        policy_obj, _ = policy
        assert policy_obj.check_shell("git status")[0] == "ok"
        assert policy_obj.check_shell("nvidia-smi -L")[0] == "ok"
        assert policy_obj.check_shell("rm -rf /tmp/x")[0] == "confirm"
        assert policy_obj.check_shell("echo hello")[0] == "confirm"   # 不在白名单
        assert policy_obj.check_shell("git reset --hard HEAD")[0] == "confirm"

    def test_confirmation_flow(self, policy):
        policy_obj, repo = policy
        token = policy_obj.create_confirmation("b1", "userA", "rm x")
        assert policy_obj.consume_confirmation(token, "userB") is None
        rec = policy_obj.consume_confirmation(token, "userA")
        assert rec and rec["status"] == "confirmed"


class TestCommands:
    @pytest.fixture
    def env(self, tmp_path: Path):
        db = DB(tmp_path / "h.db")
        init_db(db)
        repo = Repo(db)
        reg = build_command_registry()
        lark = FakeLark()
        settings = Settings(security={
            "allowed_users": [],
            "shell_allow_prefixes": ["git status", "nvidia-smi", "python --version", "pip list"],
            "danger_patterns": ["rm ", "git reset --hard", "shutdown", "del "],
        })
        security = SecurityPolicy(repo, settings)
        return reg, repo, lark, settings, security

    def _ctx(self, env, text):
        reg, repo, lark, settings, security = env
        replies: list = []

        def reply(t):
            replies.append(t)

        return CommandContext(
            msg=make_msg(text), args=[], bot_name="tbot", reply=reply,
            reply_post=lambda title, lines: replies.append(title),
            repo=repo, registry=None, settings=settings, lark=lark,
            ai_client=None, memory_dir=Path("."), executor=None, security=security,
        ), replies

    def test_help_lists_all(self, env):
        ctx, replies = self._ctx(env, "/help")
        reg, *_ = env
        status = reg.dispatch(ctx)
        assert status == "ok"
        assert "/status" in replies[0] and "/shell" in replies[0]

    def test_unknown_command(self, env):
        ctx, replies = self._ctx(env, "/nope")
        reg, *_ = env
        reg.dispatch(ctx)
        assert "未知命令" in replies[0]

    def test_non_command_ignored(self, env):
        ctx, replies = self._ctx(env, "随便聊聊")
        reg, *_ = env
        assert reg.dispatch(ctx) == "ignored"
        assert not replies

    def test_sub_add_del_list(self, env):
        reg, repo, *_ = env
        ctx, replies = self._ctx(env, "/sub add cs.RO")
        reg.dispatch(ctx)
        assert "已添加" in replies[0]
        ctx2, replies2 = self._ctx(env, "/sub list")
        reg.dispatch(ctx2)
        assert "cs.RO" in replies2[0]
        subs = repo.list_subscriptions("tbot")
        assert subs[0]["value"] == "cs.RO"
        ctx3, replies3 = self._ctx(env, f"/sub del {subs[0]['id']}")
        reg.dispatch(ctx3)
        assert "已删除" in replies3[0]

    def test_run_unknown_script(self, env):
        ctx, replies = self._ctx(env, "/run nope")
        reg, *_ = env
        reg.dispatch(ctx)
        assert "不存在" in replies[0]

    def test_shell_confirm_flow(self, env):
        reg, repo, lark, settings, security = env
        ctx, replies = self._ctx(env, "/shell rm -rf x")
        reg.dispatch(ctx)
        assert "需确认" in replies[0]
        cards = [s for s in lark.sent if s[0] == "card"]
        assert len(cards) == 1

    def test_shell_allowed_executes(self, env):
        reg, *_ = env
        ctx, replies = self._ctx(env, "/shell python --version")
        reg.dispatch(ctx)
        assert replies  # 有输出

    def test_memory_add(self, env, tmp_path: Path):
        reg, repo, lark, settings, security = env
        ctx, replies = self._ctx(env, "/memory add 记住我")
        ctx.memory_dir = tmp_path
        reg.dispatch(ctx)
        assert "已追加" in replies[0]
        from app.memory.store import MemoryStore
        store = MemoryStore(tmp_path, "tbot")
        assert "记住我" in store.read_memory("notes").content


class TestMasterRuntime:
    def test_run_master_loop_returns_loop_result(self, tmp_path: Path):
        """回归：_run_master_loop 必须返回 LoopResult（_master_flow 依赖 .text/.aborted）。"""
        from app.bots.runtime import BotRuntime
        from app.agents.defs import parse_agent_md
        from app.core.model import Response
        from app.core.tools import ToolContext, build_base_tools
        from tests.test_m2 import MockProvider

        agent = parse_agent_md("---\nname: master\nrole: master\nmodel: mock/x\nmax_turns: 3\n---\nprompt")
        db = DB(tmp_path / "h.db")
        init_db(db)
        repo = Repo(db)
        providers = {"mock": MockProvider([Response(text="你好")])}
        rt = BotRuntime(agent, Settings(), repo, None, None, providers,
                        build_base_tools(ToolContext(root_dir=tmp_path, memory_dir=tmp_path, data_dir=tmp_path)),
                        None, tmp_path, None)
        import threading
        result = rt._run_master_loop("hi", threading.Event())
        assert hasattr(result, "text") and hasattr(result, "aborted")
        assert result.text == "你好"
