from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, body: bytes, timestamp: str, request_id: str) -> str:
    message = timestamp.encode() + b"\n" + request_id.encode() + b"\n" + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_signature(
    *,
    secret: str | None,
    body: bytes,
    timestamp: str | None,
    request_id: str | None,
    signature: str | None,
) -> None:
    if not secret:
        return
    if not timestamp or not request_id or not signature:
        raise ValueError("missing signature headers")
    expected = compute_signature(secret, body, timestamp, request_id)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid signature")
