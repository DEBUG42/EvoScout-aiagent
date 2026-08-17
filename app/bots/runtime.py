"""Bot 运行时：每 bot 一个实例——飞书 ws 收消息 → 安全校验 → 命令分发/主控对话 → 回复。"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from loguru import logger

from app.bots.lark_channel import IncomingMessage, LarkChannel
from app.commands import memory_cmds, paper_cmds, run_cmds, system_cmds
from app.commands.base import CommandContext, CommandRegistry
from app.commands.security import SecurityPolicy
from app.core.agent_loop import LoopConfig, run_loop


def build_command_registry() -> CommandRegistry:
    reg = CommandRegistry()
    system_cmds.register(reg)
    run_cmds.register(reg)
    paper_cmds.register(reg)
    memory_cmds.register(reg)
    return reg


class BotRuntime:
    def __init__(self, agent_def, settings, repo, registry, executor, providers,
                 tools, ai_client, memory_dir, mailbox):
        self.agent = agent_def
        self.name = agent_def.name
        self.settings = settings
        self.repo = repo
        self.registry = registry
        self.executor = executor
        self.providers = providers
        self.tools = tools
        self.ai_client = ai_client
        self.memory_dir = memory_dir
        self.mailbox = mailbox
        self.security = SecurityPolicy(repo, settings)
        self.commands = build_command_registry()
        self.lark: LarkChannel | None = None
        self._event_dedup_lock = threading.Lock()
        # 主控聊天专用线程池：多任务并发，长任务不挤占命令线程池
        from concurrent.futures import ThreadPoolExecutor
        self._max_chats = max(1, int(settings.app.max_concurrent_chats))
        self._master_executor = ThreadPoolExecutor(
            max_workers=self._max_chats, thread_name_prefix=f"master-{self.name}")
        self._active_chats = 0
        self._active_lock = threading.Lock()
        self._master_abort = threading.Event()
        self._chat_timeout = 180.0
        # 记忆：管理 + 后台单线程（抽取/整理串行，与命令池、主控池隔离）
        from app.memory.manager import MemoryManager
        self._mem_manager = MemoryManager(self.memory_dir, self.name, self.ai_client)
        self._mem_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"mem-{self.name}")

    # ---- 启动 ----

    def start(self) -> None:
        app_id, secret = self.settings.lark_credentials(self.name)
        self.lark = LarkChannel(
            app_id, secret, self.name,
            on_message=self._on_message,
            on_card_action=self._on_card_action,
        )
        self.lark.start()
        logger.info(f"[{self.name}] bot 运行时已启动")

    def stop(self) -> None:
        if self.lark:
            self.lark.stop()

    # ---- 事件入口（必须 3 秒内返回）----

    def _on_message(self, msg: IncomingMessage) -> None:
        if not self.repo.claim_event(msg.event_id):
            logger.debug(f"[{self.name}] 重复事件 {msg.event_id}，忽略")
            return
        self.executor.submit(self._handle_message, msg)

    def _on_card_action(self, msg: IncomingMessage) -> None:
        if not self.repo.claim_event(msg.event_id):
            return
        self.executor.submit(self._handle_card_action, msg)

    # ---- 异步处理 ----

    def _handle_message(self, msg: IncomingMessage) -> None:
        try:
            if not self.security.check_user(self.name, msg.user_id):
                logger.warning(f"[{self.name}] 非授权用户 {msg.user_id} 消息，忽略")
                self.repo.log_command(self.name, msg.user_id, msg.chat_id, msg.text, "",
                                      "rejected", "非授权用户")
                return
            text = msg.text
            if text and not text.startswith("/") and self.agent.is_master:
                self._route_master_chat(msg)     # 非阻塞：不占命令线程池
                return
            self._reply_text(msg, self._process_text(msg))
        except Exception:
            logger.exception(f"[{self.name}] 消息处理异常")

    def _route_master_chat(self, msg: IncomingMessage) -> None:
        """主控自然语言：并发上限内立即受理，超限回复忙提示（主线程池保持空闲跑命令）。"""
        with self._active_lock:
            if self._active_chats >= self._max_chats:
                busy = True
            else:
                busy = False
        if busy:
            self._reply_text(
                msg,
                f"主控正在处理 {self._active_chats} 个任务（上限 {self._max_chats}），"
                f"请稍候再发（/status 等命令不受影响）",
            )
            return
        self._master_executor.submit(self._master_flow, msg)

    def _master_flow(self, msg: IncomingMessage) -> None:
        """在 master 专用线程上执行：墙钟定时器 + abort 事件实现超时中止。

        不在开头回复打扰用户；仅当处理超过 60s 时推送"仍在处理中"。
        """
        with self._active_lock:
            self._active_chats += 1
        self._master_abort.clear()
        abort = threading.Event()          # 每任务独立中止事件
        progress_timer = threading.Timer(
            60, lambda: self._reply_text(msg, "仍在处理中，请稍候…")
        )
        abort_timer = threading.Timer(self._chat_timeout, abort.set)
        progress_timer.start()
        abort_timer.start()
        try:
            if not self.providers:
                self._reply_text(msg, "模型未配置（缺 DEEPSEEK_API_KEY），请使用 /help 查看命令")
                return
            result = self._run_master_loop(msg.text, abort)
            text = result.text if result.text else "(无回复)"
            if result.aborted and not result.text:
                text = f"处理超时（>{int(self._chat_timeout)}s）已中止。建议换个更具体的问题，或用 /help 看命令。"
            reply_text = text
            # 最终防线：任何路径都不允许工具语法泄漏到用户
            from app.core.agent_loop import _has_leak
            if _has_leak(reply_text):
                logger.warning(f"[{self.name}] 最终防线拦截泄漏回复: {reply_text[:120]!r}")
                reply_text = "（结果格式异常，已修正——请重新提问或换个说法）"
            self._reply_text(msg, reply_text[:1800])
            # 对话后后台记忆抽取（不阻塞、不影响下一条消息）
            if text and not (result.aborted and not result.text):
                self._mem_executor.submit(self._post_chat_memory, msg.text, text)
        except Exception as e:
            logger.exception(f"[{self.name}] 主控聊天异常")
            self._reply_text(msg, f"主控处理出错: {type(e).__name__}: {e}")
        finally:
            progress_timer.cancel()
            abort_timer.cancel()
            with self._active_lock:
                self._active_chats -= 1

    def _post_chat_memory(self, user_text: str, reply_text: str) -> None:
        try:
            self._mem_manager.post_chat(user_text, reply_text)
        except Exception:
            logger.exception(f"[{self.name}] 对话后记忆抽取异常")

    def _process_text(self, msg: IncomingMessage) -> str:
        text = msg.text
        if not text.startswith("/") and self.agent.is_master:
            # 兜底：正常路径已在 _handle_message 分流，此分支不应到达
            return self._run_master_loop(msg.text, self._master_abort).text or ""
        ctx = self._build_ctx(msg)
        return self.commands.dispatch(ctx)

    def _run_master_loop(self, user_text: str, abort: threading.Event):
        from app.memory.inject import build_memory_prompt, get_store

        # 每次对话前热加载定义：改 agents/master.md 无需重启即生效
        try:
            self.registry.reload()
            agent = self.registry.get(self.name)
        except Exception:
            agent = self.agent
        # 相关记忆召回：>5 条时让模型选（失败回退最近前 N）
        selected: list[str] | None = None
        try:
            if len(get_store(self.memory_dir, self.name).list_memories()) > 5:
                selected = self._mem_manager.relevant_recall(user_text)
        except Exception:
            logger.exception(f"[{self.name}] 记忆召回异常，回退默认注入")
            selected = None
        system_prompt = (
            agent.prompt
            + "\n\n"
            + build_memory_prompt(
                self.memory_dir, self.name,
                selected=selected, include_session=True,
            )
        )
        cfg = LoopConfig(
            provider_map=self.providers,
            tools=self.tools,
            default_model=self.settings.ai.model,
            max_turns=agent.max_turns,
            system_prompt=system_prompt,
            allowed_tools=agent.tools or None,
            disallowed_tools=agent.disallowed_tools or None,
            abort_event=abort,
            on_tool_call=lambda n, a, r: logger.info(f"[{self.name}] tool {n}: {str(a)[:80]} -> {str(r)[:80]}"),
        )
        result = run_loop(cfg, user_text, model_spec=agent.model)
        return result

    def _build_ctx(self, msg: IncomingMessage) -> CommandContext:
        def reply(text: str) -> None:
            self._reply_text(msg, text)

        def reply_post(title: str, lines: list) -> None:
            try:
                self.lark.send_post(title, lines, msg.chat_type, msg.chat_id, msg.user_id)
            except Exception as e:
                logger.warning(f"[{self.name}] post 发送失败: {e}")

        return CommandContext(
            msg=msg, args=[], bot_name=self.name, reply=reply, reply_post=reply_post,
            repo=self.repo, registry=self.registry, settings=self.settings, lark=self.lark,
            ai_client=self.ai_client, memory_dir=self.memory_dir,
            executor=self.executor, security=self.security,
        )

    def _reply_text(self, msg: IncomingMessage, text: str) -> None:
        from app.utils.text import strip_markdown
        try:
            self.lark.send_text(strip_markdown(text), msg.chat_type, msg.chat_id, msg.user_id)
        except Exception as e:
            logger.warning(f"[{self.name}] 回复失败: {e}")

    # ---- 卡片回调 ----

    def _handle_card_action(self, msg: IncomingMessage) -> None:
        value = msg.card_value
        action = value.get("action", "")
        token = value.get("token", "")
        user_id = value.get("user_id") or msg.user_id
        try:
            if action == "confirm_shell":
                self._do_confirmed(msg, token, user_id, "confirmed")
            elif action == "cancel_shell":
                self.repo.cancel_confirmation(token, user_id)
                self._reply_text(msg, "已取消")
            elif action == "translate":
                item_id = value.get("item_id")
                self._do_translate(msg, item_id)
            else:
                logger.warning(f"[{self.name}] 未知卡片动作 {action}")
        except Exception:
            logger.exception(f"[{self.name}] 卡片动作处理异常")

    def _do_confirmed(self, msg: IncomingMessage, token: str, user_id: str, status: str) -> None:
        rec = self.security.consume_confirmation(token, user_id)
        if not rec:
            self._reply_text(msg, "确认令牌无效或已过期")
            return
        command = rec["command"]
        self.repo.log_command(self.name, user_id, "", command, "", status, "")
        from app.core.subprocess import run_command

        result = run_command([command], timeout=300, shell=True)
        out = (result.stdout or "(无输出)")[:1800]
        if result.returncode != 0:
            out += f"\n退出码 {result.returncode}: {(result.stderr or '')[:300]}"
        self._reply_text(msg, f"已执行: {command}\n{out}")

    def _do_translate(self, msg: IncomingMessage, item_id) -> None:
        if not self.ai_client:
            self._reply_text(msg, "DEEPSEEK_API_KEY 未配置")
            return
        item = self.repo.get_item(int(item_id)) if str(item_id).isdigit() else None
        if not item or not item.get("summary"):
            self._reply_text(msg, "该条目无摘要")
            return
        from app.ai.prompts import TRANSLATE_SYSTEM

        text = self.ai_client.chat_text(TRANSLATE_SYSTEM, item["summary"][:3000], bot=self.name)
        self._reply_text(msg, f"【{item['title'][:60]}】\n{text[:1800]}")
