#!/usr/bin/env python3
"""会话开工时向 claude-relay 登记自己当前的会话 ID，并取走排队等你的消息。只用标准库。

用法：
    python3 register.py <名字>                 # 登记为该名字的协调者（默认）
    python3 register.py <名字> --inbox         # 登记为该名字的「收件箱」thread（协调者投不进时的备用地址）
    python3 register.py <名字> --session session_…   # 不在云端会话里（如 Remote Control）时手动给 ID
    python3 register.py --list                 # 看当前生效的通讯录（排错用）

会话 ID 默认读环境变量 CLAUDE_CODE_REMOTE_SESSION_ID（云端会话自带，cse_ 前缀自动换成 session_）。
令牌同 send.py：CLAUDE_RELAY_TOKEN，或 ~/.config/claude-relay/token。
CLAUDE_RELAY_BASE 可选，默认 https://relay.babelstacks.com

每次开工跑一次就行，ID 没变也可以跑（幂等）；有排队消息会直接打印出来，照着处理。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://relay.babelstacks.com"


def _token() -> str | None:
    tok = os.environ.get("CLAUDE_RELAY_TOKEN")
    if tok:
        return tok
    path = os.path.expanduser("~/.config/claude-relay/token")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    return None


def _call(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    base = os.environ.get("CLAUDE_RELAY_BASE", DEFAULT_BASE).rstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")}


def main(argv: list[str]) -> int:
    args = argv[1:]
    token = _token()
    if not token:
        print("缺环境变量 CLAUDE_RELAY_TOKEN（也没有 ~/.config/claude-relay/token）", file=sys.stderr)
        return 2

    if args == ["--list"]:
        code, out = _call("GET", "/directory", token)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if code == 200 else 1

    if not args or args[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    name = args[0]
    role = "inbox" if "--inbox" in args else "coordinator"
    sid = None
    if "--session" in args:
        i = args.index("--session")
        sid = args[i + 1] if i + 1 < len(args) else None
    sid = sid or os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    if not sid:
        print("拿不到会话 ID：不在云端会话里就用 --session session_… 手动给", file=sys.stderr)
        return 2

    code, out = _call("POST", "/register", token, {"name": name, "role": role, "session_id": sid})
    if code != 200:
        print(f"登记失败 HTTP {code}: {out.get('error')}", file=sys.stderr)
        return 1
    what = "协调者" if role == "coordinator" else "收件箱"
    print(f"已登记 {name} 的{what}：{out['session_id']}（{'ID 有变，已更新' if out.get('changed') else 'ID 没变'}）")
    pending = out.get("pending") or []
    if pending:
        print(f"\n有 {len(pending)} 条排队等你的消息（已从队列取走，请逐条处理）：")
        for i, p in enumerate(pending, 1):
            print(f"\n--- {i}（{p['queued_at']} 排队）---\n{p['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
