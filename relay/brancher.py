"""POST /branch/delete：替会话删远端分支，走 GitHub API（云端会话 git push --delete 是 403）。

10-20 用户在 M8 Project 点头要做成服务。校验（任一条不过，这个分支就不删）：
- 调用方 Bearer 令牌对（和 /tag、send-to-roster 同一把）；
- 仓库 owner 在白名单里（复用 tag_service 的 allowed_owners 和 GitHub 令牌）；
- 不是默认分支、不是 main、不是 rc/*；
- 分支不受保护；
- 没有以它为 head 的打开 PR；
- 调用方给了 expect_sha 时，分支当前提交必须等于它（防止删掉别人刚推的新提交）。
每个分支（删了的和被拒的）记一行到 <state_dir>/branches.jsonl，带删除前的提交号，误删可按它恢复。
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

from .tagger import _REPO_RE, TagError, TagServiceConfig, _gh

MAX_BRANCHES_PER_REQUEST = 200


def _q(branch: str) -> str:
    return urllib.parse.quote(branch, safe="/")


def _check_repo(cfg: TagServiceConfig, repo: str) -> str:
    if not _REPO_RE.match(repo):
        raise TagError(400, "repo 要写成 owner/name")
    if repo.split("/")[0].lower() not in {o.lower() for o in cfg.allowed_owners}:
        raise TagError(403, f"仓库 {repo} 不在白名单里（只收 {', '.join(cfg.allowed_owners)}）")
    code, info = _gh(cfg, "GET", f"/repos/{repo}")
    if code != 200:
        raise TagError(404 if code == 404 else 502, f"读不到仓库 {repo}（GitHub {code}）")
    return info["default_branch"]


def delete_one(cfg: TagServiceConfig, repo: str, default_branch: str, branch: str, expect_sha: str | None) -> dict:
    owner = repo.split("/")[0]
    if not branch or branch.startswith("refs/") or ".." in branch:
        raise TagError(400, f"分支名 {branch!r} 不合法")
    if branch in (default_branch, "main") or branch.startswith("rc/"):
        raise TagError(403, f"{branch} 是默认分支、main 或 rc/*，不删")

    code, info = _gh(cfg, "GET", f"/repos/{repo}/branches/{_q(branch)}")
    if code == 404:
        raise TagError(404, f"分支 {branch} 不存在")
    if code != 200:
        raise TagError(502, f"读分支 {branch} 失败（GitHub {code}）")
    if info.get("protected"):
        raise TagError(403, f"分支 {branch} 受保护，不删")
    sha = info["commit"]["sha"]
    if expect_sha and sha != expect_sha.lower():
        raise TagError(409, f"分支 {branch} 当前在 {sha[:7]}，不是给的 {expect_sha[:7]}，可能有人刚推过，不删")

    code, prs = _gh(cfg, "GET", f"/repos/{repo}/pulls?state=open&head={urllib.parse.quote(owner + ':' + branch)}")
    if code != 200:
        raise TagError(502, f"查 {branch} 的打开 PR 失败（GitHub {code}）")
    if prs:
        raise TagError(409, f"分支 {branch} 有打开的 PR #{prs[0]['number']}，不删")

    code, body = _gh(cfg, "DELETE", f"/repos/{repo}/git/refs/heads/{_q(branch)}")
    if code != 204:
        raise TagError(502, f"删 {branch} 失败（GitHub {code}：{(body or {}).get('message', '')}）")
    return {"branch": branch, "ok": True, "sha": sha}


def delete_branches(cfg: TagServiceConfig, payload: dict, audit) -> dict:
    """payload: {repo, branches: [名字 或 {name, expect_sha}], from}。逐个处理，一个失败不影响其他。"""
    repo = str(payload.get("repo", "")).strip()
    items = payload.get("branches")
    if items is None and payload.get("branch"):
        items = [{"name": payload["branch"], "expect_sha": payload.get("expect_sha")}]
    if not isinstance(items, list) or not items:
        raise TagError(400, "branches 要给一个非空列表")
    if len(items) > MAX_BRANCHES_PER_REQUEST:
        raise TagError(400, f"一次最多 {MAX_BRANCHES_PER_REQUEST} 个分支")
    default_branch = _check_repo(cfg, repo)

    results = []
    for it in items:
        name = str(it.get("name") if isinstance(it, dict) else it).strip()
        expect = (it.get("expect_sha") if isinstance(it, dict) else None) or None
        try:
            r = delete_one(cfg, repo, default_branch, name, expect)
        except TagError as e:
            r = {"branch": name, "ok": False, "status": e.status, "error": e.message}
        audit({"repo": repo, **r})
        results.append(r)
    deleted = sum(1 for r in results if r["ok"])
    return {"ok": deleted == len(results), "repo": repo, "deleted": deleted, "failed": len(results) - deleted, "results": results}


def write_audit(state_dir: str, entry: dict) -> None:
    p = Path(state_dir) / "branches.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), **entry}, ensure_ascii=False) + "\n")
