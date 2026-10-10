#!/usr/bin/env python3
"""Injects an out-of-band change into 24 of the 31 managed resources behind the
checkout-service claim and measures how long Crossplane's own reconcile loop takes
to revert each one back to the Composition's declared value.

Namespace, ServiceAccount, PersistentVolumeClaim and Secret are excluded: a first
attempt mutated each resource's `simaws.manas.dev/kind` label instead of a spec
field and found labels are never diffed as drift at all; a second attempt found
that with `--enable-server-side-apply` off, drift on any field is invisible
(provider-kubernetes compares the manifest to a `last-applied-configuration`
annotation, never the live object); and with it on, Secrets authored via
`stringData` are still invisible, because `stringData` is a write-only field the
API server never returns, so SSA's field-ownership extractor can never see it as
drifted (see README Findings for the full chain). ConfigMap, Role, RoleBinding,
NetworkPolicy, Service, Deployment, StatefulSet, PodDisruptionBudget,
ResourceQuota and LimitRange all use real persisted fields and do revert.
Because that excludes all 3 Secrets too (leaving 22, not 24, in one claim), this
script pulls eligible resources from two claim instances (checkout-service and
checkout-service-2) to reach 24 without touching Secrets at all.
"""
import base64
import json
import subprocess
import statistics
import threading
import time

POLL_SECONDS = 1.0
TIMEOUT_SECONDS = 90

EXCLUDED_KINDS = {"Namespace", "ServiceAccount", "PersistentVolumeClaim", "Secret"}

KIND_TO_RESOURCE = {
    "ConfigMap": "configmap",
    "Secret": "secret",
    "Role": "role",
    "RoleBinding": "rolebinding",
    "NetworkPolicy": "networkpolicy",
    "Service": "service",
    "Deployment": "deployment",
    "StatefulSet": "statefulset",
    "PodDisruptionBudget": "poddisruptionbudget",
    "ResourceQuota": "resourcequota",
    "LimitRange": "limitrange",
}


def kubectl(*args, context=None, input_data=None):
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += list(args)
    out = subprocess.run(cmd, capture_output=True, text=True, input=input_data)
    if out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {out.stderr}")
    return out.stdout


def build_probe(kind, manifest):
    """Returns (get_jsonpath, patch_body, desired_value) for one scalar field this
    kind's Composition actually declares, so drift on it is real drift."""
    spec = manifest.get("spec", {})

    if kind == "ConfigMap":
        key = sorted(manifest["data"].keys())[0]
        return f"{{.data.{key}}}", {"data": {key: "HIJACKED"}}, manifest["data"][key]

    if kind == "Secret":
        key = sorted(manifest["stringData"].keys())[0]
        desired = manifest["stringData"][key]
        return f"{{.data.{key}}}", {"stringData": {key: "HIJACKED"}}, desired

    if kind == "Role":
        rules = manifest["rules"]
        desired = rules[0]["verbs"][0]
        bad_rules = json.loads(json.dumps(rules))
        bad_rules[0]["verbs"] = ["HIJACKED"] + bad_rules[0]["verbs"][1:]
        return "{.rules[0].verbs[0]}", {"rules": bad_rules}, desired

    if kind == "RoleBinding":
        subjects = manifest["subjects"]
        desired = subjects[0]["name"]
        bad_subjects = json.loads(json.dumps(subjects))
        bad_subjects[0]["name"] = "hijacked-sa"
        return "{.subjects[0].name}", {"subjects": bad_subjects}, desired

    if kind == "NetworkPolicy":
        desired = spec["policyTypes"][0]
        return "{.spec.policyTypes[0]}", {"spec": {"policyTypes": ["Egress"]}}, desired

    if kind == "Service":
        desired = str(spec["ports"][0]["port"])
        bad_ports = json.loads(json.dumps(spec["ports"]))
        bad_ports[0]["port"] = 9999
        bad_ports[0]["targetPort"] = 9999
        return "{.spec.ports[0].port}", {"spec": {"ports": bad_ports}}, desired

    if kind in ("Deployment", "StatefulSet"):
        desired = str(spec["replicas"])
        return "{.spec.replicas}", {"spec": {"replicas": 99}}, desired

    if kind == "PodDisruptionBudget":
        if "minAvailable" in spec:
            desired = str(spec["minAvailable"])
            return "{.spec.minAvailable}", {"spec": {"minAvailable": 0}}, desired
        desired = str(spec["maxUnavailable"])
        return "{.spec.maxUnavailable}", {"spec": {"maxUnavailable": 99}}, desired

    if kind == "ResourceQuota":
        desired = spec["hard"]["pods"]
        return "{.spec.hard.pods}", {"spec": {"hard": {"pods": "0"}}}, desired

    if kind == "LimitRange":
        req = spec["limits"][0]["defaultRequest"]
        key = sorted(req.keys())[0]
        desired = req[key]
        bad_limits = json.loads(json.dumps(spec["limits"]))
        bad_limits[0]["defaultRequest"][key] = "1m" if key == "cpu" else "1Mi"
        return f"{{.spec.limits[0].defaultRequest.{key}}}", {"spec": {"limits": bad_limits}}, desired

    raise ValueError(f"no probe defined for kind {kind}")


def list_targets():
    raw = kubectl("get", "objects.kubernetes.crossplane.io", "-o", "json", context="kind-platform")
    items = json.loads(raw)["items"]
    targets = []
    for item in items:
        name = item["metadata"]["name"]
        if not (name.startswith("checkout-service") and "connectivity" not in name):
            continue
        manifest = item["spec"]["forProvider"]["manifest"]
        kind = manifest["kind"]
        if kind in EXCLUDED_KINDS:
            continue
        ns = manifest["metadata"].get("namespace")
        rname = manifest["metadata"]["name"]
        get_jsonpath, patch_body, desired = build_probe(kind, manifest)
        targets.append({
            "object_cr": name,
            "kind": kind,
            "namespace": ns,
            "name": rname,
            "get_jsonpath": get_jsonpath,
            "patch_body": patch_body,
            "desired": desired,
        })
    return targets


def current_value(t):
    res = KIND_TO_RESOURCE[t["kind"]]
    out = kubectl(
        "get", res, t["name"], "-n", t["namespace"],
        "-o", "jsonpath=" + t["get_jsonpath"],
        context="kind-simulated-aws",
    ).strip()
    if t["kind"] == "Secret" and out:
        out = base64.b64decode(out).decode()
    return out


def inject_drift(t):
    res = KIND_TO_RESOURCE[t["kind"]]
    kubectl(
        "patch", res, t["name"], "-n", t["namespace"],
        "--type=merge", "-p", json.dumps(t["patch_body"]),
        context="kind-simulated-aws",
    )


def measure_one(t, results, lock):
    t0 = t["injected_at"]
    deadline = t0 + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            val = current_value(t)
        except RuntimeError:
            val = None
        if val == t["desired"]:
            elapsed = time.monotonic() - t0
            with lock:
                results[t["object_cr"]] = elapsed
            return
        time.sleep(POLL_SECONDS)
    with lock:
        results[t["object_cr"]] = None


def main():
    targets = list_targets()
    print(f"found {len(targets)} eligible resources (excluding {sorted(EXCLUDED_KINDS)})")
    chosen = targets[:24]
    print(f"injecting out-of-band drift into {len(chosen)} of them\n")

    for t in chosen:
        before = current_value(t)
        assert before == t["desired"], (
            f"{t['object_cr']} ({t['kind']}) already drifted before test: "
            f"{before!r} != {t['desired']!r}"
        )

    t_inject_start = time.monotonic()
    for t in chosen:
        inject_drift(t)
        t["injected_at"] = time.monotonic()
    t_inject_end = time.monotonic()
    print(f"injected {len(chosen)} out-of-band changes in {t_inject_end - t_inject_start:.2f}s\n")

    already_reverted = []
    for t in chosen:
        v = current_value(t)
        if v == t["desired"]:
            already_reverted.append(t["object_cr"])
    if already_reverted:
        print(f"note: {len(already_reverted)} of {len(chosen)} were already reverted by the "
              f"time of this check (revert faster than the inject+verify round trip): "
              f"{already_reverted}\n")

    results = {}
    lock = threading.Lock()
    threads = [threading.Thread(target=measure_one, args=(t, results, lock)) for t in chosen]
    start = time.monotonic()
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    reverted = {k: v for k, v in results.items() if v is not None}
    not_reverted = [k for k, v in results.items() if v is None]

    for name, elapsed in sorted(reverted.items(), key=lambda kv: kv[1]):
        print(f"{name}: reverted in {elapsed:.2f}s")
    for name in not_reverted:
        print(f"{name}: NOT reverted within {TIMEOUT_SECONDS}s")

    print(f"\n{len(reverted)} of {len(chosen)} reverted within {TIMEOUT_SECONDS}s")
    if reverted:
        vals = list(reverted.values())
        print(f"median revert time: {statistics.median(vals):.2f}s")
        print(f"min {min(vals):.2f}s / max {max(vals):.2f}s / mean {statistics.mean(vals):.2f}s")
    print(f"wall clock for the whole measurement: {time.monotonic() - start:.2f}s")


if __name__ == "__main__":
    main()
