"""可选的企业身份边界：零额外依赖的 HS256 JWT 校验与 Workspace ACL 主体模型。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet


class AuthenticationError(ValueError):
    pass


def _decode_part(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise AuthenticationError("invalid JWT encoding") from exc


@dataclass(frozen=True)
class Actor:
    subject: str
    groups: FrozenSet[str] = frozenset()
    roles: FrozenSet[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def acl_subjects(self) -> FrozenSet[str]:
        return frozenset({f"user:{self.subject}", *(f"group:{x}" for x in self.groups)})


ANONYMOUS_ACTOR = Actor("anonymous")


def decode_hs256_jwt(token: str, secret: str, issuer: str = "", audience: str = "") -> Dict[str, Any]:
    """校验 HS256 签名及常用时间/发行方声明。生产可在网关侧换成企业 OIDC。"""
    parts = token.split(".")
    if len(parts) != 3 or not secret:
        raise AuthenticationError("invalid JWT or missing secret")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _decode_part(parts[2])):
        raise AuthenticationError("invalid JWT signature")
    try:
        header = json.loads(_decode_part(parts[0]))
        payload = json.loads(_decode_part(parts[1]))
    except Exception as exc:
        raise AuthenticationError("invalid JWT payload") from exc
    if header.get("alg") != "HS256" or not isinstance(payload, dict):
        raise AuthenticationError("only HS256 JWT is accepted")
    now = time.time()
    if "exp" in payload and float(payload["exp"]) <= now:
        raise AuthenticationError("JWT expired")
    if "nbf" in payload and float(payload["nbf"]) > now:
        raise AuthenticationError("JWT is not active")
    if issuer and payload.get("iss") != issuer:
        raise AuthenticationError("invalid JWT issuer")
    claim_aud = payload.get("aud", [])
    audiences = {claim_aud} if isinstance(claim_aud, str) else set(claim_aud)
    if audience and audience not in audiences:
        raise AuthenticationError("invalid JWT audience")
    if not str(payload.get("sub", "")).strip():
        raise AuthenticationError("JWT subject is required")
    return payload


def actor_from_bearer(authorization: str, secret: str, issuer: str = "", audience: str = "") -> Actor:
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Bearer token is required")
    claims = decode_hs256_jwt(token, secret, issuer, audience)
    groups = claims.get("groups", [])
    roles = claims.get("roles", [])
    return Actor(
        subject=str(claims["sub"]),
        groups=frozenset(str(x) for x in groups if str(x).strip()),
        roles=frozenset(str(x) for x in roles if str(x).strip()),
    )
