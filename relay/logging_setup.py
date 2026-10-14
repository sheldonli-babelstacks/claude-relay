"""JSON 行日志。只记事件的元信息（路由、来源、结果、耗时、哈希），不记请求体原文、
不记密钥/令牌、不记完整唤醒消息正文——只记长度。时间戳用 UTC ISO8601（这是通用开源工具，
不绑定任何特定时区的约定；本仓运营方的内部时间惯例写在 overview 仓，不进这里）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    root = logging.getLogger("claude_relay")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False


def body_fingerprint(body: bytes) -> dict:
    """请求体不落日志，只给长度和哈希——出问题时能核对「是不是同一个请求」，但看不到内容。"""
    return {"body_len": len(body), "body_sha256": hashlib.sha256(body).hexdigest()[:16]}
