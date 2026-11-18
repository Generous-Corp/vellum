# Local-first Actions policy

Vellum should use the local disposable fleet whenever a healthy, repository-
scoped lane is available. GitHub-hosted runners remain the explicit fallback;
they are not the preferred capacity.

## Lane policy

| Work | Preferred local capacity | Fallback |
| --- | --- | --- |
| Unprivileged Linux validation | Mac Pro disposable Linux x64 | `ubuntu-latest` |
| macOS ARM64 validation and GPU proof | M3, M5, then M1 Tart | `macos-15` |
| Intel macOS compatibility canary | Mac mini native Intel | hosted Intel macOS |
| Windows compatibility candidate | Mac Pro disposable Proxmox Windows | `windows-latest` |
| Signing, deployment, finalization, and secret-bearing work | hosted trusted lane | hosted trusted lane |

The local selectors must only be enabled by the Shipyard/TartCI health lease
after a disposable-worker proof has passed. A selector must never be enabled
merely because a runner is registered: no persistent checkout, credentials,
writable host mounts, or cross-job worker state are permitted. Teardown and
credential revocation are part of the proof.

Until each lane has a live proof, the repository variables remain the hosted
fallback values:

```text
VELLUM_LINUX_RUNS_ON_JSON=["ubuntu-latest"]
VELLUM_LOCAL_MACOS_RUNS_ON_JSON=["macos-15"]
VELLUM_MACOS_RUNS_ON_JSON=["macos-15"]
```

When Shipyard enables a proven lane, it updates only the corresponding
repository-scoped variable and records the lease, capacity, selector, and
rollback value. If health expires or capacity disappears, Shipyard restores
the hosted value rather than leaving jobs queued indefinitely.

## Actions-cost controls

- PR workflows use per-ref concurrency and cancel obsolete PR commits.
- Non-PR workflows use a unique run ID in their concurrency key, preventing
  GitHub from silently replacing a pending main, tag, scheduled, or manual
  validation even when cancellation is disabled.
- Main, tag, manually dispatched, signing, and finalizer runs are never
  canceled by PR concurrency.
- Required check names and merge-queue semantics remain unchanged.
- Every workflow that produces a required check also runs on `merge_group`,
  so a merge queue on `main` can admit entries. Admission is the job of the
  Shipyard steward tick only (exact-head approval plus green, then enqueue);
  no workflow in this repository arms auto-merge or merges.
- Pull requests record their own observatory events and leave the cursor to
  the receiver (Decision 0012), so queue entries no longer serialize on
  `provenance/pulp-observatory/cursor.json`.
- Full diagnostic artifacts are retained for release and failed runs; routine
  passing checks should retain only the evidence required by their contract.
- A scheduled or hosted canary may detect local drift, but it is not a reason
  to route ordinary eligible work away from healthy local capacity.

Self-hosted execution reduces GitHub-hosted runner minutes, but it does not
remove the requirement that the GitHub Actions account be in good standing.
Billing failures can prevent dispatch before any runner is selected.


## Required Vellum macOS GPU checks

`gpu-macos-arm64` and `sterile-consumer` use `VELLUM_LOCAL_MACOS_RUNS_ON_JSON`
with `["macos-15"]` as the reversible fallback. The only reviewed local value
is `["self-hosted","macOS","ARM64","vellum-build-macos"]`. The local
profile registers one disposable Tart supervisor on each of M1, M3, M5, and
M5 Studio with that label; each Vellum lane is capped at one VM and uses the
`vm` lease class (priority 60), below Pulp's required `pulp-build-pr-head`
(100) and `pulp-build-merge-group` (110) leases.

The runner group must be restricted to this workflow and the repository must
require approval for outside contributors before the selector is enabled. The
workflow and TartCI admission checks reject fork head repositories. A Vellum
VM does not preempt a running Pulp VM and the `vm` priority does not provide a
cross-repository yield; it can occupy an otherwise free host slot until its
job completes. That bounded exposure is recorded in the fleet evidence.

When the proof job reports a reused or uncovered proof, `sterile-consumer`
only prints a notice and runs on a hosted `ubuntu-24.04` runner. On
`merge_group` runs, the hosted `cancel-destroyed-groups` job cancels the
proofs of merge groups that the queue has destroyed (see
`decisions/0008-merge-commits-are-load-bearing.md`).

`VELLUM_LOCAL_MACOS_MERGE_GROUP_RUNS_ON_JSON` is optional. When it is set,
only `merge_group` runs of these two jobs select it; `push` and
`pull_request` keep `VELLUM_LOCAL_MACOS_RUNS_ON_JSON`, so a push re-proof can
never queue ahead of a merge-queue entry. The only reviewed value is
`["self-hosted","macOS","ARM64","vellum-build-macos-merge-group"]`, the class
label TartCI's Vellum lanes register for their merge-group tier. Unset it to
roll back; every event then selects the generic class again.

### Deciding reporting runs before a Mac is leased

A `gpu-macos-arm64` run that only reports needs no Mac. That covers three
cases: a change the base's classifier says the proof does not cover, a
merge-queue run that reuses its pull request's identical-tree proof, and the
push to `main` that reuses its merge-queue proof. On the fleet such a run
waited 20 to 40 minutes for a lease to print a notice.

When the repository variable `VELLUM_GPU_PREDECIDE` is exactly `on`, the
`decide` job runs first, on `VELLUM_DECIDE_RUNNER` (a JSON label list;
unset, hosted `["ubuntu-24.04"]`). It runs the proof job's own checkout,
classify and reuse steps, copied byte for byte and held equal by
`scripts/test_ci_runner_policy.py`, and outputs `unaffected` and
`reused_run`. When either is set, `gpu-macos-arm64` runs on decide's runner.
The proof job repeats both decisions itself, and "Agree with the early
decision" fails the job on any difference, before any step that builds.

What still runs on a real Mac GPU is unchanged: the full pull-request proof,
the cheap pull-request lane, the full merge-group proof, push and tag proofs
that build, and the real `sterile-consumer` journey.

A failed or skipped `decide` leaves the proof job on its usual runner. The
guards of both proof jobs name their own status check (`!cancelled()`, and
for `sterile-consumer` the proof job's result), so `decide` can never skip a
required context, which would read as passing.

To roll back, delete `VELLUM_GPU_PREDECIDE` or set it to anything but `on`.
`decide` is then skipped and `gpu-macos-arm64` starts at once on the fleet
selector, as before.

`VELLUM_MACOS_RUNS_ON_JSON` remains the hosted selector used by
`readme-quick-start.yml` and the hosted portion of `sdk-release.yml`; it is not
the selector for these two required GPU jobs.

## The cheap pull-request lane

A pull request does not run the full GPU proof by default. `gpu-macos-arm64`
(the same required context, the same job) runs the cheap lane instead: the
release-source contract, a configure and build with the same flags as the full
proof (the pinned Skia/Dawn archive, mirror and RmlUi included), and
`ctest -L pr-fast`. `sterile-consumer` prints a notice on a hosted runner. The
merge group runs the full GPU proof and `sterile-consumer` exactly as before,
on the merged tree, so `main` keeps its guarantee. The cheap lane writes no
`vellum-gpu-proof-tree` receipt, so the merge group never reuses a cheap run in
place of a GPU proof.

A pull request runs the full proof now, as it did before, when any of these hold:

- the base's classifier (`scripts/classify_gpu_proof.py --early-proof`, read
  from the base, never from the change) says a changed path can only be proved
  on a GPU: `graphics/`, `gpu-runtime/`, `apps/`, `packages/vellum-swift/`,
  shader sources, `provenance/third-party-lock.json`, the proof workflow and
  the classifier itself;
- the pull request carries the `gpu-proof` label (the labels are read when the
  job starts, so add the label and re-run the job);
- the base has no `--early-proof` flag, the label list cannot be read, or the
  verdict is anything but `cheap`: every doubt takes the full proof.

The cheap lane runs on the same runner class as the old pull-request proof
(`VELLUM_LOCAL_MACOS_RUNS_ON_JSON`). No separate build-only class is
registered with TartCI yet. `decide` routes only runs that report, so the
cheap lane still builds on the fleet.

### Roll back

Set the repository variable `VELLUM_PR_GPU_PROOF` to `full`. Every pull request
then takes the early full proof again. Delete the variable to return to the
cheap lane. Nothing else changes: the required context names, the merge group
and the receipts are the same either way.

### Retry and alarms

`gpu-proof-guard.yml` re-runs the failed jobs of a pull-request or queue run
once, and only when every failed job failed on infrastructure (a lost runner,
a 5xx or refused download). A failure with a defect signature in its log, a
second attempt and a push are never retried. Every six hours it also checks the
last day of queue runs and opens, or updates, one tracking issue titled
"Cheap pull-request lane: alarm" when more than one queue entry was ejected,
any queue or `main` run failed at a GPU-only step, or more than one in ten
queue runs failed. The issue closes when the alarms clear. Both live in
`scripts/gpu_proof_guard.py`.

### Organization settings required before enablement

An organization administrator must update runner group **8**
(`vellum-macos-build`) to:

1. allow public repositories (Vellum is public);
2. restrict access to `Generous-Corp/vellum/.github/workflows/gpu-macos.yml@refs/heads/main`;
3. require approval for all outside contributors, including returning fork
   contributors.

These settings are part of the local runner boundary and must be delta-checked
with the TartCI admission receipt before the repository variable is set.
