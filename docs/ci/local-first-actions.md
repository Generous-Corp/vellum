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

`VELLUM_MACOS_RUNS_ON_JSON` remains the hosted selector used by
`readme-quick-start.yml` and the hosted portion of `sdk-release.yml`; it is not
the selector for these two required GPU jobs.

### Organization settings required before enablement

An organization administrator must update runner group **8**
(`vellum-macos-build`) to:

1. allow public repositories (Vellum is public);
2. restrict access to `Generous-Corp/vellum/.github/workflows/gpu-macos.yml@refs/heads/main`;
3. require approval for all outside contributors, including returning fork
   contributors.

These settings are part of the local runner boundary and must be delta-checked
with the TartCI admission receipt before the repository variable is set.
