"""双源配置加载：config.yaml（非敏感）+ .env（凭证），pydantic 校验。

铁律：key 只出现在 .env；config.yaml 不写任何凭证。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(value: Any) -> Any:
    """递归替换字符串中的 ${VAR} 占位符。"""
    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


class ArxivConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 60
    fetch_count: int = 50
    min_interval_s: float = 3.0


class SemanticScholarConfig(BaseModel):
    enabled: bool = True
    min_interval_s: float = 3.5
    interval_hours: int = 12


class AlphaxivConfig(BaseModel):
    enabled: bool = True
    min_interval_s: float = 2.0
    retry_404_after_days: int = 7
    ua: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )


class HackerNewsConfig(BaseModel):
    enabled: bool = True
    min_score: int = 150
    top_n: int = 30


class RedditConfig(BaseModel):
    enabled: bool = True
    min_interval_s: float = 2.0
    max_consecutive_failures: int = 5


class RssFeedConfig(BaseModel):
    name: str
    url: str


class RssConfig(BaseModel):
    enabled: bool = True
    feeds: list[RssFeedConfig] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    arxiv: ArxivConfig = Field(default_factory=ArxivConfig)
    semantic_scholar: SemanticScholarConfig = Field(default_factory=SemanticScholarConfig)
    alphaxiv: AlphaxivConfig = Field(default_factory=AlphaxivConfig)
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    rss: RssConfig = Field(default_factory=RssConfig)


class AiConfig(BaseModel):
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    batch_size: int = 6
    min_relevance: float = 6.0
    instant_threshold: float = 8.0
    instant_push: bool = True
    max_daily_calls: int = 40
    interests: str = ""


class PushConfig(BaseModel):
    digest_time: str = "09:00"
    targets: list[str] = Field(default_factory=list)


class ScriptConfig(BaseModel):
    path: str
    confirm: bool = False


class SecurityConfig(BaseModel):
    allowed_users: list[str] = Field(default_factory=list)
    script_dir: str = "./scripts"
    scripts: dict[str, ScriptConfig] = Field(default_factory=dict)
    shell_allow_prefixes: list[str] = Field(default_factory=list)
    danger_patterns: list[str] = Field(default_factory=list)


class SchedulerConfig(BaseModel):
    misfire_grace_time: int = 3600


class AppConfig(BaseModel):
    name: str = "evoscout-aiagent"
    timezone: str = "Asia/Shanghai"
    data_dir: str = "./data"
    max_concurrent_chats: int = 3     # 主控同时处理的自然语言任务数上限


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    models: dict[str, Any] = Field(default_factory=dict)
    ai: AiConfig = Field(default_factory=AiConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    @field_validator("models")
    @classmethod
    def _validate_models(cls, v: dict) -> dict:
        if "default" not in v:
            raise ValueError("models.default 必须配置")
        return v

    @property
    def data_dir(self) -> Path:
        p = Path(self.app.data_dir)
        if not p.is_absolute():
            p = ROOT_DIR / p
        return p.resolve()

    @property
    def agents_dir(self) -> Path:
        return ROOT_DIR / "agents"

    @property
    def memory_dir(self) -> Path:
        return ROOT_DIR / "memory"

    def lark_credentials(self, bot_name: str) -> tuple[str, str]:
        """按 bot 名解析飞书凭证：LARK_APP_ID_<NAME> / LARK_APP_SECRET_<NAME>。"""
        prefix = bot_name.upper().replace("-", "_")
        app_id = os.environ.get(f"LARK_APP_ID_{prefix}", "")
        secret = os.environ.get(f"LARK_APP_SECRET_{prefix}", "")
        if not app_id or not secret:
            raise ValueError(
                f"缺少 {bot_name} 的飞书凭证：请在 .env 配置 "
                f"LARK_APP_ID_{prefix} / LARK_APP_SECRET_{prefix}"
            )
        return app_id, secret


def _load_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _substitute_env(raw)


def _load_local_env() -> None:
    """Load local secrets from config/.env, with root .env kept as a legacy fallback."""
    for path in (ROOT_DIR / "config" / ".env", ROOT_DIR / ".env"):
        if path.exists():
            load_dotenv(path)
            return
    load_dotenv(ROOT_DIR / "config" / ".env")


def _default_config_path() -> Path:
    """Prefer config/config.yaml while keeping legacy root config.yaml compatible."""
    for path in (
        ROOT_DIR / "config" / "config.yaml",
        ROOT_DIR / "config.yaml",
        ROOT_DIR / "config" / "config.example.yaml",
    ):
        if path.exists():
            return path
    return ROOT_DIR / "config" / "config.yaml"


@lru_cache
def load_settings(config_path: Path | None = None) -> Settings:
    """加载 config/.env + config/config.yaml，构建 Settings。"""
    _load_local_env()
    cfg = _load_config_file(config_path or _default_config_path())
    settings = Settings.model_validate(cfg)
    return settings


@lru_cache
def load_settings_for_test(config_path: Path, env: dict | None = None) -> Settings:
    """测试专用：不读真实 .env，直接注入环境变量。"""
    for k, v in (env or {}).items():
        os.environ[k] = v
    cfg = _load_config_file(config_path)
    return Settings.model_validate(cfg)
