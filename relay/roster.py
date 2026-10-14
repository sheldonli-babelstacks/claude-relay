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
