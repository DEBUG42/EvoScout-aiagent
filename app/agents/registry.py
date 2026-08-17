"""Agent 注册表：加载 agents/ 目录全部定义，校验 master 存在。"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.agents.defs import AgentDefinition, load_agent


class AgentRegistry:
    def __init__(self, agents_dir: Path):
        self.agents_dir = agents_dir
        self.agents: dict[str, AgentDefinition] = {}
        self.reload()

    def reload(self) -> None:
        """重新扫描目录（bot 定义热更新入口）。"""
        loaded: dict[str, AgentDefinition] = {}
        if self.agents_dir.exists():
            for f in sorted(self.agents_dir.glob("*.md")):
                try:
                    agent = load_agent(f)
                    loaded[agent.name] = agent
                    logger.debug(f"加载 agent 定义: {agent.name} ({f.name})")
                except ValueError as e:
                    logger.error(f"跳过非法 agent 定义 {f}: {e}")
        if not loaded:
            raise ValueError(f"{self.agents_dir} 下没有合法 agent 定义")
        self.agents = loaded

    def get(self, name: str) -> AgentDefinition:
        if name not in self.agents:
            raise KeyError(f"agent {name!r} 不存在（现有: {sorted(self.agents)}）")
        return self.agents[name]

    def master(self) -> AgentDefinition:
        masters = [a for a in self.agents.values() if a.is_master]
        if len(masters) != 1:
            raise ValueError(f"需要恰好 1 个 role=master 的 agent，现有 {len(masters)} 个")
        return masters[0]

    def bots(self) -> list[AgentDefinition]:
        """除 master 外的全部 bot 定义。"""
        return [a for a in self.agents.values() if not a.is_master]

    def list_names(self) -> list[str]:
        return sorted(self.agents)
