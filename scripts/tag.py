#!/usr/bin/env python3
"""请 claude-relay 替你在 GitHub 上打 annotated tag（云端会话推 tag 是 403）。只用标准库。

用法：
    python3 tag.py <owner/repo> <40 位提交号> <tag 名> [说明]

环境变量：
    CLAUDE_RELAY_TOKEN  和 send.py 共用的令牌；没设时读 ~/.config/claude-relay/token
    CLAUDE_RELAY_TAG_URL  可选，默认 https://relay.babelstacks.com/tag
    CLAUDE_RELAY_FROM     可选，写进审计日志的发起人名字
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://relay.babelstacks.com/tag"


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print("用法：tag.py <owner/repo> <40 位提交号> <tag 名> [说明]", file=sys.stderr)
        return 2
    repo, sha, tag = argv[1:4]
    message = argv[4] if len(argv) == 5 else tag

    token = os.environ.get("CLAUDE_RELAY_TOKEN")
    if not token:
        token_file = os.path.expanduser("~/.config/claude-relay/token")
        if os.path.exists(token_file):
            token = open(token_file, encoding="utf-8").read().strip()
    if not token:
        print("缺环境变量 CLAUDE_RELAY_TOKEN（也没有 ~/.config/claude-relay/token）", file=sys.stderr)
        return 2

    body = json.dumps(
        {"repo": repo, "sha": sha, "tag": tag, "message": message, "from": os.environ.get("CLAUDE_RELAY_FROM", "")}
    ).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("CLAUDE_RELAY_TAG_URL", DEFAULT_URL),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(resp.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"请求失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
