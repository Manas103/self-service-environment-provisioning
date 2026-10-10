"""Integration tests against the live kind-platform / kind-simulated-aws clusters.

These need the two kind clusters, Crossplane 1.20 (claims, --enable-usages) and
provider-kubernetes (--poll=20s --enable-server-side-apply) already installed, as
documented in the README. They are not spun up by the test itself (that is a
multi-minute, multi-GB kind-cluster bring-up, out of scope for a pytest fixture),
so each test skips cleanly if `kubectl --context kind-platform` cannot reach a
cluster, the same pattern this repo already uses for Kafka/PostgreSQL.
"""
import json
import subprocess

import pytest


def _kubectl_ok(context):
    try:
        r = subprocess.run(
            ["kubectl", "--context", context, "get", "ns"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


CLUSTERS_UP = _kubectl_ok("kind-platform") and _kubectl_ok("kind-simulated-aws")
pytestmark = pytest.mark.skipif(
    not CLUSTERS_UP,
    reason="needs the kind-platform and kind-simulated-aws clusters from the README",
)


def kubectl(*args, context="kind-platform"):
    r = subprocess.run(
        ["kubectl", "--context", context, *args],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_claim_is_synced_and_ready():
    out = json.loads(kubectl("get", "appruntime", "checkout-service", "-n", "default", "-o", "json"))
    conds = {c["type"]: c["status"] for c in out["status"]["conditions"]}
    assert conds["Synced"] == "True"
    assert conds["Ready"] == "True"


def test_claim_renders_exactly_31_managed_resources():
    out = json.loads(kubectl("get", "objects.kubernetes.crossplane.io", "-o", "json"))
    names = {
        i["metadata"]["name"]
        for i in out["items"]
        if i["metadata"].get("labels", {}).get("crossplane.io/claim-name") == "checkout-service"
    }
    assert len(names) == 31


def test_three_platform_apis_are_installed_as_xrds():
    out = kubectl("get", "compositeresourcedefinitions.apiextensions.crossplane.io", "-o",
                  "jsonpath={.items[*].metadata.name}")
    for xrd in (
        "xobjectstores.platform.schonfeld.manas.dev",
        "xmanagedpostgreses.platform.schonfeld.manas.dev",
        "xappruntimes.platform.schonfeld.manas.dev",
    ):
        assert xrd in out


def test_scoped_iam_reader_and_writer_roles_differ():
    reader = json.loads(kubectl(
        "get", "role", "bucket-reader", "-n", "checkout-service-store",
        "-o", "json", context="kind-simulated-aws",
    ))["rules"]
    writer = json.loads(kubectl(
        "get", "role", "bucket-writer", "-n", "checkout-service-store",
        "-o", "json", context="kind-simulated-aws",
    ))["rules"]
    reader_verbs = set(reader[0]["verbs"])
    writer_verbs = set(writer[0]["verbs"])
    assert reader_verbs == {"get", "list", "watch"}
    assert "update" in writer_verbs and "patch" in writer_verbs
    assert "watch" not in writer_verbs
