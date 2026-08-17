"""App 组装：装配全部组件（M4 版：抓取+管线+调度；M5 将接入飞书通道与命令）。"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from app.agents.mailbox import Mailbox
from app.agents.master_tools import build_master_tools
from app.agents.registry import AgentRegistry
from app.agents.subagent import SubagentManager
from app.ai.client import DeepSeekClient
from app.ai.pipeline import run_fetch_pipeline
from app.bots.runtime import BotRuntime
from app.config.settings import ROOT_DIR, Settings
from app.core.model import CodexCliProvider, build_providers
from app.core.tools import ToolContext, build_base_tools
from app.scheduler.jobs import JobDeps, build_scheduler
from app.sources.alphaxiv import AlphaxivClient
from app.sources.base import RateLimiter
from app.sources.registry import build_sources
from app.storage.db import DB
from app.storage.repo import Repo
from app.storage.schema import init_db
from app.utils.http import make_client


def console_push(bot: str, item: dict, bot_item: dict) -> str | None:
    """M4 调试推送：打印到控制台（M5 换 LarkChannel）。"""
    print(
        f"\n[push:{bot}] score={bot_item.get('score')} {item['title']}\n"
        f"  {bot_item.get('digest_zh', '')}\n"
        f"  {item['url']}"
    )
    return None


def console_digest_push(bot: str, entries: list[dict]) -> str | None:
    print(f"\n[digest:{bot}] 过去 24h 共 {len(entries)} 条:")
    for e in entries:
        print(f"  - {e['title']} ({e['url']})")
    return None


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DB(settings.data_dir / "hub.db")
        self.repo: Repo = None
        self.http = make_client()
        self.limiter = RateLimiter()
        self.registry: AgentRegistry = None
        self.tools = None
        self.providers = None
        self.codex: CodexCliProvider | None = None
        self.mailbox: Mailbox = None
        self.subagents: SubagentManager = None
        self.sources: dict = {}
        self.alphaxiv: AlphaxivClient | None = None
        self.ai_client: DeepSeekClient | None = None
        self.scheduler = None
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hub")

    def setup(self) -> "App":
        init_db(self.db)
        self.repo = Repo(self.db)
        self.registry = AgentRegistry(self.settings.agents_dir)

        # 工具 + 主控
        ctx = ToolContext(root_dir=ROOT_DIR, memory_dir=self.settings.memory_dir,
                          data_dir=self.settings.data_dir)
        self.tools = build_base_tools(ctx)
        try:
            self.providers = build_providers()
        except Exception as e:
            logger.warning(f"模型 providers 不可用（主控/subagent 将无法工作）: {e}")
            self.providers = {}
        self.codex = CodexCliProvider() if shutil.which("codex") else None
        self.mailbox = Mailbox(self.settings.data_dir / "mailboxes")
        # 子代理独立线程池：循环中的子代理不会挤占命令处理线程
        self.subagent_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="subagent")
        self.subagents = SubagentManager(
            self.subagent_executor, self.registry, self.tools, self.providers,
            self.settings.data_dir, self.settings.ai.model, self.codex,
        )
        for t in build_master_tools(ctx, self.registry, self.subagents, self.mailbox):
            self.tools.register(t)

        # 抓取 + AI
        self.sources = build_sources(self.settings, self.registry, self.http, self.limiter, self.repo)
        import os
        if os.environ.get("DEEPSEEK_API_KEY"):
            self.ai_client = DeepSeekClient(
                os.environ["DEEPSEEK_API_KEY"],
                base_url=self.settings.ai.base_url,
                model=self.settings.ai.model,
                repo=self.repo,
                max_daily_calls=self.settings.ai.max_daily_calls,
            )
        if self.settings.sources.alphaxiv.enabled:
            self.alphaxiv = AlphaxivClient(
                self.settings.sources.alphaxiv.model_dump(),
                self.http, self.settings.data_dir / "cache", self.repo, self.limiter,
            )

        # 调度（push 回调在 bots 装配后设置，见 _build_push_callbacks）
        self.deps = JobDeps(
            self.settings, self.registry, self.repo, self.sources,
            self.ai_client, self.settings.memory_dir, self.alphaxiv,
            push=console_push, digest_push=console_digest_push,
        )
        self.scheduler = build_scheduler(self.deps)

        # 飞书 bot 运行时（凭证齐全才启动）
        self.bots: dict = {}
        for agent in self.registry.agents.values():
            try:
                self.settings.lark_credentials(agent.name)
            except ValueError as e:
                logger.info(f"[{agent.name}] 跳过飞书接入: {e}")
                continue
            self.bots[agent.name] = BotRuntime(
                agent, self.settings, self.repo, self.registry, self.executor,
                self.providers, self.tools, self.ai_client,
                self.settings.memory_dir, self.mailbox,
            )
        if self.bots:
            push, digest_push = self._build_lark_push_callbacks()
            self.deps.push = push
            self.deps.digest_push = digest_push
            self.subagents.on_complete = self._notify_subagent_done
            self._register_send_file_tool()
        logger.info(
            f"App 就绪: bots={[b.name for b in self.registry.bots()]} "
            f"飞书={sorted(self.bots)} sources={sorted(self.sources)}"
        )
        return self

    def _push_target(self, bot_name: str) -> str | None:
        targets = self.settings.push.targets
        if targets:
            return targets[0]
        return self.repo.kv_get(f"bound_user_{bot_name}")

    def _notify_subagent_done(self, task) -> None:
        """子代理完成 → 结果推送到用户手机（经 master bot）。"""
        from app.core.agent_loop import _has_leak
        from app.utils.text import strip_markdown

        rt = self.bots.get("master")
        user_id = self._push_target("master")
        if not rt or not rt.lark or not user_id:
            return
        if task.status == "completed":
            result_text = task.result or ""
            if _has_leak(result_text):
                result_text = "（子代理结果格式异常，已丢弃——请重新提问或换个说法）"
            text = f"子代理 [{task.name}] 完成:\n{result_text[:1200]}"
        else:
            text = f"子代理 [{task.name}] 失败: {task.error or '(未知错误)'}"
        rt.lark.send_text(strip_markdown(text), "p2p", user_id=user_id)

    def _register_send_file_tool(self) -> None:
        """master 专属工具：把电脑上的文件（pdf/ppt 等）发送到用户手机。"""
        from app.core.tools import Tool

        def send_file(args: dict) -> str:
            rt = self.bots.get("master")
            user_id = self._push_target("master")
            if not rt or not rt.lark or not user_id:
                return "错误: 飞书通道未就绪（缺凭证或用户未绑定）"
            raw = args["path"]
            from pathlib import Path as P
            home = P.home()
            candidates = [P(raw), ROOT_DIR / raw]                     # 绝对路径 + 相对项目根
            candidates += [home / "Desktop" / raw, home / "Downloads" / raw,
                           home / "Documents" / raw]                  # 常用目录兜底
            path = next((c for c in candidates if c.exists()), None)
            if path is None:
                return f"错误: 文件不存在（已尝试: 绝对路径、项目根、桌面/下载/文档）—— {raw}"
            suffix = path.suffix.lower()
            file_type = "pdf" if suffix == ".pdf" else "stream"
            try:
                file_key = rt.lark.upload_file(path, file_type)
                msg_id = rt.lark.send_file(file_key, "p2p", user_id=user_id)
                return f"已发送 {path}（message_id={msg_id}）"
            except Exception as e:
                return f"发送失败: {e}"

        self.tools.register(Tool(
            name="send_file",
            description="把电脑上的文件（pdf/ppt/doc 等）发送到用户手机飞书。path 为相对项目根的文件路径",
            input_schema={"properties": {
                "path": {"type": "string", "description": "文件路径（相对项目根，如 data/scratch/报告.pdf）"},
            }, "required": ["path"]},
            call=send_file,
            is_read_only=True,
        ))

    def _build_lark_push_callbacks(self):
        from app.bots.cards import digest_card, news_lines, paper_card

        def push(bot_name: str, item: dict, bot_item: dict) -> str | None:
            rt = self.bots.get(bot_name) or self.bots.get("master")  # 无独立应用的 bot 由 master 中转
            user_id = self._push_target(bot_name) or self._push_target("master")
            if not rt or not rt.lark or not user_id:
                return None
            if item["kind"] == "paper":
                return rt.lark.send_card(paper_card(item, bot_item), "p2p", user_id=user_id)
            return rt.lark.send_post(
                "新闻", news_lines([{**item, "digest_zh": bot_item.get("digest_zh")}]),
                "p2p", user_id=user_id,
            )

        def digest_push(bot_name: str, entries: list[dict]) -> str | None:
            rt = self.bots.get(bot_name) or self.bots.get("master")
            user_id = self._push_target(bot_name) or self._push_target("master")
            if not rt or not rt.lark or not user_id:
                return None
            return rt.lark.send_card(digest_card(bot_name, entries), "p2p", user_id=user_id)

        return push, digest_push

    def sync_once(self) -> None:
        if not self.ai_client:
            logger.warning("DEEPSEEK_API_KEY 未配置：只抓取入库，跳过 LLM 处理")
        summaries = run_fetch_pipeline(
            self.sources, self.registry, self.repo, self.ai_client,
            self.settings, self.settings.memory_dir, self.alphaxiv, console_push,
        )
        for s in summaries:
            print(s.line())

    def start(self) -> None:
        for rt in self.bots.values():
            rt.start()
        self.scheduler.start()
        logger.info("调度器已启动（sync 每 30 分钟 / s2 每日 2 次 / digest 每日 09:00）")

    def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        for rt in self.bots.values():
            rt.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.subagent_executor.shutdown(wait=False, cancel_futures=True)
        self.http.close()
