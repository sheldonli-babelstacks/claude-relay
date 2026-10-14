"""加载 routes.yaml 配置。密钥永远来自环境变量，配置文件里只写变量名。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_env_refs(value: Any) -> Any:
    """把形如 "${FOO_SECRET}" 的字符串换成对应环境变量的值；不是这个形状就原样返回。
    配置文件里永远不直接写密钥，只写引用——这样配置本身可以安全地进仓／进日志。
    """
    if isinstance(value, str):
        m = _ENV_REF.match(value)
        if m:
            env_name = m.group(1)
            resolved = os.environ.get(env_name)
            if resolved is None:
                raise ConfigError(f"环境变量 {env_name} 未设置（配置里引用了 ${{{env_name}}}）")
            return resolved
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(v) for v in value]
    return value


class ConfigError(RuntimeError):
    pass


@dataclass
class Account:
    alias: str
    config_dir: str

    def __post_init__(self) -> None:
        if not self.config_dir:
            raise ConfigError(f"账号 {self.alias} 没有配置 config_dir")
        # relay 不允许任何账号复用机器的默认 ~/.claude —— 必须是独立目录，
        # 10-14 本机真的踩过这个坑（见 overview 仓消息中转站设计.md）。
        default_dir = str(Path.home() / ".claude")
        if str(Path(self.config_dir).expanduser()) == default_dir:
            raise ConfigError(
                f"账号 {self.alias} 的 config_dir 指向了机器默认 ~/.claude，relay 不允许这样配——"
                "每个账号必须有自己独立、relay 专用的目录"
            )


@dataclass
class Route:
    name: str
    source: str  # github | jira | send
    event: str | None
    match: dict
    action: dict
    template: dict = field(default_factory=dict)
    rate: dict = field(default_factory=dict)
    merge: dict = field(default_factory=dict)
    continue_matching: bool = False


@dataclass
class AntiLoopConfig:
    ignore_authors: list[str] = field(default_factory=list)
    ignore_body_patterns: list[str] = field(default_factory=list)
    circuit_breaker_window_seconds: int = 300
    circuit_breaker_max_wakes: int = 20


@dataclass
class Config:
    accounts: dict[str, Account]
    routes: list[Route]
    anti_loop: AntiLoopConfig
    dedup_window_seconds: int
    dedup_db_path: str
    state_dir: str
    github_webhook_secret: str | None
    jira_webhook_secret: str | None
    send_tokens: dict[str, str]  # route_name -> bearer token


def load_config(path: str) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw = _resolve_env_refs(raw)

    accounts = {
        alias: Account(alias=alias, config_dir=cfg["config_dir"])
        for alias, cfg in (raw.get("accounts") or {}).items()
    }

    al = raw.get("anti_loop") or {}
    cb = al.get("circuit_breaker") or {}
    anti_loop = AntiLoopConfig(
        ignore_authors=al.get("ignore_authors") or [],
        ignore_body_patterns=al.get("ignore_body_patterns") or [],
        circuit_breaker_window_seconds=cb.get("window_seconds", 300),
        circuit_breaker_max_wakes=cb.get("max_wakes", 20),
    )

    dedup = raw.get("dedup") or {}
    state_dir = raw.get("state_dir", "/var/lib/claude-relay")

    routes = []
    for r in raw.get("routes") or []:
        if r.get("action", {}).get("type") == "wake":
            acct = r["action"].get("account")
            if acct not in accounts:
                raise ConfigError(f"路由 {r['name']} 引用了未定义的账号 {acct!r}")
        routes.append(
            Route(
                name=r["name"],
                source=r["source"],
                event=r.get("event"),
                match=r.get("match") or {},
                action=r["action"],
                template=r.get("template") or {},
                rate=r.get("rate") or {},
                merge=r.get("merge") or {},
                continue_matching=bool(r.get("continue", False)),
            )
        )

    send_tokens = {}
    for r in routes:
        if r.source == "send":
            tok = r.action.get("token")
            if tok:
                send_tokens[r.name] = tok

    return Config(
        accounts=accounts,
        routes=routes,
        anti_loop=anti_loop,
        dedup_window_seconds=dedup.get("window_seconds", 86400),
        dedup_db_path=dedup.get(
            "db_path", str(Path(state_dir) / "dedup.sqlite3")
        ),
        state_dir=state_dir,
        github_webhook_secret=raw.get("github_webhook_secret"),
        jira_webhook_secret=raw.get("jira_webhook_secret"),
        send_tokens=send_tokens,
    )
