# Local-first CI routing

Vellum prefers local compute only after the local lane is demonstrably safe.
The checked-in profile records the intended order in `activation_targets`,
while its operative `targets` list and every workflow remain GitHub-hosted until
the matching Vellum-only runner group, health lease, disposable job, and
teardown proof exist. A profile entry is not activation evidence.

## Current posture

The 2026-08-14 live audit found:

- `Generous-Corp/vellum` has zero repository-visible runners.
- Organization groups `vellum-pr-safe-build`, `vellum-release-build`,
  `vellum-macos-build`, `vellum-macos-intel`, and `vellum-windows-build` select
  Vellum but contain no runners. Their workflow-reference restriction is off,
  so they are not safe PR admission boundaries yet.
- The two visible Mac Pro Linux x64 workers are in the Pulp trusted group and
  carry only `pulp-*` capability labels. One free Pulp slot is not Vellum
  capacity.
- M1, M3, and M5 run Pulp-scoped TartCI services; M3 also has a Forge runner.
  Mac Pro has a Pulp-scoped coordinator, and Mac mini has no serving runner.
  No host has a Vellum-scoped supervisor, profile, label set, or registration
  authority installed.
- The GitHub App could read Actions variables, runner inventory, and group
  inventory. A write-scoped Vellum JIT/registration path for the serving
  supervisors was not installed or proved, and this rollout did not mint a
  token or register a runner.
- The legacy repository variables currently contain the bare strings
  `ubuntu-latest` and `macos-15`, while the old workflows passed them through
  `fromJSON`. Workflows now name their hosted executors directly, so malformed,
  stale, or prematurely changed variables cannot create an unsatisfiable local
  queue. No live variable was changed by this repository update.

These are exact missing prerequisites, not evidence that local capacity is
impossible. The local targets in
`.shipyard/ci-profiles/normal-local-fast.toml` therefore remain
`proven = false` and the hosted values remain effective. The static Shipyard
and TartCI profile planners currently choose the first operative target without
evaluating `proven` or lease metadata. Keeping only the hosted target in
`targets` makes their dry-run fail-safe; the repository resolver evaluates the
separate local-first activation chain.

## Workflow classification and stable checks

| Workflow job(s) | Check name(s) preserved | Current executor | Classification |
| --- | --- | --- | --- |
| Product contract | `product-quality` | `ubuntu-latest` | Unprivileged; future PR-safe Linux candidate |
| Provenance | `forbidden-deps`, `provenance-verify` | `ubuntu-latest` | Read-only; future PR-safe Linux candidate |
| README proof | `clean-release` | `macos-15` | Unprivileged; future disposable macOS candidate |
| Locked GPU proof | `gpu-macos-arm64`, `sterile-consumer` | `macos-15` | Mixed build and release publication; hosted until split |
| Merge steward | `merge-exact-green-head` | `ubuntu-latest` | Repository-write credential; hosted-only |
| Authority activation | `authority-active` | `ubuntu-latest` | Private-key/App-token work; hosted-only |
| Authority release | `finalize-authority-release` | `ubuntu-latest` | Immutable release finalization; hosted-only |

The GPU workflow cannot truthfully move to a generic local pool yet. Both jobs
contain tag-only private-release mutation, and the workflow has repository-write
permission. A later change must split unprivileged build/proof from hosted
publication without renaming the stable checks before its build half can use a
local selector.

The three unprivileged checks are candidates, not active PR admission. Their
runner group must accept only the trusted protected-main workflow reference; a
contributor-controlled workflow revision must not select the local pool. If an
ordinary `pull_request` run cannot satisfy that boundary, onboarding must first
add a protected dispatch/admission shape in a separately claimed change.

## Inspect and plan

Shipyard owns fleet-wide reconciliation; TartCI owns the disposable provider;
the repository owns workflow trust classification. Inspection is always safe:

```bash
ghapp api repos/Generous-Corp/vellum/actions/runners
ghapp api orgs/Generous-Corp/actions/runner-groups
shipyard --json runner capacity
shipyard ci profile plan normal-local-fast \
  --repo Generous-Corp/vellum \
  --profile-file .shipyard/ci-profiles/normal-local-fast.toml \
  --json
```

The current Shipyard command is a static, read-only profile plan. For this
baseline it must report `strategy: github-only`, a `github.*` `selected_now`,
and no local variable change. It is not the fleet-aware activation decision and
must not be used to apply an `activation_targets` entry.

The repository planner consumes a read-only inventory and emits one concrete
selector. With no inventory it must choose hosted:

```bash
python3 scripts/local_ci_route.py \
  --lane pr.linux \
  --event workflow_run \
  --now 2026-08-14T21:00:00Z
```

Inventory has this bounded shape:

```json
{
  "repository": "Generous-Corp/vellum",
  "workflow_ref": "Generous-Corp/vellum/.github/workflows/product-quality.yml@refs/heads/main",
  "leases": {
    "VELLUM_PR_SAFE_LINUX_LEASE_UNTIL": "2026-08-14T21:04:00Z"
  },
  "groups": {
    "vellum-pr-safe-build": {
      "repositories": ["Generous-Corp/vellum"],
      "restricted_to_workflows": true,
      "allows_public_repositories": false,
      "workflow_access": [
        "Generous-Corp/vellum/.github/workflows/product-quality.yml@refs/heads/main",
        "Generous-Corp/vellum/.github/workflows/provenance.yml@refs/heads/main"
      ]
    }
  },
  "runners": [
    {
      "name": "vellum-pr-safe-ephemeral-example",
      "repository": "Generous-Corp/vellum",
      "group": "vellum-pr-safe-build",
      "status": "online",
      "busy": false,
      "healthy": true,
      "ephemeral": true,
      "teardown_proven": true,
      "credentials_reusable": false,
      "egress_policy_proven": true,
      "writable_host_mounts": [],
      "labels": [
        "self-hosted",
        "Linux",
        "X64",
        "vellum-build-linux-x64",
        "vellum-host-macpro",
        "vellum-pr-safe-linux-x64"
      ]
    }
  ]
}
```

Even that inventory selects hosted today because the target is deliberately
unproven. Tests exercise a simulated proven target to show that the same
resolver selects local only when the event, exact labels, group restriction,
repository scope, protected workflow ref, one-job identity, health, five-minute
lease, idle capacity, egress, credential, mount, and teardown facts all agree.
Expiry, widening, or removal of any one fact is the causal fallback control.

## Apply and activation gate

There is no repository-side apply command. Apply belongs to Shipyard because it
must reconcile the host service and GitHub state atomically and idempotently.
The installed Shipyard/TartCI profile planner does not yet implement that
fleet-aware apply path; until it does, its only safe plan is the hosted-only
operative list. Before Shipyard may enable one Vellum local selector, it must:

1. install a Vellum-specific supervisor with repository-scoped JIT authority;
2. restrict the Vellum-only group to the exact protected-main workflow refs;
3. register a one-job worker with the exact profile labels and no Pulp/Forge
   aliases;
4. prove a pristine checkout, no reusable credentials, no writable shared host
   mounts, bounded egress, capacity, cancellation, and teardown on every exit;
5. dispatch one real Vellum job and retain its runner name, labels, group,
   assignment, result, teardown, and registration-revocation evidence;
6. add that evidence in the same reviewed change that flips the one target's
   `proven` bit, promotes its `activation_targets` chain into the operative
   route, and establishes its at-most-five-minute health lease; and
7. prove lease expiry chooses the same hosted runner and check name.

If registration authority, host access, or any proof is missing, apply must be
a no-op and report the exact missing item. It must never repurpose a Pulp or
Forge runner, widen a group, guess a label, or leave a required job waiting on
an absent runner.

## Teardown and rollback

The supervisor owns teardown with `INT`, `TERM`, and `EXIT` traps. It must stop
the guest, delete the disposable clone, revoke or expire the JIT registration,
remove checkout and job credentials, and verify that its own slot has no
residue. A failed setup, checkout, cancellation, test, or upload takes the same
cleanup path.

Rollback is selection-only: expire or remove the local health lease and let the
planner choose the final hosted target. Do not rename checks, delete groups, or
relabel an in-flight worker during an incident. The fixed hosted workflow
selectors in this baseline are the final safety net until protected admission
is separately implemented and dispatched successfully.
