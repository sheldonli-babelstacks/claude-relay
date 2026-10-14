"""把 Jira webhook payload 转成 router.Event。"""
from __future__ import annotations

import hashlib
import json

from ..router import Event


def parse(payload: dict, site_base_url: str | None = None) -> Event | None:
    webhook_event = payload.get("webhookEvent", "")
    issue = payload.get("issue") or {}
    issue_key = issue.get("key")
    issue_url = f"{site_base_url}/browse/{issue_key}" if site_base_url and issue_key else None

    # Jira 没有像 GitHub X-GitHub-Delivery 那样现成的事件 id，退而求其次用请求体哈希去重。
    dedup_key = "jira:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]

    if webhook_event == "comment_created":
        comment = payload.get("comment") or {}
        author = comment.get("author") or {}
        return Event(
            source="jira",
            event="comment_created",
            author_id=author.get("accountId"),
            author_display_name=author.get("displayName"),
            body_text=comment.get("body", "") if isinstance(comment.get("body"), str) else "",
            fields={"issue_key": issue_key, "issue_url": issue_url},
            dedup_key=dedup_key,
        )

    if webhook_event in ("jira:issue_updated", "issue_updated"):
        user = payload.get("user") or {}
        return Event(
            source="jira",
            event="issue_updated",
            author_id=user.get("accountId"),
            author_display_name=user.get("displayName"),
            body_text="",
            fields={"issue_key": issue_key, "issue_url": issue_url},
            dedup_key=dedup_key,
        )

    return None
