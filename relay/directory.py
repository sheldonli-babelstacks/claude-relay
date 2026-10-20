"""活的通讯录：会话自己登记当前 ID，投不进的消息排队等对方登记时取走。

为什么要有（10-20）：Project 协调者会被平台回收换新，会话 ID 一天能变几次；原来靠新协调者
发「ID上报」给 Ops、Ops 手改 roster.json，环节多、常断。现在：
- 会话开工时自己 POST /register {name, session_id}，立刻生效，不经人手。
- 每个名字可以再登记一个「收件箱」会话（role=inbox，一般是该 Project 里一个长期不 resolve 的 thread），
  主会话投不进时投给它。
- 两个都投不进，消息进 mailbox 排队；对方下次 /register 时随回执一起取走。

静态 roster.json（ai-team 通用/通讯录.md 生成）仍是种子：没登记过的名字照旧用它。
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$")
SESSION_RE = re.compile(r"^(session|cse)_[A-Za-z0-9]{10,60}$")
ROLES = ("coordinator", "inbox")
MAILBOX_MAX_PER_NAME = 200


def normalize_session_id(sid: str) -> str | None:
    sid = (sid or "").strip()
    if not SESSION_RE.match(sid):
        return None
    return "session_" + sid.split("_", 1)[1]


class Directory:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS live_roster ("
            " name TEXT NOT NULL, role TEXT NOT NULL, account TEXT NOT NULL,"
            " session_id TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (name, role))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS mailbox ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
            " message TEXT NOT NULL, ts REAL NOT NULL, error TEXT)"
        )
        self._conn.commit()

    def register(self, name: str, role: str, account: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_roster (name, role, account, session_id, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, role, account, session_id, time.time()),
            )
            self._conn.commit()

    def live(self, name: str) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, account, session_id, updated_at FROM live_roster WHERE name = ?", (name,)
            ).fetchall()
        return {r[0]: {"account": r[1], "session_id": r[2], "updated_at": r[3]} for r in rows}

    def all_live(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, role, account, session_id, updated_at FROM live_roster ORDER BY name"
            ).fetchall()
        out: dict[str, dict] = {}
        for name, role, account, sid, ts in rows:
            out.setdefault(name, {})[role] = {"account": account, "session_id": sid, "updated_at": ts}
        return out

    def targets(self, name: str, static_entry: dict | None) -> list[tuple[str, str, str]]:
        """按投递顺序返回 [(role, account, session_id)]：登记过的协调者 > 静态花名册 > 收件箱，去重。"""
        live = self.live(name)
        out: list[tuple[str, str, str]] = []
        if "coordinator" in live:
            out.append(("coordinator", live["coordinator"]["account"], live["coordinator"]["session_id"]))
        elif static_entry and static_entry.get("session_id"):
            out.append(("coordinator", static_entry.get("account", "a"), static_entry["session_id"]))
        if "inbox" in live:
            out.append(("inbox", live["inbox"]["account"], live["inbox"]["session_id"]))
        seen: set[str] = set()
        uniq = []
        for t in out:
            if t[2] not in seen:
                seen.add(t[2])
                uniq.append(t)
        return uniq

    def enqueue(self, name: str, message: str, error: str) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mailbox (name, message, ts, error) VALUES (?, ?, ?, ?)",
                (name, message, time.time(), error[:500]),
            )
            # 只留最近 MAILBOX_MAX_PER_NAME 条，防止无人取时无限涨
            self._conn.execute(
                "DELETE FROM mailbox WHERE name = ? AND id NOT IN "
                "(SELECT id FROM mailbox WHERE name = ? ORDER BY id DESC LIMIT ?)",
                (name, name, MAILBOX_MAX_PER_NAME),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT COUNT(*) FROM mailbox WHERE name = ?", (name,)).fetchone()
        return row[0]

    def take(self, name: str) -> list[dict]:
        """取走并清空 name 的排队消息（旧的在前）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, message, ts FROM mailbox WHERE name = ? ORDER BY id", (name,)
            ).fetchall()
            if rows:
                self._conn.execute("DELETE FROM mailbox WHERE name = ? AND id <= ?", (name, rows[-1][0]))
                self._conn.commit()
        return [
            {"message": m, "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))}
            for _, m, ts in rows
        ]

    def pending_count(self, name: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM mailbox WHERE name = ?", (name,)).fetchone()
        return row[0]
