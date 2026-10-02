"""
Drives the real local Vault dev-mode server through the OIDC-style JWT login
flow: a valid mock-issuer token gets a short-lived Vault token and can read
the KV secret; a token with the wrong `sub` claim is rejected. Requires the
`vault` binary; skips cleanly if it is not on PATH or VAULT_BIN.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

VAULT_BIN = os.environ.get("VAULT_BIN") or shutil.which("vault")

pytestmark = pytest.mark.skipif(
    not VAULT_BIN or not os.path.exists(VAULT_BIN or ""),
    reason="vault binary not available on this machine (set VAULT_BIN)",
)


def test_valid_jwt_gets_short_lived_token_and_reads_secret():
    import issue_credentials

    result = issue_credentials.run_flow()
    assert result["valid_jwt_login_ok"] is True
    assert result["valid_jwt_secret_read_ok"] is True


def test_invalid_subject_jwt_is_rejected():
    import issue_credentials

    result = issue_credentials.run_flow()
    assert result["invalid_jwt_rejected"] is True


def test_no_vault_token_or_secret_value_is_committed_to_the_repo():
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    offending = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".pytest_cache")]
        for fn in filenames:
            if fn.endswith((".py", ".tf", ".yaml", ".yml", ".md")):
                path = os.path.join(dirpath, fn)
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                normalized = path.replace("\\", "/")
                excluded = ("tools/issue_credentials.py", "tests/test_vault_credential_flow.py")
                if "generated-at-apply-time-not-committed" in content and not any(e in normalized for e in excluded):
                    offending.append(path)
    assert offending == [], f"found a literal secret value outside the demo script: {offending}"
