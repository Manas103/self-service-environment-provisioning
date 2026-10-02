"""
A tiny self-signed JWT issuer standing in for an OIDC provider.

This machine cannot obtain a real GitHub Actions OIDC token outside of an
actual GitHub Actions run (the token is only available via
ACTIONS_ID_TOKEN_REQUEST_URL, which only exists inside a workflow run with
`permissions: id-token: write`). So the locally-measured numbers in this
repo use this mock issuer, which signs the same claim shape GitHub's real
token carries (iss, sub, aud, exp). The real path is authored separately in
.github/workflows/provision.yml and only exercises against GitHub's real
issuer when that workflow actually runs on GitHub's infrastructure.
"""
from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://mock-issuer.local"


def generate_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def issue_token(private_pem: bytes, sub: str, aud: str = "vault", ttl_seconds: int = 300) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256")
