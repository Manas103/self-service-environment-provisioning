# Self-Service Environment Provisioning: Terraform Plan/Apply and a Crossplane Control Plane

Two self-service paths to the same kind of environment, built so the comparison between them is measurable in one repository rather than asserted. The original path (below, under `tools/`) is a 12-line YAML environment request, checked against an allowlist of approved module versions, planned with a policy verdict summarized before a second approver merges, then applied with OIDC-federated short-lived credentials and Vault-issued secrets. The extension (`crossplane/`) is a real Crossplane v1 control plane: a 12-line claim renders 31 managed resources across 3 platform APIs in a simulated AWS account, adopts resources already held in Terraform state, reverts out-of-band changes on its own poll loop instead of waiting for the next `terraform apply`, and clears deletion-ordering races by declaring resource usages. Every number below, in both parts, was measured on this machine by actually running the thing, not asserted.

## Part 1: the plan/apply path (original)

14 of 14 non-conforming requests are rejected before `terraform plan` is ever invoked, and 0 credential values are stored in this repository.

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

## Part 2: the Crossplane control plane (extension)

### Why this extends rather than replaces

The req that drove this extension asks for "Terraform and/or CrossPlane Claims", and the word "Claims" is specific: Crossplane v2 dropped the namespaced claim model in favor of direct composite resources, so a req that still says "Claims" is on the v1 line. This extension installs Crossplane 1.20.13 (chart `crossplane-stable/crossplane`, pinned below v2) deliberately, not the latest chart, so the claim-based workflow below is the real v1 behavior, not a v2 approximation.

Part 1's plan/apply path and this control plane answer the same user need (give a team an environment without standing credentials) with two different correctness models: plan/apply catches drift only the next time someone runs `terraform plan`; a Crossplane managed resource's own controller polls the external resource on a fixed interval and corrects it whether or not anyone asked. The adoption and drift sections below measure that difference directly against the same simulated backend Part 1 already uses.

### Honest framing, up front

- **"Simulated AWS account" is a second kind cluster, not LocalStack.** `kind-simulated-aws` stands in for the AWS account; `kind-platform` runs Crossplane and reaches it over the shared `kind` Docker network via a kubeconfig Secret and a `ProviderConfig`. The managed "AWS resources" (object store, scoped IAM, managed Postgres, namespaced app runtime) are real Kubernetes primitives (`ConfigMap`, `Secret`, `ServiceAccount`/`Role`/`RoleBinding`, `StatefulSet`, `NetworkPolicy`, ...) rather than real S3 buckets or RDS instances, for the same reason the sibling Azure repo vendors a local Terraform provider: this machine has no cloud credentials, and the claim being tested is control-plane behavior (abstraction ratio, adoption, drift correction, deletion ordering), not a specific cloud API shape.
- **The provider is the real, official `crossplane-contrib/provider-kubernetes` v0.15.0**, not a hand-written stand-in; only its two off-by-default flags were turned on (`--poll=20s` instead of the 10-minute default, `--enable-server-side-apply`), via a `DeploymentRuntimeConfig`, and that choice is itself a finding (see below).
- **Crossplane is pinned to 1.20.13 (the v1 line), not v2**, specifically so `ObjectStore`, `ManagedPostgres` and `AppRuntime` behave as Crossplane v1 Claims, matching the req's own vocabulary.
- Nothing here was ever applied against a real AWS account.

### Architecture

```
crossplane/
  xrds/
    object-store-xrd.yaml              XObjectStore: object store + scoped IAM (10 resources)
    object-store-composition.yaml      namespace, bucket config x2, encryption-key secret, access
                                        ServiceAccount, reader/writer Roles and RoleBindings, netpol
    managed-postgres-xrd.yaml          XManagedPostgres: managed Postgres (10 resources)
    managed-postgres-composition.yaml  namespace, credentials/tls secrets, connection configmap, PVC,
                                        StatefulSet, 2 Services, netpol, PodDisruptionBudget
    app-runtime-xrd.yaml               XAppRuntime: the claimed, umbrella platform API (11 direct
                                        resources, plus nested XObjectStore + XManagedPostgres = 31)
    app-runtime-composition.yaml       namespace, quota, limits, netpol, RBAC, app-config, Deployment,
                                        Service, PDB; nests one XObjectStore and one XManagedPostgres
  providerconfig/
    provider-kubernetes-runtimeconfig.yaml   DeploymentRuntimeConfig: --poll=20s, --enable-server-side-apply
  claims/
    example-checkout-service.yaml      the real 12-line claim
  terraform-adopt/
    main.tf                            9 resources a team built by hand in Terraform, before adoption
  tools/
    adopt_check.py                     adopts the 9 Terraform resources; proves 0 deletes, 0 re-creates
    drift_revert_check.py              injects out-of-band drift into 24 of 31 resources, times reversion
    usage_teardown_check.py            4 teardown races, cleared by declaring a Crossplane Usage
  tests/
    test_crossplane_claim.py           pytest integration tests against the live clusters (skip cleanly
                                        if they are not up)
  docs/
    claim_31_resources_output.txt, adopt_check_output.txt, drift_revert_output.txt,
    usage_teardown_output.txt, terraform_adoption_output.txt
```

### Design deep dives

- **Why `AppRuntime` nests `XObjectStore` and `XManagedPostgres` instead of the claim listing 3 flat resources.** A Composition's `resources[]` can be any apiVersion/kind, including another Composite Resource's, not only a leaf managed resource. Claiming `AppRuntime` cascades into its own object store and database automatically, which is the realistic shape of "claim one thing, get the whole environment"; it is also the only way one claim renders all 31 resources, since a claim is for exactly one XRD.
- **Why the nested object store and database get their own namespaces (`<app>-store`, `<app>-db`) instead of sharing the app's namespace.** The first version put all three in the same namespace and two different Composition-owned `Namespace` Objects raced to own the identical real namespace, which Crossplane correctly refused ("existing object is not controlled by this UID"); see Findings.
- **Why `ObjectStore` splits `bucket-reader` and `bucket-writer` into two Roles bound to one ServiceAccount**, instead of one Role with every verb. The claim's "scoped IAM" is a credential-scoping story; two Roles bound to the same identity is what "least privilege" means on this req, and `tests/test_crossplane_claim.py::test_scoped_iam_reader_and_writer_roles_differ` checks the verb sets actually differ (`watch` is reader-only, `update`/`patch` are writer-only) rather than just checking both Roles exist.

## Validation (Part 2)

```
$ python3 -m pytest crossplane/tests/test_crossplane_claim.py -v
4 passed in 1.59s

$ kubectl get appruntime checkout-service -n default
NAME               SYNCED   READY   CONNECTION-SECRET   AGE
checkout-service   True     True                        81s
```

Raw output: `crossplane/docs/claim_31_resources_output.txt`, `crossplane/docs/adopt_check_output.txt`, `crossplane/docs/drift_revert_output.txt`, `crossplane/docs/usage_teardown_output.txt`, `crossplane/docs/terraform_adoption_output.txt`.

## Findings (Part 2)

Three real things broke while measuring this, all kept rather than smoothed over, because each one is a genuine fact about how Crossplane's drift correction actually works, not a bug in this repo's compositions:

1. **A label-only out-of-band change is invisible to drift detection, full stop.** The first drift-revert attempt mutated each resource's `simaws.manas.dev/kind` label and waited; nothing ever reverted, with any poll interval. `provider-kubernetes`'s `IsUpToDate` check only compares the fields it actually manages; a label was never asserted to be wrong in a way its own comparison noticed in this configuration. The probes in `drift_revert_check.py` mutate a scalar field each resource's Composition actually declares (a ConfigMap value, a Role's verb, a Service's port, ...), which is real drift, not metadata noise.
2. **With server-side apply off (the provider's default), *no* field-level drift reverts, ever, regardless of poll interval.** `provider-kubernetes` ships `--enable-server-side-apply=false` by default; in that mode it diffs the desired manifest against a `kubectl.kubernetes.io/last-applied-configuration` annotation, never the live object, so an out-of-band edit is structurally invisible no matter how long you wait. Turning on `--enable-server-side-apply` (via the `DeploymentRuntimeConfig`) is what makes drift detection possible at all; this is disclosed as a required, deliberate configuration choice, not a performance tune.
3. **Even with SSA on, 3 of 24 probed resources never reverted, for a real, different reason.** They are all Kubernetes `Service` objects with exactly one port. `spec.ports` is an SSA-managed associative list keyed by `(port, protocol)`; forcing the desired `(80, TCP)` entry back in without first removing the out-of-band `(9999, TCP)` entry (which Crossplane doesn't own and so can't remove) would leave two ports on the Service, and Kubernetes itself rejects a multi-port Service whose ports aren't named (`spec.ports[0].name: Required value`). So the apply is rejected by the API server every cycle, forever, and the drift simply never clears. This is disclosed rather than hidden: 21 of 24 reverted (median 9.9s to 15.0s across two runs, see Measured results); the other 3 are a genuine, reproducible SSA-apply/API-validation interaction, not noise.

A fourth, smaller thing: declaring a Crossplane `Usage` does not make deletion wait in a `Terminating` state; `apiextensions.crossplane.io`'s `nousages` admission webhook denies the `DELETE` request synchronously, before the object is ever marked for deletion. `usage_teardown_check.py`'s step 5 retries the delete after removing the `by` resource, rather than waiting for a `Terminating` object to disappear, because there was never a pending deletion to wait on.

## Measured results (Part 2)

Machine: 8 physical / 16 logical cores (AMD Ryzen 7 7800X3D), Windows 11 Home + WSL2 Ubuntu 22.04.5 LTS (capped to 12 cores / 12 GB RAM by `.wslconfig`), 2 kind v0.24.0 clusters (`platform`, `simulated-aws`, Kubernetes v1.31.0), Crossplane 1.20.13, `crossplane-contrib/provider-kubernetes` v0.15.0 (`--poll=20s --enable-server-side-apply`), Terraform 1.9.8, Python 3.10.12.

| Claim | Measured |
|---|---|
| 3 platform APIs as Crossplane CompositeResourceDefinitions | `XObjectStore`, `XManagedPostgres`, `XAppRuntime`, each with a `v1alpha1` Claim (`ObjectStore`, `ManagedPostgres`, `AppRuntime`) |
| **one 12-line claim renders 31 managed resources** | `crossplane/claims/example-checkout-service.yaml` is 12 lines (`wc -l`); **31/31** `Object` resources Synced and Ready |
| object store with scoped IAM, managed Postgres, namespaced app runtime, in a simulated AWS account | 10 + 10 + 11 = 31 resources across 2 kind clusters; every resource lives on `kind-simulated-aws`, reached from `kind-platform` only through a `ProviderConfig` |
| **adopted 9 resources already held in Terraform state with 0 deletes and 0 re-creates** | **9/9**, uid unchanged for every resource before vs. after adoption (`adopt_check_output.txt`); `terraform plan` reports "No changes" immediately after |
| **reverted 24 of 24 injected out-of-band changes at a median 41s** | **21 of 24** reverted (3 of 24 permanently blocked by a real Service-port SSA/validation conflict, disclosed in Findings, not a timeout); median revert time **9.9s** and **15.0s** across two independent runs (noisy on this shared machine, range given), not 41s: this configuration reverts roughly 3-4x faster than the claimed median, the resume will be corrected to the measured number |
| **cleared 4 of 4 teardown races by declaring resource usages** | **4/4**: each of `bucket-encryption-key`/`bucket-config`, `bucket-access-role`/`bucket-reader-binding`, `bucket-reader-role`/`bucket-reader-binding`, `postgres-connection`/`postgres-statefulset` deleted immediately with no Usage declared, then blocked outright by the `nousages` admission webhook once declared |

## Building and running (Part 2)

```bash
# two kind clusters on the shared "kind" docker network
kind create cluster --name simulated-aws
kind create cluster --name platform
kind get kubeconfig --name simulated-aws --internal > simulated-aws.kubeconfig.internal

# Crossplane 1.20 (the Claims-supporting v1 line) with usages enabled
helm install crossplane crossplane-stable/crossplane --version 1.20.13 \
  -n crossplane-system --create-namespace --set "args={--enable-usages}"

# official provider-kubernetes, with faster polling and SSA turned on
kubectl apply -f crossplane/providerconfig/provider-kubernetes-runtimeconfig.yaml
kubectl apply -f - <<'YAML'
apiVersion: pkg.crossplane.io/v1
kind: Provider
metadata: {name: provider-kubernetes}
spec:
  package: xpkg.upbound.io/crossplane-contrib/provider-kubernetes:v0.15.0
  runtimeConfigRef: {name: provider-kubernetes-fast-poll}
YAML

kubectl -n crossplane-system create secret generic simulated-aws-kubeconfig \
  --from-file=kubeconfig=simulated-aws.kubeconfig.internal
kubectl apply -f crossplane/xrds/
kubectl apply -f crossplane/claims/example-checkout-service.yaml
# a second claim instance, so drift_revert_check.py has 24 non-Secret resources to
# probe without touching the 3 Secrets excluded in Findings (one claim only has 22)
kubectl apply -f crossplane/claims/example-checkout-service-2.yaml

python3 -m pytest crossplane/tests/test_crossplane_claim.py -v
python3 crossplane/tools/adopt_check.py
python3 crossplane/tools/drift_revert_check.py
python3 crossplane/tools/usage_teardown_check.py
```

## Sibling comparison

Part 1 (`terraform plan`/`apply`) and Part 2 (Crossplane) are now both measured against the same kind of out-of-band change. Part 1's plan-and-apply model catches drift only the next time a human or a scheduled job runs `terraform plan`; its own measured claim ("14/14 non-conforming requests rejected before plan") is about *admission*, not *ongoing* correctness. Part 2's managed resources correct 21 of 24 injected changes on their own, with a median of 9.9-15.0 seconds and no human action, because the controller's `Observe`/`Update` loop runs on a fixed poll interval regardless of whether anyone asked. The headline trade this repo now demonstrates in one place: a reconciling control plane buys continuous correction a plan/apply pipeline cannot give you, at the cost of the exact SSA-apply sharp edges documented above (label-blindness, the SSA-off default, and the Service-port associative-list conflict), none of which a `terraform plan` workflow has to deal with at all.

## Limitations

- The destination module is vendored locally, not pulled from the sibling repo by pinned git source; see Honest framing (Part 1).
- The real GitHub Actions OIDC fetch in `.github/workflows/provision.yml`'s `apply` job is authored but has not been exercised on GitHub's own infrastructure yet (it will run once this push lands, since the workflow fires on `push` to `master`, but the actual Vault-server-side wiring for that live token is a stub, disclosed in the workflow's own comments rather than faked).
- The "second approver merges" gate is a real GitHub branch-protection feature this repository should enable, not something a solo-maintainer account can demonstrate end to end with two distinct human identities; the workflow checks plan and policy, the repository setting is what would actually require two reviewers.
- Part 2's "simulated AWS account" is a second kind cluster of plain Kubernetes primitives, not a real S3 bucket or RDS instance; see Honest framing (Part 2).
- Part 2's `crossplane/tests/test_crossplane_claim.py` needs the two kind clusters, Crossplane and provider-kubernetes already installed and running; it is not a from-scratch CI job, and skips cleanly if they are not reachable.
- 3 of the 24 drift-revert probes are permanently, reproducibly blocked by a genuine SSA/Kubernetes-validation interaction on single-port Services (Findings, Part 2); this is disclosed as a real limit of this provider's drift correction, not fixed by retrying.
- This repo is one of three that form a single platform story; see also [`compliant-terraform-module-policy-gate`](https://github.com/Manas103/compliant-terraform-module-policy-gate) (the Azure-shaped module library this pipeline is designed to provision from) and [`terraform-state-refactor-drift-reconciliation`](https://github.com/Manas103/terraform-state-refactor-drift-reconciliation) (state refactor and drift detection on an existing estate).
