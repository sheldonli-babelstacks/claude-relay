#!/usr/bin/env python3
"""给花名册里的人发一条消息，唤醒对方的会话——不用知道对方的 session_id，只要知道名字。

用法：
    python3 send.py <姓名> "<消息内容>"

需要两个环境变量（在你的会话环境里设置，不要写进任何仓库/卡/日志）：
    CLAUDE_RELAY_TOKEN  这条路由的令牌，找管 claude-relay 的人要
    CLAUDE_RELAY_URL    可选，默认指向 https://relay.babelstacks.com/send/send-to-roster

只用标准库，任何能跑 python3 的环境（包括云端会话）直接拿去用，不用装依赖。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://relay.babelstacks.com/send/send-to-roster"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法：send.py <姓名> <消息>", file=sys.stderr)
        return 2

    _, name, message = argv

    token = os.environ.get("CLAUDE_RELAY_TOKEN")
    if not token:
        print("缺环境变量 CLAUDE_RELAY_TOKEN", file=sys.stderr)
        return 2

    url = os.environ.get("CLAUDE_RELAY_URL", DEFAULT_URL)
    body = json.dumps({"to": name, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"请求失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
