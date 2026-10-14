"""入口验签：GitHub HMAC、Jira 共享密钥（走自定义头，不走 URL 查询串）、/send 的 Bearer token。
所有比较用 hmac.compare_digest，常量时间，不给时序侧信道。
"""
from __future__ import annotations

import hashlib
import hmac


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """GitHub 的 X-Hub-Signature-256 头，形如 "sha256=<hex>"。"""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


def verify_shared_secret(expected: str, provided: str | None) -> bool:
    """Jira webhook：密钥放自定义头（例如 X-Relay-Secret），不放 URL 查询串——
    查询串会被反向代理／负载均衡器原样写进访问日志，等于把密钥写进了日志文件。
    """
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


def verify_bearer_token(expected: str, auth_header: str | None) -> bool:
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    provided = auth_header[len("Bearer "):]
    return hmac.compare_digest(expected, provided)
