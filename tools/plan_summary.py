"""
Turns a validated environment request into a real `terraform plan` and a
markdown summary (plan + policy verdict) suitable for posting as a PR
comment, before a second approver merges. validate_request.validate() is
called first; an invalid request never reaches terraform plan (this is the
same ordering guarantee tests/test_validate_request.py checks).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from validate_request import load_request, validate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(REPO_ROOT, "modules", "environment-unit")
TERRAFORM_BIN = os.environ.get("TERRAFORM_BIN", "terraform")


def _write_root_module(workdir: str, request: dict) -> None:
    tf = f"""
module "env" {{
  source            = "{MODULE_DIR.replace(os.sep, '/')}"
  environment_name  = "{request['environment_name']}"
  region            = "{request['region']}"
  ttl_hours         = {request['ttl_hours']}
  owner             = "{request['owner']}"
  cost_center       = "{request['cost_center']}"
}}
"""
    with open(os.path.join(workdir, "main.tf"), "w") as f:
        f.write(tf)


def run_plan(request: dict) -> str:
    workdir = tempfile.mkdtemp(prefix="envreq-plan-")
    _write_root_module(workdir, request)
    subprocess.run([TERRAFORM_BIN, "init", "-input=false"], cwd=workdir, check=True, capture_output=True)
    result = subprocess.run(
        [TERRAFORM_BIN, "plan", "-input=false", "-no-color"],
        cwd=workdir, capture_output=True, text=True,
    )
    shutil.rmtree(workdir, ignore_errors=True)
    return result.stdout


def summarize(request_path: str) -> str:
    request = load_request(request_path)
    verdict = validate(request)

    lines = [f"### Environment request: `{request.get('environment_name', '(unparseable)')}`", ""]
    lines.append("**Policy verdict:** " + ("PASS, all checks satisfied" if verdict.ok else "REJECTED before plan"))
    if not verdict.ok:
        lines.append("")
        lines.append("Violations:")
        for v in verdict.violations:
            lines.append(f"- {v}")
        return "\n".join(lines)

    lines.append("")
    lines.append("**Terraform plan:**")
    lines.append("```")
    lines.append(run_plan(request).strip())
    lines.append("```")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(summarize(sys.argv[1]))
