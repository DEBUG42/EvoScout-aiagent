"""记忆升级测试：store 新能力 + MemoryManager（fake client）+ 泄漏修复。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.core.agent_loop import LoopConfig, run_loop
from app.core.model import Response, ToolCall
from app.core.tools import ToolContext, build_base_tools
from app.memory.manager import MemoryManager
from app.memory.store import MemoryStore, MemoryType
from tests.test_m2 import MockProvider


class FakeClient:
    """脚本化 DeepSeekClient（只实现 chat_json/can_call）。"""

    def __init__(self, results=None, can_call=True):
        self.results = list(results or [])
        self.can_call_flag = can_call
        self.calls: list[dict] = []

    def can_call(self, bot: str) -> bool:
        return self.can_call_flag

    def chat_json(self, system: str, user: str, bot: str = "") -> dict:
        self.calls.append({"system": system, "user": user, "bot": bot})
        return self.results.pop(0) if self.results else {"memories": [], "session_line": ""}


class TestStoreUpgrades:
    def test_updated_at_written_and_backfilled(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("m1", "内容", MemoryType.USER, "描述")
        meta = s.read_memory("m1").meta
        assert meta.updated_at      # 写入自动记录
        # 老文件无 updated_at：手动剥掉后 mtime 回填
        from datetime import date
        raw = (tmp_path / "a" / "m1.md").read_text(encoding="utf-8")
        raw = raw.replace(f"updated_at: {meta.updated_at}\n", "")
        (tmp_path / "a" / "m1.md").write_text(raw, encoding="utf-8")
        backfilled = s.read_memory("m1").meta
        assert backfilled.updated_at == date.today().isoformat()

    def test_recency_order(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("old", "1")
        s.write_memory("new", "2")
        from datetime import date, timedelta
        raw = (tmp_path / "a" / "old.md").read_text(encoding="utf-8")
        yesterday = (date.today() - timedelta(days=10)).isoformat()
        (tmp_path / "a" / "old.md").write_text(
            raw.replace(f"updated_at: {date.today().isoformat()}", f"updated_at: {yesterday}"),
            encoding="utf-8")
        names = [m.name for m in s.list_memories()]
        assert names[0] == "new"        # 最近更新在前

    def test_build_prompt_selected(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("rel", "相关内容", MemoryType.USER, "相关")
        s.write_memory("irr", "无关内容", MemoryType.PROJECT, "无关")
        prompt = s.build_prompt(selected=["rel"])
        assert "相关内容" in prompt and "无关内容" not in prompt
        # 默认 = 最近前 N
        prompt_all = s.build_prompt()
        assert "相关内容" in prompt_all and "无关内容" in prompt_all
        # 空 selected = 不注入正文
        prompt_empty = s.build_prompt(selected=[])
        assert "相关内容" not in prompt_empty

    def test_freshness_caveat(self, tmp_path: Path):
        from datetime import date, timedelta
        s = MemoryStore(tmp_path, "a")
        s.write_memory("m", "内容")
        old = (date.today() - timedelta(days=40)).isoformat()
        raw = (tmp_path / "a" / "m.md").read_text(encoding="utf-8")
        (tmp_path / "a" / "m.md").write_text(
            raw.replace(f"updated_at: {date.today().isoformat()}", f"updated_at: {old}"),
            encoding="utf-8")
        prompt = s.build_prompt()
        assert "可能已过时" in prompt and "40 天前" in prompt

    def test_append_preserves_meta(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("m", "第一行", MemoryType.FEEDBACK, "反馈", "hook")
        s.append_memory("m", "第二行")
        mem = s.read_memory("m")
        assert mem.meta.type == MemoryType.FEEDBACK
        assert "第一行" in mem.content and "第二行" in mem.content

    def test_archive_moves_to_subdir(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("m", "内容")
        assert s.archive_memory("m")
        assert (tmp_path / "a" / "archive" / "m.md").exists()
        assert s.read_memory("m") is None
        assert "m" not in [x.name for x in s.list_memories()]

    def test_sessions_rolling(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        for i in range(7):
            s.append_session(f"- [2026-08-13] 对话{i}")
        assert len(s.read_sessions()) == 5
        assert "对话2" in s.read_sessions()[0]   # 最旧的被滚掉

    def test_concurrent_appends(self, tmp_path: Path):
        s = MemoryStore(tmp_path, "a")
        s.write_memory("m", "start")
        errors = []

        def worker(i):
            try:
                s.append_memory("m", f"line{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        content = s.read_memory("m").content
        assert all(f"line{i}" in content for i in range(10))


class TestMemoryManager:
    @pytest.fixture
    def env(self, tmp_path: Path):
        store = MemoryStore(tmp_path, "mgr")
        store.write_memory("user-profile", "用户是 AI agent 研究者", MemoryType.USER, "用户背景")
        store.write_memory("cooking", "用户喜欢川菜", MemoryType.USER, "饮食偏好")
        store.write_memory("p1", "A", MemoryType.PROJECT, "项目A")
        store.write_memory("p2", "B", MemoryType.PROJECT, "项目B")
        store.write_memory("p3", "C", MemoryType.PROJECT, "项目C")
        store.write_memory("p4", "D", MemoryType.PROJECT, "项目D")
        return tmp_path, store

    def test_recall_selects_and_filters(self, env):
        tmp_path, store = env
        client = FakeClient([{"selected_memories": ["user-profile", "幻觉条目", "user-profile"]}])
        mgr = MemoryManager(tmp_path, "mgr", client)
        names = mgr.relevant_recall("用户做什么研究")
        assert names == ["user-profile"]          # 幻觉过滤 + 去重

    def test_recall_empty_selection(self, env):
        tmp_path, store = env
        client = FakeClient([{"selected_memories": []}])
        mgr = MemoryManager(tmp_path, "mgr", client)
        assert mgr.relevant_recall("无关问题") == []

    def test_recall_no_client_or_budget(self, env):
        tmp_path, store = env
        assert MemoryManager(tmp_path, "mgr", None).relevant_recall("q") is None
        client = FakeClient([], can_call=False)
        assert MemoryManager(tmp_path, "mgr", client).relevant_recall("q") is None

    def test_recall_llm_failure_returns_none(self, env):
        tmp_path, store = env
        client = FakeClient([], can_call=True)

        def boom(system, user, bot=""):
            raise __import__("app.ai.client", fromlist=["AiError"]).AiError("挂了")

        client.chat_json = boom
        assert MemoryManager(tmp_path, "mgr", client).relevant_recall("q") is None

    def test_post_chat_create_and_session(self, env):
        tmp_path, store = env
        client = FakeClient([{
            "memories": [{"action": "create", "name": "new-pref", "type": "user",
                          "description": "新偏好", "content": "用户喜欢用表格展示数据"}],
            "session_line": "用户问了数据展示偏好",
        }])
        mgr = MemoryManager(tmp_path, "mgr", client)
        mgr.post_chat("怎么展示数据", "可以用表格")
        assert store.read_memory("new-pref")
        assert "数据展示偏好" in store.read_sessions()[0]

    def test_post_chat_update_merges(self, env):
        tmp_path, store = env
        client = FakeClient([{
            "memories": [{"action": "update", "name": "user-profile", "type": "user",
                          "description": "", "content": "最近在学 RAG 评测"}],
            "session_line": "s",
        }])
        MemoryManager(tmp_path, "mgr", client).post_chat("q", "a")
        mem = store.read_memory("user-profile")
        assert "AI agent 研究者" in mem.content and "最近在学 RAG 评测" in mem.content

    def test_post_chat_llm_failure_no_side_effect(self, env):
        tmp_path, store = env
        client = FakeClient([], can_call=True)

        def boom(system, user, bot=""):
            raise __import__("app.ai.client", fromlist=["AiError"]).AiError("挂了")

        client.chat_json = boom
        before = {m.name for m in store.list_memories()}
        MemoryManager(tmp_path, "mgr", client).post_chat("q", "a")
        assert {m.name for m in store.list_memories()} == before

    def test_consolidate_merge_and_archive(self, env):
        tmp_path, store = env
        client = FakeClient([{
            "ops": [
                {"action": "merge", "keep": "p1", "absorb": ["p2"]},
                {"action": "archive", "target": "p4"},
                {"action": "update_desc", "target": "p3", "description": "新描述"},
            ]
        }])
        mgr = MemoryManager(tmp_path, "mgr", client)
        ops = mgr.consolidate()
        assert len(ops) == 3
        assert store.read_memory("p2") is None
        assert "B" in store.read_memory("p1").content
        assert store.read_memory("p4") is None
        assert store.read_memory("p3").meta.description == "新描述"

    def test_consolidate_dry_run(self, env):
        tmp_path, store = env
        client = FakeClient([{"ops": [{"action": "archive", "target": "p4"}]}])
        mgr = MemoryManager(tmp_path, "mgr", client)
        ops = mgr.consolidate(dry_run=True)
        assert len(ops) == 1
        assert store.read_memory("p4") is not None   # 未实际归档


class TestLeakFix:
    @pytest.fixture
    def ctx(self, tmp_path: Path):
        return ToolContext(root_dir=tmp_path, memory_dir=tmp_path / "mem", data_dir=tmp_path / "data")

    def test_leaked_reply_gets_corrected(self, ctx):
        tools = build_base_tools(ctx)
        provider = MockProvider([
            Response(text='<invoke name="shell">\n<parameter name="command">dir</parameter>\n</invoke>'),
            Response(text="已按自然语言说明目录内容"),
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools)
        result = run_loop(cfg, "列目录", model_spec="mock/x")
        assert result.text == "已按自然语言说明目录内容"
        # 修正轮无工具
        assert provider.received_tools[-1] is None

    def test_persistent_leak_replaced(self, ctx):
        tools = build_base_tools(ctx)
        provider = MockProvider([
            Response(text='<tool_call>{"tool": "x"}</tool_call>'),
            Response(text='<invoke name="y"></invoke>'),
            Response(text='{"tool": "z"}'),
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools)
        result = run_loop(cfg, "x", model_spec="mock/x")
        assert "格式异常" in result.text

    def test_normal_reply_untouched(self, ctx):
        tools = build_base_tools(ctx)
        provider = MockProvider([Response(text="正常回答，包含 <div> 标签也无妨")])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools)
        result = run_loop(cfg, "x", model_spec="mock/x")
        assert result.text == "正常回答，包含 <div> 标签也无妨"
