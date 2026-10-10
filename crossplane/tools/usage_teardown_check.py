#!/usr/bin/env python3
"""Demonstrates 4 teardown races and clears each by declaring a Crossplane Usage.

For each (of, by) pair of managed resources behind the checkout-service claim:
  1. Delete `of` with no Usage declared. It deletes immediately even though `by`
     still needs it: that is the race.
  2. Recreate `of`.
  3. Declare a Usage(of=of, by=by).
  4. Delete `of` again. This time Crossplane's `nousages` admission webhook denies
     the delete request outright, synchronously, because `by` still exists: the
     race is cleared.
  5. Delete `by`, which lets `of`'s deletion (and the Usage) finish.
  6. Recreate `of` and `by` so the claim is left exactly as it was.
"""
import json
import subprocess
import time

CLAIM_NAME = "checkout-service"

PAIRS = [
    ("bucket-encryption-key", "bucket-config",
     "bucket-config's encryption-key reference"),
    ("bucket-access-role", "bucket-reader-binding",
     "RoleBinding's ServiceAccount subject"),
    ("bucket-reader-role", "bucket-reader-binding",
     "RoleBinding's Role reference"),
    ("postgres-connection", "postgres-statefulset",
     "StatefulSet's ConfigMap envFrom"),
]


def kubectl(*args, context=None, input_data=None, check=True, timeout=None):
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += list(args)
    out = subprocess.run(cmd, capture_output=True, text=True, input=input_data, timeout=timeout)
    if check and out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {out.stderr}")
    return out


def find_by_resource_name(resource_name):
    raw = kubectl("get", "objects.kubernetes.crossplane.io", "-o", "json", context="kind-platform").stdout
    items = json.loads(raw)["items"]
    for item in items:
        labels = item["metadata"].get("labels", {})
        if labels.get("crossplane.io/claim-name") != CLAIM_NAME:
            continue
        ann = item["metadata"].get("annotations", {})
        if ann.get("crossplane.io/composition-resource-name") == resource_name:
            return item
    raise LookupError(f"no Object found for composition-resource-name={resource_name}")


def clean_manifest(obj):
    """Strips an Object CR down to something re-appliable."""
    obj = json.loads(json.dumps(obj))
    meta = obj["metadata"]
    for k in ("resourceVersion", "uid", "creationTimestamp", "generation",
              "managedFields", "finalizers", "ownerReferences", "deletionTimestamp"):
        meta.pop(k, None)
    obj.pop("status", None)
    return obj


def apply(obj):
    kubectl("apply", "-f", "-", context="kind-platform", input_data=json.dumps(obj))


def delete_and_time(name, timeout_s=15):
    t0 = time.monotonic()
    try:
        kubectl("delete", "object.kubernetes.crossplane.io", name,
                "--wait=true", f"--timeout={timeout_s}s", context="kind-platform",
                timeout=timeout_s + 10)
        return True, time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - t0
    except RuntimeError as e:
        msg = str(e).lower()
        if "timed out" in msg or "in-use by" in msg or "denied the request" in msg:
            return False, time.monotonic() - t0
        raise


def wait_gone(name, timeout_s=30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = kubectl("get", "object.kubernetes.crossplane.io", name,
                     context="kind-platform", check=False)
        if r.returncode != 0:
            return True
        time.sleep(1)
    return False


def usage_name(of_name, by_name):
    return f"usage-{of_name}-{by_name}"


def run_pair(of_rn, by_rn, reason):
    print(f"\n=== pair: of={of_rn!r} by={by_rn!r} ({reason}) ===")
    of_obj = find_by_resource_name(of_rn)
    by_obj = find_by_resource_name(by_rn)
    of_name = of_obj["metadata"]["name"]
    by_name = by_obj["metadata"]["name"]
    of_manifest = clean_manifest(of_obj)

    print(f"of Object CR: {of_name}, by Object CR: {by_name}")

    print("-- step 1: delete `of` with no Usage declared --")
    deleted, elapsed = delete_and_time(of_name)
    print(f"   deleted={deleted} in {elapsed:.2f}s (race: deletion proceeded while `by` still needs it)")
    assert deleted, "expected the undeclared-usage delete to succeed quickly"

    print("-- step 2: recreate `of` --")
    apply(of_manifest)
    for _ in range(20):
        r = kubectl("get", "object.kubernetes.crossplane.io", of_name, context="kind-platform", check=False)
        if r.returncode == 0:
            break
        time.sleep(1)

    print("-- step 3: declare Usage(of, by) --")
    usage = {
        "apiVersion": "apiextensions.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {"name": usage_name(of_name, by_name)},
        "spec": {
            "of": {
                "apiVersion": "kubernetes.crossplane.io/v1alpha2",
                "kind": "Object",
                "resourceRef": {"name": of_name},
            },
            "by": {
                "apiVersion": "kubernetes.crossplane.io/v1alpha2",
                "kind": "Object",
                "resourceRef": {"name": by_name},
            },
            "reason": reason,
        },
    }
    apply(usage)
    time.sleep(2)

    print("-- step 4: delete `of` again, with the Usage in place --")
    deleted, elapsed = delete_and_time(of_name)
    print(f"   deleted={deleted} after waiting {elapsed:.2f}s (race cleared: deletion is blocked)")
    cleared = not deleted

    print("-- step 5: delete `by`, then retry deleting `of` (admission blocked it outright "
          "the first time, so nothing was ever marked Terminating to wait on) --")
    kubectl("delete", "object.kubernetes.crossplane.io", by_name, "--wait=true", "--timeout=30s",
            context="kind-platform")
    deleted2, elapsed2 = delete_and_time(of_name, timeout_s=30)
    usage_gone = wait_gone(usage_name(of_name, by_name), timeout_s=15)
    print(f"   `of` deleted once `by` was gone: {deleted2} in {elapsed2:.2f}s; Usage gone: {usage_gone}")

    print("-- step 6: recreate `of` and `by` to restore the claim --")
    kubectl("delete", "usage.apiextensions.crossplane.io", usage_name(of_name, by_name),
            context="kind-platform", check=False)
    by_manifest = clean_manifest(by_obj)
    apply(of_manifest)
    apply(by_manifest)
    for name in (of_name, by_name):
        for _ in range(30):
            r = kubectl("get", "object.kubernetes.crossplane.io", name, "-o", "json",
                        context="kind-platform", check=False)
            if r.returncode == 0:
                conds = {c["type"]: c["status"] for c in json.loads(r.stdout).get("status", {}).get("conditions", [])}
                if conds.get("Synced") == "True":
                    break
            time.sleep(1)

    return cleared


def main():
    results = []
    for of_rn, by_rn, reason in PAIRS:
        cleared = run_pair(of_rn, by_rn, reason)
        results.append((of_rn, by_rn, cleared))

    print("\n=== summary ===")
    n_cleared = sum(1 for _, _, c in results if c)
    for of_rn, by_rn, cleared in results:
        print(f"{of_rn} used by {by_rn}: race cleared = {cleared}")
    print(f"\n{n_cleared} of {len(results)} teardown races cleared by declaring resource usages")


if __name__ == "__main__":
    main()
