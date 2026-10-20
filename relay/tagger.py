"""POST /tag：替会话打 annotated tag，全走 GitHub API，不在本地 checkout。

云端会话推 tag 是 403（10-18 实测），以前都是发消息请 Ops 手动打，来回慢（10-19 用户要求改成服务）。
校验（任何一条不过就拒，不打）：
- 调用方 Bearer 令牌对；
- 仓库 owner 在白名单里；
- tag 名符合 tag_pattern（缺省只收 vX.Y.Z 和 vX.Y.Z-rc.N）；
- sha 是 40 位完整提交号，而且在默认分支上（compare 默认分支...sha 为 behind／identical）；
  freeze/* 例外，只要提交在仓库里（冻结代码常在专门分支上，10-20 用户定）；
- 同名 tag 不存在——只新建，永远不改、不删、不覆盖。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("claude_relay.tagger")

API = "https://api.github.com"
FREEZE_PREFIX = "freeze/"
DEFAULT_TAG_PATTERN = r"^v\d+\.\d+\.\d+(-rc\.\d+)?$"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass
class TagServiceConfig:
    token: str  # 调用方用的 Bearer 令牌
    github_token: str  # relay 调 GitHub API 用的令牌（只要 Contents: write）
    allowed_owners: list[str] = field(default_factory=list)
    tag_pattern: str = DEFAULT_TAG_PATTERN


class TagError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _gh(cfg: TagServiceConfig, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {cfg.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "claude-relay-tagger",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"message": raw.decode("utf-8", "replace")[:300]}


def validate_request(cfg: TagServiceConfig, payload: dict) -> tuple[str, str, str, str]:
    repo = str(payload.get("repo", "")).strip()
    sha = str(payload.get("sha", "")).strip().lower()
    tag = str(payload.get("tag", "")).strip()
    message = str(payload.get("message") or tag).strip()[:2000]
    if not _REPO_RE.match(repo):
        raise TagError(400, "repo 要写成 owner/name")
    if repo.split("/")[0].lower() not in {o.lower() for o in cfg.allowed_owners}:
        raise TagError(403, f"仓库 {repo} 不在白名单里（只收 {', '.join(cfg.allowed_owners)}）")
    if not re.match(cfg.tag_pattern, tag):
        raise TagError(400, f"tag 名 {tag!r} 不符合 {cfg.tag_pattern}")
    if not _SHA_RE.match(sha):
        raise TagError(400, "sha 要写 40 位完整提交号")
    return repo, sha, tag, message


def create_tag(cfg: TagServiceConfig, payload: dict) -> dict:
    repo, sha, tag, message = validate_request(cfg, payload)

    code, info = _gh(cfg, "GET", f"/repos/{repo}")
    if code != 200:
        raise TagError(404 if code == 404 else 502, f"读不到仓库 {repo}（GitHub {code}）")
    default_branch = info["default_branch"]

    if tag.startswith(FREEZE_PREFIX):
        # freeze/*：冻结代码常在专门分支上（如 recut/vol01-on-0.4.5），只要求提交在这个仓库里（10-20 用户定）
        code, _ = _gh(cfg, "GET", f"/repos/{repo}/commits/{sha}")
        if code in (404, 422):
            raise TagError(404, f"仓库里没有提交 {sha}")
        if code != 200:
            raise TagError(502, f"查提交 {sha[:7]} 失败（GitHub {code}）")
    else:
        code, cmp = _gh(cfg, "GET", f"/repos/{repo}/compare/{default_branch}...{sha}")
        if code == 404:
            raise TagError(404, f"仓库里没有提交 {sha}，或它和 {default_branch} 没有共同祖先")
        if code != 200:
            raise TagError(502, f"比较 {default_branch}...{sha} 失败（GitHub {code}）")
        # base=默认分支、head=sha：sha 在默认分支上时，head 比 base 落后或相同
        if cmp.get("status") not in ("behind", "identical"):
            raise TagError(409, f"提交 {sha[:7]} 不在 {default_branch} 上（compare 状态 {cmp.get('status')}）")

    code, _ = _gh(cfg, "GET", f"/repos/{repo}/git/ref/tags/{tag}")
    if code == 200:
        raise TagError(409, f"tag {tag} 已存在，不覆盖")
    if code != 404:
        raise TagError(502, f"查 tag {tag} 失败（GitHub {code}）")

    code, tag_obj = _gh(
        cfg, "POST", f"/repos/{repo}/git/tags",
        {"tag": tag, "message": message, "object": sha, "type": "commit"},
    )
    if code != 201:
        raise TagError(502, f"建 tag 对象失败（GitHub {code}：{tag_obj.get('message', '')}）")

    code, ref = _gh(cfg, "POST", f"/repos/{repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": tag_obj["sha"]})
    if code == 422:
        raise TagError(409, f"tag {tag} 刚被别人建了，不覆盖")
    if code != 201:
        raise TagError(502, f"建 refs/tags/{tag} 失败（GitHub {code}：{ref.get('message', '')}）")

    return {"ok": True, "repo": repo, "tag": tag, "tag_sha": tag_obj["sha"], "commit": sha, "branch": default_branch}


def write_audit(state_dir: str, entry: dict) -> None:
    """每次请求（成功或被拒）都记一行，供事后查谁打了什么。不记令牌。"""
    p = Path(state_dir) / "tags.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), **entry}, ensure_ascii=False) + "\n")
