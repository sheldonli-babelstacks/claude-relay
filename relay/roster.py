"""从 JSON 花名册里按正文里出现的 "@姓名" 动态找目标账号/会话，不用给每个人单独写一条
路由。花名册真源是 ai-team 仓各成员的 intro.md（见 roster.example.json 和
scripts/build_roster.py），relay 这边只负责读、不负责编——改人员去改 ai-team 仓。

花名册结构：{"Owen": {"account": "a", "session_id": "session_..."}, ...}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]{1,30})")

# 我们各会话发评论的首行约定：「角色｜姓名」，可能后面跟冒号加正文，也可能单独一行
# 就是这些（纯回执）。两种都要认出来——见 GUJI-195 10691 的回环漏洞（Jira 所有会话
# 共用一个账号，按 author 过滤无效，只能靠这个首行格式识别「这是我们自己写的」）。
_ROLE_NAME_HEADER_RE = re.compile(r"^[^\n]{1,20}｜([^\n]{1,20}?)(?:[：:]|$)", re.MULTILINE)


def load_roster(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def find_mentions(body_text: str, roster: dict) -> list[str]:
    """正文里 @到的、且花名册里确实存在的名字，按出现顺序去重返回。
    纯子串匹配——正文里提到别人名字（哪怕不是想叫醒对方）也会命中，这是已知限制，
    靠路由自己的限流／熔断兜底，不做更复杂的 NLP 判断。
    """
    seen: list[str] = []
    for m in _MENTION_RE.finditer(body_text or ""):
        name = m.group(1)
        if name in roster and name not in seen:
            seen.append(name)
    return seen


def extract_header_name(body_text: str) -> str | None:
    """评论首行是「角色｜姓名」格式（如 "高级经理｜Morgan：..." 或单独一行 "高级经理｜Morgan"）
    就返回姓名，否则返回 None。用于防自己唤醒自己（GUJI-195 10691）。
    """
    m = _ROLE_NAME_HEADER_RE.match(body_text or "")
    return m.group(1) if m else None
