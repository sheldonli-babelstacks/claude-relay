#!/usr/bin/env python3
"""给花名册里的人发一条消息，唤醒对方的会话——不用知道对方的 session_id，只要知道名字。
走 claude-relay 的 /send/send-to-roster 接口（与 ai-team 仓 process/send.py 是同一份）。

用法：
    python3 process/send.py <姓名> "<消息内容>"

需要两个环境变量（在你的会话环境里设置，不要写进任何仓库/卡/日志）：
    CLAUDE_RELAY_TOKEN  这条路由的令牌，找 Ops 要；美国服务器上的会话不用设，自动读 ~/.config/claude-relay/token
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
        # 美国服务器上的 Remote Control 会话没有 Project 环境变量，读本机私有令牌文件（Ops 维护，600 权限，不进仓）
        token_file = os.path.expanduser("~/.config/claude-relay/token")
        if os.path.exists(token_file):
            token = open(token_file, encoding="utf-8").read().strip()
    if not token:
        print("缺环境变量 CLAUDE_RELAY_TOKEN（也没有 ~/.config/claude-relay/token）", file=sys.stderr)
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
        # relay 那边会先投协调者、再投收件箱，失败还会重试，超时给够，才能看到真正的错误而不是本地超时
        with urllib.request.urlopen(req, timeout=400) as resp:
            out = resp.read().decode("utf-8")
            if resp.status == 202:
                # 协调者和收件箱都投不进：relay 已排队，对方下次开工 register.py 时会收到，不用重发
                info = json.loads(out or "{}")
                print(f"对方暂时投不进，已排队（共 {info.get('pending')} 条等它）：{info.get('note', '')}")
                return 0
            print(out)
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"请求失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
