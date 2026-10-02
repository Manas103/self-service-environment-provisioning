"""
Starts a real local HashiCorp Vault dev-mode server, configures its JWT auth
method to trust the mock OIDC issuer's public key, and issues a short-lived
Vault token plus a KV secret in response to a valid signed token, denying an
invalid one. Nothing issued here is ever written to a file or committed; the
only thing on disk is the ephemeral dev-mode Vault process's own in-memory
storage, which is gone the moment the process is stopped.

This is a real Vault server (binary at VAULT_BIN, default "vault" on PATH),
not a mock: the HTTP calls below are genuine Vault API calls.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import requests

from mock_oidc_issuer import generate_keypair, issue_token

VAULT_BIN = os.environ.get("VAULT_BIN", "vault")
VAULT_ADDR = "http://127.0.0.1:18200"
ROOT_TOKEN = "root-token-for-this-ephemeral-dev-server-only"
BOUND_SUBJECT = "repo:Manas103/self-service-environment-provisioning:ref:refs/heads/main"


def start_vault_dev():
    env = os.environ.copy()
    env["VAULT_ADDR"] = VAULT_ADDR
    proc = subprocess.Popen(
        [
            VAULT_BIN, "server", "-dev",
            f"-dev-root-token-id={ROOT_TOKEN}",
            "-dev-listen-address=127.0.0.1:18200",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    for _ in range(50):
        try:
            r = requests.get(f"{VAULT_ADDR}/v1/sys/health", timeout=1)
            if r.status_code in (200, 429, 472, 473):
                return proc
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("vault dev server did not become healthy in time")


def vault_api(method, path, token, **kwargs):
    r = requests.request(
        method, f"{VAULT_ADDR}/v1/{path}",
        headers={"X-Vault-Token": token}, timeout=5, **kwargs,
    )
    return r


def configure_vault(public_pem: bytes):
    vault_api("POST", "sys/auth/jwt", ROOT_TOKEN, json={"type": "jwt"})
    vault_api(
        "POST", "auth/jwt/config", ROOT_TOKEN,
        json={
            "jwt_validation_pubkeys": [public_pem.decode()],
            "bound_issuer": "https://mock-issuer.local",
        },
    )
    policy_hcl = """
path "secret/data/env-provisioner/*" {
  capabilities = ["read"]
}
"""
    vault_api("PUT", "sys/policies/acl/env-provisioner", ROOT_TOKEN, json={"policy": policy_hcl})
    vault_api(
        "POST", "auth/jwt/role/github-ci", ROOT_TOKEN,
        json={
            "role_type": "jwt",
            "bound_audiences": "vault",
            "user_claim": "sub",
            "bound_claims": {"sub": BOUND_SUBJECT},
            "policies": ["env-provisioner"],
            "ttl": "5m",
        },
    )
    vault_api(
        "POST", "secret/data/env-provisioner/dev", ROOT_TOKEN,
        json={"data": {"db_password": "generated-at-apply-time-not-committed"}},
    )


def login_with_jwt(token: str):
    r = vault_api("POST", "auth/jwt/login", "", json={"role": "github-ci", "jwt": token})
    return r


def run_flow():
    private_pem, public_pem = generate_keypair()
    proc = start_vault_dev()
    try:
        configure_vault(public_pem)

        valid_token = issue_token(private_pem, sub=BOUND_SUBJECT)
        good = login_with_jwt(valid_token)
        good_ok = good.status_code == 200
        client_token = good.json()["auth"]["client_token"] if good_ok else None
        secret_read_ok = False
        if client_token:
            secret_resp = vault_api("GET", "secret/data/env-provisioner/dev", client_token)
            secret_read_ok = secret_resp.status_code == 200

        invalid_token = issue_token(private_pem, sub="repo:someone-else/unrelated-repo:ref:refs/heads/main")
        bad = login_with_jwt(invalid_token)
        bad_rejected = bad.status_code != 200

        return {
            "valid_jwt_login_ok": good_ok,
            "valid_jwt_secret_read_ok": secret_read_ok,
            "invalid_jwt_rejected": bad_rejected,
            "client_token_written_to_disk": False,
        }
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    import json

    result = run_flow()
    print(json.dumps(result, indent=2))
    sys.exit(0 if all([result["valid_jwt_login_ok"], result["valid_jwt_secret_read_ok"], result["invalid_jwt_rejected"]]) else 1)
