"""入口验签：GitHub／Jira 都是 HMAC-SHA256 签名头（WebSub 格式 "sha256=<hex>"），
分别是 X-Hub-Signature-256 和 X-Hub-Signature；/send 用 Bearer token。
所有比较用 hmac.compare_digest，常量时间，不给时序侧信道。
"""
from __future__ import annotations

import hashlib
import hmac


def verify_hmac_sha256(secret: str, body: bytes, signature_header: str | None) -> bool:
    """形如 "sha256=<hex>" 的签名头，GitHub（X-Hub-Signature-256）和 Jira Cloud
    原生 webhook 密钥（X-Hub-Signature，2024-02 起支持）用的是同一套格式。"""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


def verify_bearer_token(expected: str, auth_header: str | None) -> bool:
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    provided = auth_header[len("Bearer "):]
    return hmac.compare_digest(expected, provided)
