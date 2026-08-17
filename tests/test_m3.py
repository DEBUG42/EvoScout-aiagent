"""M3 测试：文件邮箱 + 子代理管理 + 主控工具。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.agents.defs import AgentDefinition
from app.agents.mailbox import Mailbox
from app.agents.master_tools import build_master_tools
from app.agents.registry import AgentRegistry
from app.agents.subagent import SubagentManager
from app.core.model import Response, ToolCall
from app.core.tools import ToolContext, build_base_tools
from tests.test_m2 import MockProvider


class TestMailbox:
    def test_send_poll(self, tmp_path: Path):
        mb = Mailbox(tmp_path)
        mb.send("bot1", "任务1", sender="master")
        mb.send("bot1", "任务2", sender="master")
        unread = mb.poll("bot1")
        assert len(unread) == 2 and unread[0].text == "任务1"
        assert mb.poll("bot1") == []          # 已标已读
        assert mb.poll("bot2") == []

    def test_persist_and_corrupt_recovery(self, tmp_path: Path):
        mb = Mailbox(tmp_path)
        mb.send("a", "msg")
        mb2 = Mailbox(tmp_path)               # 新实例读同一目录
        assert len(mb2.poll("a")) == 1
        (tmp_path / "a.json").write_text("{broken", encoding="utf-8")
        assert mb2.poll("a") == []            # 损坏文件重置不崩溃


@pytest.fixture
def master_env(tmp_path: Path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "master.md").write_text(
        "---\nname: master\nrole: master\nmax_turns: 5\n---\n你是主控。", encoding="utf-8")
    (agents_dir / "researcher.md").write_text(
        "---\nname: researcher\nrole: subagent\nmax_turns: 5\n---\n你是研究子代理。", encoding="utf-8")
    registry = AgentRegistry(agents_dir)
    ctx = ToolContext(root_dir=tmp_path, memory_dir=tmp_path / "mem", data_dir=tmp_path / "data")
    tools = build_base_tools(ctx)
    providers = {"mock": MockProvider([
        Response(tool_calls=[ToolCall(id="1", name="list_dir", arguments={})]),
        Response(text="研究完成"),
    ])}
    executor = ThreadPoolExecutor(max_workers=2)
    mailbox = Mailbox(tmp_path / "data" / "mailboxes")
    subagents = SubagentManager(executor, registry, tools, providers, tmp_path / "data", "mock-x")
    for t in build_master_tools(ctx, registry, subagents, mailbox):
        tools.register(t)
    return ctx, registry, tools, mailbox, subagents


class TestSubagents:
    def test_builtin_subagent_flow(self, master_env):
        ctx, registry, tools, mailbox, subagents = master_env
        task = subagents.spawn("researcher", "调研一下目录结构", model="mock/x")
        assert task.status == "completed"
        assert "研究完成" in task.result
        assert task.turns == 2
        assert "<task-notification>" in task.notification_xml()

    def test_background_task(self, master_env):
        ctx, registry, tools, mailbox, subagents = master_env
        task = subagents.spawn("researcher", "后台任务", model="mock/x", background=True)
        assert task.wait(timeout=10)
        assert subagents.get(task.id).status == "completed"

    def test_unknown_agent_falls_back(self, master_env):
        ctx, registry, tools, mailbox, subagents = master_env
        task = subagents.spawn("nobody", "任意任务", model="mock/x")
        assert task.status == "completed"


class TestMasterTools:
    def test_list_agents(self, master_env):
        ctx, registry, tools, _, _ = master_env
        out = tools.get("list_agents").call({})
        assert "master" in out and "researcher" in out

    def test_modify_agent_hot_reload(self, master_env):
        ctx, registry, tools, _, _ = master_env
        out = tools.get("modify_agent").call(
            {"name": "researcher", "field": "description", "value": "新描述"})
        assert "已修改" in out
        assert registry.get("researcher").description == "新描述"

    def test_modify_subscriptions(self, master_env):
        ctx, registry, tools, _, _ = master_env
        tools.get("modify_agent").call({
            "name": "researcher", "field": "subscriptions",
            "value": json.dumps({"arxiv": ["cs.AI"]}),
        })
        assert registry.get("researcher").subscriptions == {"arxiv": ["cs.AI"]}
        # 落盘持久化
        raw = (ctx.root_dir / "agents" / "researcher.md").read_text(encoding="utf-8")
        assert "cs.AI" in raw

    def test_send_to_agent_and_poll(self, master_env):
        ctx, registry, tools, mailbox, _ = master_env
        tools.get("send_to_agent").call({"to": "researcher", "text": "你好"})
        out = tools.get("poll_mailbox").call({"agent": "researcher"})
        assert "你好" in out

    def test_modify_invalid_field(self, master_env):
        ctx, registry, tools, _, _ = master_env
        out = tools.get("modify_agent").call(
            {"name": "researcher", "field": "nonsense", "value": "x"})
        assert "不支持的字段" in out
