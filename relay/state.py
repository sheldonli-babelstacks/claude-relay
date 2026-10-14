"""运行状态：去重、限流、全局熔断。全部用一个 sqlite 文件，单进程够用，重启不丢状态。"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class State:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS dedup (key TEXT PRIMARY KEY, ts REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS wakes (route TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS circuit_breaker (tripped_at REAL, reason TEXT)"
        )
        self._conn.commit()

    # ---- 去重：同一个事件（GitHub delivery id / Jira webhook id / 请求体哈希）只处理一次 ----
    def seen_before(self, key: str, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            self._conn.execute("DELETE FROM dedup WHERE ts < ?", (now - window_seconds,))
            row = self._conn.execute("SELECT 1 FROM dedup WHERE key = ?", (key,)).fetchone()
            if row:
                return True
            self._conn.execute("INSERT OR REPLACE INTO dedup (key, ts) VALUES (?, ?)", (key, now))
            self._conn.commit()
            return False

    # ---- 单路由限流：滑动窗口 ----
    def route_wake_count(self, route: str, window_seconds: int) -> int:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM wakes WHERE route = ? AND ts >= ?",
                (route, now - window_seconds),
            ).fetchone()
            return row[0] if row else 0

    def record_wake(self, route: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO wakes (route, ts) VALUES (?, ?)", (route, time.time()))
            self._conn.commit()

    # ---- 全局熔断：所有路由合计唤醒数，防回环兜底 ----
    def global_wake_count(self, window_seconds: int) -> int:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM wakes WHERE ts >= ?", (now - window_seconds,)
            ).fetchone()
            return row[0] if row else 0

    def circuit_breaker_tripped(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT reason FROM circuit_breaker ORDER BY tripped_at DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None

    def trip_circuit_breaker(self, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO circuit_breaker (tripped_at, reason) VALUES (?, ?)",
                (time.time(), reason),
            )
            self._conn.commit()

    def reset_circuit_breaker(self) -> None:
        """人工恢复：熔断后必须有人确认过回环已经解决才能调这个。"""
        with self._lock:
            self._conn.execute("DELETE FROM circuit_breaker")
            self._conn.commit()
