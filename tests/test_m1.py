"""M1 单元测试：记忆系统 + Agent 定义解析 + 配置加载。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.defs import AgentDefinition, dump_agent_md, load_agent, parse_agent_md
from app.agents.registry import AgentRegistry
from app.memory.store import MemoryStore, MemoryType, parse_memory_md

AGENT_MD = """---
name: testbot
description: 测试机器人
model: deepseek-chat
role: bot
channel: lark
tools: [shell, read_file]
subscriptions:
  arxiv: [cs.AI]
  keywords: [LLM, agent]
max_turns: 10
---
你是测试机器人。
"""


class TestAgentDef:
    def test_parse_full(self):
        a = parse_agent_md(AGENT_MD)
        assert a.name == "testbot"
        assert a.description == "测试机器人"
        assert a.model == "deepseek-chat"
        assert a.role == "bot"
        assert a.tools == ["shell", "read_file"]
        assert a.subscriptions == {"arxiv": ["cs.AI"], "keywords": ["LLM", "agent"]}
        assert a.max_turns == 10
        assert a.memory == "testbot"      # 默认 = name
        assert a.prompt == "你是测试机器人。"
        assert not a.is_master

    def test_missing_frontmatter(self):
        with pytest.raises(ValueError, match="frontmatter"):
            parse_agent_md("没有 frontmatter 的文本")

    def test_missing_name(self):
        with pytest.raises(ValueError, match="name"):
            parse_agent_md("---\ndescription: x\n---\n正文")

    def test_invalid_role(self):
        with pytest.raises(ValueError, match="role"):
            parse_agent_md("---\nname: x\nrole: superman\n---\n正文")

    def test_roundtrip_dump(self):
        a = parse_agent_md(AGENT_MD)
        b = parse_agent_md(dump_agent_md(a))
        assert b.name == a.name
        assert b.subscriptions == a.subscriptions
        assert b.prompt == a.prompt
        assert b.max_turns == a.max_turns

    def test_defaults(self):
        a = parse_agent_md("---\nname: x\n---\n")
        assert a.model == "inherit"
        assert a.role == "bot"
        assert a.channel == "lark"
        assert a.max_turns == 20


class TestRegistry:
    def test_load_and_master(self, tmp_path: Path):
        d = tmp_path / "agents"
        d.mkdir()
        (d / "m.md").write_text("---\nname: m\nrole: master\n---\n主控", encoding="utf-8")
        (d / "b1.md").write_text("---\nname: b1\n---\nbot1", encoding="utf-8")
        (d / "bad.md").write_text("坏文件", encoding="utf-8")
        reg = AgentRegistry(d)
        assert reg.list_names() == ["b1", "m"]
        assert reg.master().name == "m"
        assert [b.name for b in reg.bots()] == ["b1"]

    def test_reload_picks_up_changes(self, tmp_path: Path):
        d = tmp_path / "agents"
        d.mkdir()
        (d / "m.md").write_text("---\nname: m\nrole: master\n---\nv1", encoding="utf-8")
        reg = AgentRegistry(d)
        (d / "b.md").write_text("---\nname: b\n---\nnew", encoding="utf-8")
        reg.reload()
        assert "b" in reg.agents


class TestMemoryStore:
    def test_write_read_delete(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "agent1")
        s.write_memory("foo", "记忆内容", MemoryType.USER, "用户信息", "这是用户")
        mem = s.read_memory("foo")
        assert mem and mem.content == "记忆内容"
        assert mem.meta.type == MemoryType.USER
        assert "foo" in s.read_index()
        assert s.delete_memory("foo")
        assert s.read_memory("foo") is None
        assert "foo" not in s.read_index()

    def test_index_upsert_no_duplicate(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("x", "v1", description="d1")
        s.write_memory("x", "v2", description="d2")
        lines = [l for l in s.read_index().splitlines() if "[x](" in l]
        assert len(lines) == 1

    def test_index_truncate_200_lines(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        for i in range(250):
            s.write_memory(f"m{i}", f"c{i}")
        assert len(s.read_index().splitlines()) <= 202  # 200 + 警告行

    def test_build_prompt_injects(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("p", "重要偏好：中文回答", MemoryType.USER, "偏好", "hook")
        prompt = s.build_prompt()
        assert "重要偏好" in prompt
        assert "MEMORY" or "记忆" in prompt

    def test_parse_memory_frontmatter(self):
        meta, body = parse_memory_md(
            "---\nname: a-b\n"
            "description: 一条记忆\n"
            "metadata:\n  type: feedback\n---\n"
            "正文内容"
        )
        assert meta.name == "a-b"
        assert meta.type == MemoryType.FEEDBACK
        assert body == "正文内容"

    def test_append_history(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.append_history("history", ["论文A", "论文B"])
        s.append_history("history", ["论文C"])
        mem = s.read_memory("history")
        assert mem and "论文A" in mem.content and "论文C" in mem.content

    def test_missing_frontmatter_memory(self):
        with pytest.raises(ValueError):
            parse_memory_md("纯文本无 frontmatter")
