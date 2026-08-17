"""Agent 定义：frontmatter markdown（Claude Code ~/.claude/agents 风格）。

格式：
---
name: aipapers
description: AI 论文追踪机器人
model: deepseek-chat        # inherit = 跟随默认模型
role: bot                   # master | bot | subagent
channel: lark               # lark | console
tools: [shell, read_file]
disallowed_tools: [spawn_subagent]
subscriptions: {arxiv: [cs.AI, cs.CL], keywords: [LLM, agent, RAG]}
memory: memory/aipapers     # 记忆目录（相对项目根），默认 memory/<name>
max_turns: 20
background: false
---
正文 = system prompt（可被 master 修改）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

VALID_ROLES = ("master", "bot", "subagent")
VALID_CHANNELS = ("lark", "console")


@dataclass
class AgentDefinition:
    name: str
    description: str
    prompt: str                      # 正文 system prompt
    model: str = "inherit"           # inherit = 跟随 models.default
    role: str = "bot"
    channel: str = "lark"
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    subscriptions: dict = field(default_factory=dict)
    memory: str = ""                 # 记忆目录名，默认 name
    max_turns: int = 20
    background: bool = False
    source_file: Path = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"{self.name}: 非法 role {self.role!r}（可选 {VALID_ROLES}）")
        if self.channel not in VALID_CHANNELS:
            raise ValueError(f"{self.name}: 非法 channel {self.channel!r}（可选 {VALID_CHANNELS}）")
        if not self.memory:
            self.memory = self.name

    @property
    def is_master(self) -> bool:
        return self.role == "master"


def parse_agent_md(text: str, source: Path | None = None) -> AgentDefinition:
    """解析 agent 定义 markdown；缺字段抛 ValueError（启动即失败，尽早暴露配置错误）。"""
    m = FRONTMATTER_PATTERN.match(text)
    if not m:
        raise ValueError(f"{source or '<string>'}: 缺少 frontmatter（--- 包裹）")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{source or '<string>'}: frontmatter YAML 解析失败: {e}") from e
    if not isinstance(fm, dict):
        raise ValueError(f"{source or '<string>'}: frontmatter 必须是键值对")

    name = fm.get("name")
    if not name:
        raise ValueError(f"{source or '<string>'}: frontmatter 缺少 name")
    return AgentDefinition(
        name=str(name),
        description=str(fm.get("description", "")),
        prompt=text[m.end():].strip(),
        model=str(fm.get("model", "inherit")),
        role=str(fm.get("role", "bot")),
        channel=str(fm.get("channel", "lark")),
        tools=list(fm.get("tools") or []),
        disallowed_tools=list(fm.get("disallowed_tools") or []),
        subscriptions=dict(fm.get("subscriptions") or {}),
        memory=str(fm.get("memory", "")),
        max_turns=int(fm.get("max_turns", 20)),
        background=bool(fm.get("background", False)),
        source_file=source,
    )


def load_agent(path: Path) -> AgentDefinition:
    return parse_agent_md(path.read_text(encoding="utf-8"), source=path)


def dump_agent_md(agent: AgentDefinition) -> str:
    """序列化回 markdown（master 修改 bot 后写盘用）。"""
    fm: dict = {
        "name": agent.name,
        "description": agent.description,
        "model": agent.model,
        "role": agent.role,
        "channel": agent.channel,
    }
    if agent.tools:
        fm["tools"] = agent.tools
    if agent.disallowed_tools:
        fm["disallowed_tools"] = agent.disallowed_tools
    if agent.subscriptions:
        fm["subscriptions"] = agent.subscriptions
    if agent.memory and agent.memory != agent.name:
        fm["memory"] = agent.memory
    fm["max_turns"] = agent.max_turns
    if agent.background:
        fm["background"] = True
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + agent.prompt.rstrip() + "\n"
