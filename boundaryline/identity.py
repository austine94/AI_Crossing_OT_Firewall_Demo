"""Minimal locally signed identity tokens for the isolated lab.

This deliberately avoids accepting identity from request bodies. It is not a general-purpose
JWT implementation and must not be reused outside the synthetic BoundaryLine environment.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from pydantic import Field

from boundaryline.contracts import Scenario, StrictModel

TOKEN_VERSION = "bl1"


class TokenClaims(StrictModel):
    sub: str = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    aud: str = Field(min_length=1)
    run_id: UUID
    scenario: Scenario = Scenario.B
    iat: int
    exp: int


class InvalidToken(ValueError):
    """Raised when a lab identity token cannot be trusted."""


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_token(
    *,
    subject: str,
    roles: tuple[str, ...],
    audience: str,
    run_id: UUID,
    secret: str,
    scenario: Scenario = Scenario.B,
    ttl_seconds: int = 300,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    claims = TokenClaims(
        sub=subject,
        roles=roles,
        aud=audience,
        run_id=run_id,
        scenario=scenario,
        iat=issued_at,
        exp=issued_at + ttl_seconds,
    )
    payload = _encode(claims.model_dump_json().encode("utf-8"))
    signing_input = f"{TOKEN_VERSION}.{payload}".encode()
    signature = _encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{TOKEN_VERSION}.{payload}.{signature}"


def verify_token(
    token: str,
    *,
    secret: str,
    audience: str,
    now: int | None = None,
) -> TokenClaims:
    try:
        version, payload, supplied_signature = token.split(".")
        if version != TOKEN_VERSION:
            raise InvalidToken("unsupported token version")
        signing_input = f"{version}.{payload}".encode()
        expected = _encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied_signature):
            raise InvalidToken("invalid token signature")
        raw: dict[str, Any] = json.loads(_decode(payload))
        claims = TokenClaims.model_validate(raw)
    except InvalidToken:
        raise
    except Exception as exc:
        raise InvalidToken("malformed token") from exc

    current_time = int(time.time()) if now is None else now
    if claims.aud != audience:
        raise InvalidToken("invalid token audience")
    if claims.exp <= current_time:
        raise InvalidToken("token expired")
    if claims.iat > current_time + 30:
        raise InvalidToken("token issued in the future")
    return claims
