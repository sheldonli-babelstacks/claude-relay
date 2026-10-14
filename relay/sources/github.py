"""把 GitHub webhook payload 转成 router.Event。只提取结构化字段，评论/提交正文原样保留在
body_text 里，由 router 决定要不要截断带出去——parser 自己不做信任判断。
"""
from __future__ import annotations

from ..router import Event


def parse(event_type: str, delivery_id: str, payload: dict) -> Event | None:
    repo = (payload.get("repository") or {}).get("full_name")

    if event_type == "push":
        sender = payload.get("sender") or {}
        return Event(
            source="github",
            event="push",
            author_id=str(sender.get("id")) if sender.get("id") else sender.get("login"),
            author_display_name=sender.get("login"),
            body_text=(payload.get("head_commit") or {}).get("message", ""),
            fields={
                "repo": repo,
                "ref": payload.get("ref"),
                "url": (payload.get("head_commit") or {}).get("url"),
            },
            dedup_key=f"github:{delivery_id}",
        )

    if event_type == "issue_comment":
        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        user = comment.get("user") or {}
        return Event(
            source="github",
            event="issue_comment",
            author_id=str(user.get("id")) if user.get("id") else user.get("login"),
            author_display_name=user.get("login"),
            body_text=comment.get("body", ""),
            fields={
                "repo": repo,
                "issue_key": f"#{issue.get('number')}" if issue.get("number") else None,
                "issue_url": issue.get("html_url"),
                "url": comment.get("html_url"),
            },
            dedup_key=f"github:{delivery_id}",
        )

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        user = pr.get("user") or {}
        return Event(
            source="github",
            event="pull_request",
            author_id=str(user.get("id")) if user.get("id") else user.get("login"),
            author_display_name=user.get("login"),
            body_text=pr.get("body") or "",
            fields={
                "repo": repo,
                "pr_number": pr.get("number"),
                "issue_url": pr.get("html_url"),
                "url": pr.get("html_url"),
            },
            dedup_key=f"github:{delivery_id}",
        )

    return None  # 没配置的事件类型，忽略
