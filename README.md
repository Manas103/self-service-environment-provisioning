# Self-Service Environment Provisioning on Short-Lived Credentials

A 12-line YAML environment request, checked against an allowlist of approved module versions, planned with a policy verdict summarized before a second approver merges, then applied with OIDC-federated short-lived credentials and Vault-issued secrets. Every number below was measured on this machine by actually running the validator, Terraform, and a real local HashiCorp Vault server, not asserted: 14 of 14 non-conforming requests are rejected before `terraform plan` is ever invoked, and 0 credential values are stored in this repository.

## Why this exists

A platform team that builds good Terraform modules (see the sibling repo below) still has to decide how an application team actually gets an environment: not by filing a ticket and waiting, and not by handing out standing cloud credentials either. This is a small version of the self-service pattern: a YAML request, an automatic allowlist and policy check, a visible plan before anyone merges, and credentials that exist for minutes, not as a long-lived secret sitting in a repo or a runner.

## Honest framing, up front

- **The destination modules are vendored locally** (`modules/environment-unit`, built on the real zero-credential `hashicorp/local` and `hashicorp/random` providers), not pulled from the sibling repo by git source. `allowlist.yaml` documents the intended production shape: a pinned git-tag reference into [`compliant-terraform-module-policy-gate`](https://github.com/Manas103/compliant-terraform-module-policy-gate)'s modules. Vendoring a small local equivalent here keeps this repo's `terraform plan` genuinely runnable end to end, offline, without depending on the sibling's Go-based provider; the plan it produces is real, the resource shapes behind it are deliberately generic rather than Azure-specific (see that sibling repo for the Azure-shaped claim).
- **The OIDC credential exchange is real, but the issuer is a stand-in.** This machine cannot obtain a genuine GitHub Actions OIDC token outside of an actual GitHub Actions job (the token is only reachable via `ACTIONS_ID_TOKEN_REQUEST_URL`, which only exists inside a running workflow with `permissions: id-token: write`). So the numbers below come from `tools/mock_oidc_issuer.py`, a small self-signed JWT issuer carrying the same claim shape (`iss`/`sub`/`aud`/`exp`) GitHub's real token carries, validated by a real local Vault dev-mode server's real JWT auth method. `.github/workflows/provision.yml` separately authors the real path (fetching GitHub's actual OIDC token and handing it to a real network-reachable Vault server), which only exercises for real once this workflow runs on GitHub's own infrastructure; it was not run this session.
- **The second-approver gate is a real GitHub branch-protection setting on this repository, not something this workflow can fabricate** for a solo-maintainer account; see Limitations.
- Nothing here was ever applied against a real cloud.

## Architecture

```
requests/example-dev.yaml       a real 12-line environment request
schema/request_schema.json      JSON Schema for the request shape
allowlist.yaml                  approved (module source, version) pairs, approved regions, max TTL
modules/environment-unit/       the vendored destination module (local_file + random_id)
tools/
  validate_request.py           schema + allowlist + business-rule checks; plan is never called on a
                                 rejected request (validate_and_maybe_plan is the enforced ordering)
  plan_summary.py                validates, then (only if valid) runs a real terraform plan against a
                                 generated root module and formats a markdown PR-comment summary
  mock_oidc_issuer.py            self-signed JWT issuer standing in for GitHub's real OIDC provider
  issue_credentials.py           starts a real local Vault dev server, configures JWT auth trusting the
                                 mock issuer's public key, logs in, reads a KV secret, tears Vault down
  active_environments.json       tiny ledger the duplicate-name check reads
tests/
  fixtures/non_conforming/*.yaml 14 distinct non-conforming requests
  fixtures/conforming/*.yaml     3 conforming requests
  test_validate_request.py       14/14 rejected, plan never called; conforming accepted and planned
  test_vault_credential_flow.py  real Vault JWT login/secret-read/rejection, and a repo-wide grep that
                                 no demo secret value lives outside the one script that creates it
docs/
  pytest_output.txt              raw 38-test run
  vault_credential_flow_output.txt   raw Vault flow result
  plan_summary_example.txt       raw plan + policy verdict for requests/example-dev.yaml
.github/workflows/
  ci.yml                         installs vault + terraform, runs the full pytest suite
  provision.yml                  validate+plan+summary on every request PR; a separate apply job
                                 authoring the real GitHub OIDC fetch (see Honest framing)
```

### Design deep dives

- **Why `validate_and_maybe_plan()` takes a callable instead of just returning a bool.** The claim is an ordering guarantee ("rejected *before* plan"), not just a final-state guarantee. Passing `plan_fn` in lets the test suite assert `plan_fn.assert_not_called()` with a real `unittest.mock.Mock`, which catches a bug a simple `if not valid: return` could still get wrong (e.g. a stray call above the check).
- **Why `jsonschema.validate()` needs an explicit `format_checker`.** Found the hard way (see Findings): `jsonschema` does not enforce `"format": "email"` unless a `FormatChecker` is passed, so a first version of the validator silently accepted `owner: "not-an-email"`.
- **Why Vault's `jwt_validation_pubkeys` instead of `oidc_discovery_url`.** `oidc_discovery_url` requires Vault to fetch a live JWKS endpoint over the network; a pre-shared PEM public key needs no network call at all, which is what makes the mock-issuer path fully reproducible offline while still exercising the real JWT-auth validation logic.

## Validation

```
$ python3 -m pytest tests/ -v
...
38 passed in 3.33s

$ python3 tools/issue_credentials.py
{
  "valid_jwt_login_ok": true,
  "valid_jwt_secret_read_ok": true,
  "invalid_jwt_rejected": true,
  "client_token_written_to_disk": false
}
```

Raw output: `docs/pytest_output.txt`, `docs/vault_credential_flow_output.txt`, `docs/plan_summary_example.txt`.

## Findings

Two real bugs found while building this, both kept rather than smoothed over:

1. **The email-format bug above.** `jsonschema.validate(instance, schema)` with no `format_checker` argument validates shape but not `format` keywords at all; `owner: "not-an-email"` passed validation on the first run. The fix (`format_checker=jsonschema.draft7_format_checker`) is a one-line change, but the lesson is that `jsonschema`'s format checking is opt-in, not a safe default, which is easy to miss when the schema *looks* like it is already enforcing it.
2. **A self-matching test.** The first version of `test_no_vault_token_or_secret_value_is_committed_to_the_repo` failed against its own file, because the test's source code necessarily contains the literal string it is checking for (to know what to exclude). Fixed by excluding the test file itself from the scan, which is a small reminder that a "grep the repo for X" test has to think about where X legitimately appears on purpose.

## Measured results

Machine: 8 physical / 16 logical cores (AMD Ryzen 7 7800X3D), Windows 11 Home + WSL2 Ubuntu 22.04.5 LTS (capped to 12 cores / 12 GB RAM by `.wslconfig`), Terraform 1.9.8, Vault 1.18.3, Python 3.10.12.

| Claim | Measured |
|---|---|
| 12-line YAML request checked against an approved-module-version allowlist | `requests/example-dev.yaml` is 12 lines; checked by `tools/validate_request.py` against `allowlist.yaml` |
| plan plus policy verdict summarized before a second approver merges | `tools/plan_summary.py`; real `terraform plan` output for a conforming request, real rejection reason for a non-conforming one (see `docs/plan_summary_example.txt`) |
| apply uses OIDC-federated short-lived credentials and Vault-issued secrets | real local Vault dev server, real JWT auth method, real short-lived client token, real KV secret read; mock issuer locally, real GitHub issuer authored in CI (see Honest framing) |
| **14 of 14 non-conforming requests rejected before plan** | **14/14**, each via a distinct violation, `plan_fn` proven never called (`unittest.mock.Mock.assert_not_called`) |
| **0 stored credentials in the repository or runner** | **0/0** by repo-wide grep (`test_no_vault_token_or_secret_value_is_committed_to_the_repo`); the Vault client token lives only in the Python process's memory for the duration of the request |

## Building and running

```bash
pip install -r requirements.txt

# validate + plan a request
python3 tools/plan_summary.py requests/example-dev.yaml

# the full test suite (needs a `vault` binary on PATH or $VAULT_BIN)
python3 -m pytest tests/ -v
```

## Limitations

- The destination module is vendored locally, not pulled from the sibling repo by pinned git source; see Honest framing.
- The real GitHub Actions OIDC fetch in `.github/workflows/provision.yml`'s `apply` job is authored but has not been exercised on GitHub's own infrastructure yet (it will run once this push lands, since the workflow fires on `push` to `master`, but the actual Vault-server-side wiring for that live token is a stub, disclosed in the workflow's own comments rather than faked).
- The "second approver merges" gate is a real GitHub branch-protection feature this repository should enable, not something a solo-maintainer account can demonstrate end to end with two distinct human identities; the workflow checks plan and policy, the repository setting is what would actually require two reviewers.
- This repo is one of three that form a single platform story; see also [`compliant-terraform-module-policy-gate`](https://github.com/Manas103/compliant-terraform-module-policy-gate) (the Azure-shaped module library this pipeline is designed to provision from) and [`terraform-state-refactor-drift-reconciliation`](https://github.com/Manas103/terraform-state-refactor-drift-reconciliation) (state refactor and drift detection on an existing estate).
