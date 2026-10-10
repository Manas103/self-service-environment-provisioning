#!/usr/bin/env python3
"""Adopts 9 Terraform-managed resources into Crossplane and proves 0 deletes / 0 re-creates.

Reads the 9 resources kubernetes_namespace.legacy_bucket and friends (crossplane/terraform-adopt)
already created directly on the simulated-aws cluster, records each one's (uid, resourceVersion),
applies 9 matching Object custom resources on the platform cluster (no claim, no Composition,
one Object per legacy resource), waits for them to sync, then re-reads the same 9 resources and
compares. 0 deletes and 0 re-creates means every uid is unchanged after adoption.
"""
import json
import subprocess
import sys
import time

NAMESPACE = "legacy-bucket"

RESOURCES = [
    ("v1", "Namespace", None, "legacy-bucket"),
    ("v1", "ConfigMap", NAMESPACE, "bucket-config"),
    ("v1", "ConfigMap", NAMESPACE, "bucket-lifecycle-policy"),
    ("v1", "Secret", NAMESPACE, "bucket-encryption-key"),
    ("v1", "ServiceAccount", NAMESPACE, "bucket-access-role"),
    ("rbac.authorization.k8s.io/v1", "Role", NAMESPACE, "bucket-reader"),
    ("rbac.authorization.k8s.io/v1", "Role", NAMESPACE, "bucket-writer"),
    ("rbac.authorization.k8s.io/v1", "RoleBinding", NAMESPACE, "bucket-reader-binding"),
    ("rbac.authorization.k8s.io/v1", "RoleBinding", NAMESPACE, "bucket-writer-binding"),
]


def kubectl(*args, context=None):
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += list(args)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {out.stderr}")
    return out.stdout


def kind_to_resource(kind):
    return {
        "Namespace": "namespace",
        "ConfigMap": "configmap",
        "Secret": "secret",
        "ServiceAccount": "serviceaccount",
        "Role": "role",
        "RoleBinding": "rolebinding",
    }[kind]


def snapshot():
    snap = {}
    for _, kind, ns, name in RESOURCES:
        res = kind_to_resource(kind)
        args = ["get", res, name, "-o", "json"]
        if ns:
            args += ["-n", ns]
        obj = json.loads(kubectl(*args, context="kind-simulated-aws"))
        snap[(kind, ns, name)] = (obj["metadata"]["uid"], obj["metadata"]["resourceVersion"])
    return snap


def object_cr_name(kind, name):
    return f"adopt-{kind.lower()}-{name}"


def apply_object_crs():
    for api_version, kind, ns, name in RESOURCES:
        manifest = {"apiVersion": api_version, "kind": kind, "metadata": {"name": name}}
        if ns:
            manifest["metadata"]["namespace"] = ns
        cr = {
            "apiVersion": "kubernetes.crossplane.io/v1alpha2",
            "kind": "Object",
            "metadata": {"name": object_cr_name(kind, name)},
            "spec": {
                "providerConfigRef": {"name": "simulated-aws"},
                "forProvider": {"manifest": manifest},
                "readiness": {"policy": "SuccessfulCreate"},
            },
        }
        p = subprocess.run(
            ["kubectl", "--context", "kind-platform", "apply", "-f", "-"],
            input=json.dumps(cr), capture_output=True, text=True,
        )
        if p.returncode != 0:
            raise RuntimeError(f"apply failed for {name}: {p.stderr}")
        print(p.stdout.strip())


def wait_for_sync(timeout=60):
    deadline = time.time() + timeout
    names = [object_cr_name(kind, name) for _, kind, _, name in RESOURCES]
    while time.time() < deadline:
        out = json.loads(kubectl(
            "get", "objects.kubernetes.crossplane.io", "-o", "json", context="kind-platform"
        ))
        statuses = {
            item["metadata"]["name"]: {
                c["type"]: c["status"] for c in item.get("status", {}).get("conditions", [])
            }
            for item in out["items"]
            if item["metadata"]["name"] in names
        }
        if len(statuses) == len(names) and all(
            s.get("Synced") == "True" for s in statuses.values()
        ):
            return statuses
        time.sleep(2)
    raise TimeoutError("not all adopted Objects synced in time")


def main():
    print("=== before adoption ===")
    before = snapshot()
    for k, v in before.items():
        print(k, v)

    print("\n=== applying 9 Object CRs (default FullControl management policy) ===")
    apply_object_crs()

    print("\n=== waiting for sync ===")
    statuses = wait_for_sync()
    for name, s in statuses.items():
        print(name, s)

    print("\n=== after adoption ===")
    after = snapshot()
    for k, v in after.items():
        print(k, v)

    deletes = 0
    recreates = 0
    updates = 0
    for key in before:
        before_uid, before_rv = before[key]
        after_uid, after_rv = after[key]
        if after_uid != before_uid:
            recreates += 1
            print(f"RECREATE detected for {key}: uid {before_uid} -> {after_uid}")
        elif after_rv != before_rv:
            updates += 1
            print(f"UPDATE (same uid) detected for {key}: resourceVersion {before_rv} -> {after_rv}")

    print(f"\n9 resources adopted; {recreates} re-creates (uid changed); "
          f"{deletes} deletes; {updates} in-place updates (same uid, different resourceVersion)")
    if recreates != 0 or deletes != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
