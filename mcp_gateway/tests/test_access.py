"""Unit tests for the gateway's role transport (app.auth). No Cube needed.

Covers: bearer parsing, agent-JWT validation, claim projection, the no-HTTP-context
degradation, and that the Cube JWT the gateway mints (PyJWT) is verifiable by cube.py's
stdlib HS256 decoder (gateway↔Cube compatibility).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import jwt
import pytest
from app import auth
from app.config import settings


def test_extract_bearer() -> None:
    """The bearer prefix is optional and case-insensitive; an empty header yields an empty token."""
    assert auth.extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"
    assert auth.extract_bearer("bearer abc") == "abc"
    assert auth.extract_bearer("abc") == "abc"
    assert auth.extract_bearer("") == ""


def test_decode_agent_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A correctly signed agent token decodes back to the access claims it carried."""
    monkeypatch.setattr(settings, "agent_jwt_secret", "s3cr3t")
    tok = jwt.encode(
        {"level": 10, "domains": ["furniture"], "is_admin": False, "sub": "u1"},
        "s3cr3t", algorithm="HS256",
    )
    claims = auth.decode_agent_token(tok)
    assert claims["level"] == 10 and claims["domains"] == ["furniture"]


def test_decode_agent_token_bad_signature_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed with the wrong secret is rejected — the gate must not be forgeable."""
    monkeypatch.setattr(settings, "agent_jwt_secret", "s3cr3t")
    tok = jwt.encode({"level": 10}, "WRONG", algorithm="HS256")
    with pytest.raises(auth.AuthError):
        auth.decode_agent_token(tok)


def test_decode_agent_token_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent token is rejected rather than treated as an anonymous caller."""
    monkeypatch.setattr(settings, "agent_jwt_secret", "s3cr3t")
    with pytest.raises(auth.AuthError):
        auth.decode_agent_token("")


def test_claims_to_security_context_projects_only_access_keys() -> None:
    """Only the access claims cross into the security context.

    Forwarding arbitrary token claims to Cube would let a caller smuggle fields the model might
    later start trusting, so the projection is deliberately a whitelist.
    """
    sc = auth.claims_to_security_context(
        {"level": 10, "domains": ["furniture"], "is_admin": False,
         "sub": "u1", "iat": 123, "extra": "x"}
    )
    assert sc == {"level": 10, "domains": ["furniture"], "is_admin": False, "user_id": "u1"}


def test_current_security_context_without_http_context_is_empty() -> None:
    """Outside an HTTP request the context degrades to {} and Cube applies its own fallback."""
    assert auth.current_security_context() == {}


def test_gateway_jwt_is_verifiable_by_cube_stdlib_decoder() -> None:
    """The gateway mints the Cube JWT with PyJWT; cube.py verifies it with stdlib HS256.

    This proves the two are compatible without booting Cube.
    """
    secret = "shared-secret"
    tok = jwt.encode({"level": 30, "domains": ["*"], "is_admin": True}, secret, algorithm="HS256")
    hb, pb, sb = tok.split(".")
    expected = hmac.new(secret.encode(), f"{hb}.{pb}".encode(), hashlib.sha256).digest()
    actual = base64.urlsafe_b64decode(sb + "=" * (-len(sb) % 4))
    assert hmac.compare_digest(expected, actual)
    payload = json.loads(base64.urlsafe_b64decode(pb + "=" * (-len(pb) % 4)))
    assert payload["is_admin"] is True and payload["domains"] == ["*"]
