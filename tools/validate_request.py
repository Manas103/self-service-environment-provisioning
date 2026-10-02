"""
Validates a 12-line YAML environment request before any terraform plan is run.

Checks, in order (the first failure wins, but all are collected so the
rejection message is useful): JSON-schema shape, module source/version against
allowlist.yaml, region against the allowed-region list, ttl_hours against the
allowlist's max, and the environment_name against a simple active-ledger file
(no re-provisioning a name that's already active). A request that fails any
of these is rejected and plan() is never called; validate_and_maybe_plan()
below is the thing the pytest suite checks never calls terraform when
validation fails.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import jsonschema
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCHEMA_PATH = os.path.join(REPO_ROOT, "schema", "request_schema.json")
ALLOWLIST_PATH = os.path.join(REPO_ROOT, "allowlist.yaml")
ACTIVE_LEDGER_PATH = os.path.join(REPO_ROOT, "tools", "active_environments.json")


@dataclass
class Verdict:
    ok: bool
    violations: list = field(default_factory=list)

    def to_dict(self):
        return {"ok": self.ok, "violations": self.violations}


def _load_schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _load_allowlist():
    with open(ALLOWLIST_PATH) as f:
        return yaml.safe_load(f)


def _load_active_ledger():
    if not os.path.exists(ACTIVE_LEDGER_PATH):
        return []
    with open(ACTIVE_LEDGER_PATH) as f:
        return json.load(f)


def load_request(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate(request: dict) -> Verdict:
    violations = []

    schema = _load_schema()
    try:
        # format_checker is required here: jsonschema does not enforce
        # "format": "email" (or any format keyword) by default, it only
        # validates the shape unless a FormatChecker is passed explicitly.
        # A first version of this function omitted it and silently accepted
        # owner: "not-an-email", found by the pytest suite below and fixed
        # here rather than by weakening the test.
        jsonschema.validate(instance=request, schema=schema, format_checker=jsonschema.draft7_format_checker)
    except jsonschema.exceptions.ValidationError as e:
        violations.append(f"schema: {e.message}")
        # Downstream checks assume required fields exist; stop here if the
        # shape itself is broken, but still return a populated verdict.
        return Verdict(ok=False, violations=violations)

    allowlist = _load_allowlist()
    approved = {(m["source"], m["version"]) for m in allowlist["approved_modules"]}
    if (request["module_source"], request["module_version"]) not in approved:
        violations.append(
            f"module_not_allowlisted: {request['module_source']}@{request['module_version']} "
            f"is not in allowlist.yaml's approved_modules"
        )

    if request["region"] not in allowlist["approved_regions"]:
        violations.append(f"region_not_allowed: {request['region']}")

    if request["ttl_hours"] > allowlist["max_ttl_hours"]:
        violations.append(
            f"ttl_too_long: {request['ttl_hours']}h exceeds max_ttl_hours={allowlist['max_ttl_hours']}"
        )

    if not re.match(r"^[a-z][a-z0-9-]{2,30}$", request["environment_name"]):
        violations.append(f"environment_name_invalid: {request['environment_name']}")

    active = _load_active_ledger()
    if request["environment_name"] in active:
        violations.append(f"environment_name_already_active: {request['environment_name']}")

    if not re.match(r"^CC-[0-9]{4}$", request["cost_center"]):
        violations.append(f"cost_center_invalid: {request['cost_center']}")

    if len(request.get("approvers", [])) < 1:
        violations.append("no_approvers_named")

    return Verdict(ok=len(violations) == 0, violations=violations)


def validate_and_maybe_plan(request_path: str, plan_fn) -> Verdict:
    """The ordering guarantee the pytest suite checks: plan_fn is only ever
    called if validation passes."""
    request = load_request(request_path)
    verdict = validate(request)
    if verdict.ok:
        plan_fn(request)
    return verdict


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: validate_request.py <request.yaml>", file=sys.stderr)
        sys.exit(2)
    req = load_request(sys.argv[1])
    v = validate(req)
    print(json.dumps(v.to_dict(), indent=2))
    sys.exit(0 if v.ok else 1)
