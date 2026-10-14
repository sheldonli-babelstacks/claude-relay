"""唤醒执行器：对已有 session_id 跑 `claude -p "<msg>" --cloud <session_id>`。

硬规矩（10-14 在美国服务器真的踩过坑，详见 overview 仓消息中转站设计.md）：
- 永远显式传 CLAUDE_CONFIG_DIR，不读进程环境里可能已经有的默认值——
  relay 不信任、不借用宿主机任何「默认登录」，哪怕是账号 A 也要走 relay 自己的目录。
- 只对预先登记好的固定 session_id 唤醒；不动态建新会话
  （`claude --cloud "<描述>"` 建新会话需要交互式 TTY，脚本环境里用不了，实测过）。
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Account

logger = logging.getLogger("claude_relay.executor")

WAKE_TIMEOUT_SECONDS = 60
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 3


@dataclass
class WakeResult:
    ok: bool
    exit_code: int | None
    session_id: str
    account_alias: str
    attempts: int
    error: str | None = None


def wake(account: Account, session_id: str, message: str) -> WakeResult:
    import os

    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = account.config_dir

    last_error = None
    for attempt in range(1, MAX_RETRIES + 2):  # 首次 + 最多 MAX_RETRIES 次重试
        try:
            proc = subprocess.run(
                ["claude", "-p", message, "--cloud", session_id],
                env=env,
                capture_output=True,
                text=True,
                timeout=WAKE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            last_error = f"超时（>{WAKE_TIMEOUT_SECONDS}s）"
            logger.warning(
                "wake timeout account=%s session=%s attempt=%d",
                account.alias,
                session_id,
                attempt,
            )
        else:
            if proc.returncode == 0:
                return WakeResult(
                    ok=True,
                    exit_code=0,
                    session_id=session_id,
                    account_alias=account.alias,
                    attempts=attempt,
                )
            last_error = (proc.stderr or proc.stdout or "").strip()[:500]
            logger.warning(
                "wake failed account=%s session=%s attempt=%d exit=%d",
                account.alias,
                session_id,
                attempt,
                proc.returncode,
            )

        if attempt <= MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return WakeResult(
        ok=False,
        exit_code=None,
        session_id=session_id,
        account_alias=account.alias,
        attempts=MAX_RETRIES + 1,
        error=last_error,
    )


def write_dead_letter(dead_letter_path: str, route_name: str, session_id: str, message: str, error: str) -> None:
    """重试耗尽后落盘，供人工重放。消息体本身是 relay 自己按白名单字段拼出来的（见 router.py），
    不含外部原始密钥，落盘没问题；但依然不记录任何 token／密钥相关字段。
    """
    Path(dead_letter_path).parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "route": route_name,
        "session_id": session_id,
        "message": message,
        "error": error,
    }
    with open(dead_letter_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
