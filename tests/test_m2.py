"""M2 测试：agent 循环（mock provider）+ 子进程封装 + 工具注册。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.agent_loop import LoopConfig, run_loop
from app.core.model import Response, ToolCall
from app.core.subprocess import run_command
from app.core.tools import ToolContext, ToolRegistry, build_base_tools


class MockProvider:
    """脚本化 provider：按预设响应序列返回，记录收到的消息与工具参数。"""
    name = "mock"

    def __init__(self, script: list[Response]):
        self.script = list(script)
        self.received_messages: list[list[dict]] = []
        self.received_tools: list[list | None] = []

    def chat(self, messages, tools, model):
        self.received_messages.append(list(messages))
        self.received_tools.append(tools)
        return self.script.pop(0) if self.script else Response(text="(结束)")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(root_dir=tmp_path, memory_dir=tmp_path / "mem", data_dir=tmp_path / "data")


@pytest.fixture
def tools(ctx: ToolContext) -> ToolRegistry:
    return build_base_tools(ctx)


class TestAgentLoop:
    def test_single_tool_call_then_answer(self, ctx, tools):
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id="1", name="write_file", arguments={
                "path": "out.txt", "content": "hello"})]),
            Response(text="已写好"),
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, default_model="x")
        result = run_loop(cfg, "创建 out.txt", model_spec="mock/x")
        assert result.text == "已写好"
        assert result.turns == 2
        assert (ctx.root_dir / "out.txt").read_text(encoding="utf-8") == "hello"
        # 验证 tool 消息回填
        assert result.tool_calls[0][0] == "write_file"

    def test_max_turns_cap(self, ctx, tools):
        """轮数用尽 → wrap-up 无工具总结（脚本耗尽则降级说明文本）。"""
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id=str(i), name="list_dir", arguments={"path": f"d{i}"})])
            for i in range(5)
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, max_turns=3)
        result = run_loop(cfg, "列目录", model_spec="mock/x")
        assert result.turns == 3
        assert result.stop_reason == "max_turns"
        assert result.text   # wrap-up 或降级文本，不再是生硬的"达到上限"

    def test_unknown_tool_returns_error(self, ctx, tools):
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id="1", name="no_such_tool", arguments={})]),
            Response(text="收到错误"),
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools)
        result = run_loop(cfg, "x", model_spec="mock/x")
        assert "未知工具" in result.tool_calls[0][2]

    def test_destructive_confirm_rejected(self, ctx, tools):
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id="1", name="shell", arguments={"command": "del x"})]),
            Response(text="知道了"),
        ])
        cfg = LoopConfig(
            provider_map={"mock": provider}, tools=tools,
            confirm=lambda tool, args: False,   # 全部拒绝
        )
        result = run_loop(cfg, "删文件", model_spec="mock/x")
        assert "拒绝" in result.tool_calls[0][2]

    def test_tool_filtering(self, ctx, tools):
        provider = MockProvider([Response(text="ok")])
        cfg = LoopConfig(
            provider_map={"mock": provider}, tools=tools,
            allowed_tools=["read_file"], disallowed_tools=["shell"],
        )
        run_loop(cfg, "x", model_spec="mock/x")
        sent_tools = provider.received_messages[0]  # system/user 消息不含 tools，检查 chat 参数
        # tools 参数在 chat 调用时传入，MockProvider 记录了 messages；tool_schemas 校验走 schemas_for
        schemas = tools.schemas_for(["read_file"], ["shell"])
        names = {s["name"] for s in schemas}
        assert names == {"read_file"}

    def test_abort_event(self, ctx, tools):
        import threading
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id="1", name="list_dir", arguments={})]),
            Response(text="不会到达"),
        ])
        abort = threading.Event()
        abort.set()
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, abort_event=abort)
        result = run_loop(cfg, "x", model_spec="mock/x")
        assert result.aborted


class TestSubprocess:
    def test_echo(self):
        r = run_command(["cmd", "/c", "echo hello"], timeout=10)
        assert r.ok and "hello" in r.stdout

    def test_timeout_kills(self):
        r = run_command(
            ["cmd", "/c", "ping -n 30 127.0.0.1 > nul"], timeout=2
        )
        assert r.timed_out
        assert r.returncode != 0

    def test_nonexistent(self):
        r = run_command(["definitely_not_a_command_xyz"], timeout=5)
        assert r.returncode == 127

    def test_output_file(self, tmp_path: Path):
        out = tmp_path / "o.txt"
        run_command(["cmd", "/c", "echo 数据"], timeout=10, output_file=out)
        assert "数据" in out.read_text(encoding="utf-8")


class TestTools:
    def test_edit_file_unique(self, ctx, tools):
        (ctx.root_dir / "f.txt").write_text("aaa\nbbb\n", encoding="utf-8")
        r = tools.get("edit_file").call({"path": "f.txt", "old_string": "bbb", "new_string": "ccc"})
        assert "已修改" in r
        assert (ctx.root_dir / "f.txt").read_text(encoding="utf-8") == "aaa\nccc\n"

    def test_edit_file_not_unique(self, ctx, tools):
        (ctx.root_dir / "f.txt").write_text("xx\nxx\n", encoding="utf-8")
        r = tools.get("edit_file").call({"path": "f.txt", "old_string": "xx", "new_string": "yy"})
        assert "出现 2 次" in r

    def test_memory_tools(self, ctx, tools):
        tools.get("memory_write").call({"agent": "a", "name": "n1", "content": "内容1"})
        listing = tools.get("memory_list").call({"agent": "a"})
        assert "n1" in listing
        read = tools.get("memory_read").call({"agent": "a", "name": "n1"})
        assert "内容1" in read

    def test_read_file_line_numbers(self, ctx, tools):
        (ctx.root_dir / "x.txt").write_text("l1\nl2\n", encoding="utf-8")
        r = tools.get("read_file").call({"path": "x.txt"})
        assert "1\tl1" in r and "2\tl2" in r


class TestLoopTermination:
    """参考 Claude Code / OpenAI SDK / AutoGPT 的终止机制测试。"""

    def test_max_turns_forces_wrapup(self, ctx, tools):
        """轮数用尽后追加一次无工具总结调用，返回的是总结文本而非'达到上限'。"""
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id="1", name="list_dir", arguments={})]),
            Response(tool_calls=[ToolCall(id="2", name="list_dir", arguments={"path": "x"})]),
            Response(text="基于已做工作的最终总结"),
        ])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, max_turns=2)
        result = run_loop(cfg, "列目录", model_spec="mock/x")
        assert result.text == "基于已做工作的最终总结"
        assert result.stop_reason == "max_turns"
        # wrap-up 调用不带工具
        assert provider.received_tools[-1] is None
        # wrap-up 提示词要求直接给结论
        assert "不要再调用工具" in provider.received_messages[-1][-1]["content"]

    def test_repeat_detection_triggers_wrapup(self, ctx, tools):
        """相同工具+参数重复 3 次 → 判死循环提前收尾。"""
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id=str(i), name="shell",
                                          arguments={"command": "ping x"})])
            for i in range(3)
        ] + [Response(text="网络不通，给出已有结论")])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, max_turns=20)
        result = run_loop(cfg, "测试", model_spec="mock/x")
        assert result.stop_reason == "loop_detected"
        assert result.text == "网络不通，给出已有结论"
        assert len(result.tool_calls) == 2    # 第 3 次重复在调用前就被拦截

    def test_nudge_injected_when_close_to_cap(self, ctx, tools):
        """剩余 2 轮时注入收尾提醒。"""
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id=str(i), name="list_dir", arguments={"path": f"d{i}"})])
            for i in range(4)
        ] + [Response(text="done")])
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, max_turns=5)
        run_loop(cfg, "x", model_spec="mock/x")
        all_contents = [m["content"] for msgs in provider.received_messages for m in msgs
                        if m.get("content")]
        assert any("剩余轮数不多" in c for c in all_contents)

    def test_wrapup_failure_fallback(self, ctx, tools):
        """wrap-up 调用失败（脚本耗尽）时降级为说明文本，不崩溃。"""
        provider = MockProvider([
            Response(tool_calls=[ToolCall(id=str(i), name="list_dir", arguments={})])
            for i in range(6)
        ])   # 脚本耗尽：wrap-up 时 MockProvider 返回默认 "(结束)"
        cfg = LoopConfig(provider_map={"mock": provider}, tools=tools, max_turns=3)
        result = run_loop(cfg, "x", model_spec="mock/x")
        assert result.stop_reason in ("max_turns", "loop_detected")
        assert result.text
