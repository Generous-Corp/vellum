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
VELLUM_MACOS_RUNS_ON_JSON=["macos-15"]
```

When Shipyard enables a proven lane, it updates only the corresponding
repository-scoped variable and records the lease, capacity, selector, and
rollback value. If health expires or capacity disappears, Shipyard restores
the hosted value rather than leaving jobs queued indefinitely.

## Actions-cost controls

- PR workflows use per-ref concurrency and cancel obsolete PR commits.
- Main, tag, manually dispatched, signing, and finalizer runs are never
  canceled by PR concurrency.
- Required check names and merge-queue semantics remain unchanged.
- Full diagnostic artifacts are retained for release and failed runs; routine
  passing checks should retain only the evidence required by their contract.
- A scheduled or hosted canary may detect local drift, but it is not a reason
  to route ordinary eligible work away from healthy local capacity.

Self-hosted execution reduces GitHub-hosted runner minutes, but it does not
remove the requirement that the GitHub Actions account be in good standing.
Billing failures can prevent dispatch before any runner is selected.
