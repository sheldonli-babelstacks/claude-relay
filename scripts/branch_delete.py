#!/usr/bin/env python3
"""请 claude-relay 替你删远端分支（云端会话 git push --delete 是 403）。只用标准库。

用法：
    python3 branch_delete.py <owner/repo> <分支> [分支 ...]
    python3 branch_delete.py <owner/repo> -f 名单.txt      # 一行一个分支；可写「分支<TAB>提交号」，提交号对不上就不删

relay 会拒绝：白名单外的仓库、默认分支/main/rc/*、受保护分支、有打开 PR 的分支。删了不能撤销，
但每个分支删除前的提交号都记在服务器 /var/lib/claude-relay/branches.jsonl，可以按它恢复。

环境变量：同 tag.py（CLAUDE_RELAY_TOKEN 或 ~/.config/claude-relay/token；CLAUDE_RELAY_FROM 写进日志）。
CLAUDE_RELAY_BRANCH_URL 可选，默认 https://relay.babelstacks.com/branch/delete
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://relay.babelstacks.com/branch/delete"
BATCH = 100


def _token() -> str | None:
    token = os.environ.get("CLAUDE_RELAY_TOKEN")
    if not token:
        token_file = os.path.expanduser("~/.config/claude-relay/token")
        if os.path.exists(token_file):
            token = open(token_file, encoding="utf-8").read().strip()
    return token


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("用法：branch_delete.py <owner/repo> <分支> [分支 ...] | -f 名单.txt", file=sys.stderr)
        return 2
    repo = argv[1]
    items: list[dict] = []
    if argv[2] == "-f":
        for line in open(argv[3], encoding="utf-8"):
            parts = line.strip().split("\t")
            if parts and parts[0] and not parts[0].startswith("#"):
                items.append({"name": parts[0], "expect_sha": parts[1] if len(parts) > 1 and len(parts[1]) == 40 else None})
    else:
        items = [{"name": b} for b in argv[2:]]

    token = _token()
    if not token:
        print("缺环境变量 CLAUDE_RELAY_TOKEN（也没有 ~/.config/claude-relay/token）", file=sys.stderr)
        return 2

    deleted = failed = 0
    for i in range(0, len(items), BATCH):
        body = json.dumps({"repo": repo, "branches": items[i : i + BATCH], "from": os.environ.get("CLAUDE_RELAY_FROM", "")})
        req = urllib.request.Request(
            os.environ.get("CLAUDE_RELAY_BRANCH_URL", DEFAULT_URL),
            data=body.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
            return 1
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"请求失败：{e}", file=sys.stderr)
            return 1
        deleted += result["deleted"]
        failed += result["failed"]
        for r in result["results"]:
            if not r["ok"]:
                print(f"没删 {r['branch']}：{r['error']}")
    print(f"{repo}：删了 {deleted} 个，没删 {failed} 个")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
