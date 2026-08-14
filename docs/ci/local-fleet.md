# Local CI fleet

This document is the Vellum-side onboarding and operations note for the
Generous Corp local CI fleet. It is intentionally configuration-oriented: it
describes the labels and contracts a repository must use, while live runner
registration, credentials, leases, and host services remain outside this
repository.

## Operating principle

Use local capacity first for eligible, unprivileged work. Keep a hosted value
available as the rollback path, and never make a required job queue forever on
a local label that is unavailable or unhealthy. Protected merge-queue semantics
remain unchanged; local execution is a capacity decision, not a protection
bypass.

The local-first contract currently applies to ordinary build/test jobs only.
The following remain hosted until a separate security review proves an
equivalent trusted lane:

- `pull_request_target` and other jobs whose workflow code comes from a
  protected branch;
- signing, deployment, release publication, authority activation, and
  provenance jobs;
- jobs carrying private Pulp/Vellum keys, release credentials, or other
  repository secrets.

## Fleet roles and labels

Labels are stable capabilities, not machine names. A worker is disposable and
may receive one job only; its checkout, credentials, VM/container, and runner
registration are destroyed or revoked during teardown. Do not encode a static
runner name in a workflow.

| Role | Required labels | Intended use |
| --- | --- | --- |
| Mac Pro native Linux | `self-hosted`, `Linux`, `X64`, `vellum-build-linux-x64`, `vellum-host-macpro` | Unprivileged Linux build/test jobs |
| M1/M3/M5 Tart macOS | `self-hosted`, `macOS`, `ARM64`, `vellum-build-macos` plus a host capability label | Vellum arm64 macOS build/test jobs |
| Mac mini native macOS | `self-hosted`, `macOS`, `X64`, `vellum-build-macos-intel`, `vellum-host-macmini` | Explicit Intel/macOS canary jobs only |

The corresponding repository-scoped runner groups are `vellum-pr-safe-build`
for unprivileged PR Linux, `vellum-macos-build` for PR macOS,
`vellum-release-build` for build-only release preparation,
`vellum-macos-intel` for the Mac mini canary, and `vellum-windows-build` for
the Mac Pro Windows candidate. These groups are selected for Vellum only; they
must never be shared with Pulp or another repository.

The Mac mini is bare-metal Intel and does not run TartCI. M1, M3, and M5 are
Apple-silicon TartCI hosts. The Mac Pro is an x86_64 Proxmox/Linux host and is
not an Apple-silicon Tart lane. A host label is for capacity selection and
observability; the capability labels are the compatibility contract.

## Vellum workflow routing

Keep hosted rollback values in the repository variables and change routing only
for reviewed, safe jobs. The existing variables are the rollback surface:

```text
VELLUM_LINUX_RUNS_ON_JSON=["ubuntu-latest"]
VELLUM_MACOS_RUNS_ON_JSON=["macos-15"]
```

For a local rollout, use separate local variables or an equivalent resolver
for the eligible job class. Do not globally replace the hosted values for
provenance, authority, release, signing, deployment, or trusted merge-group
jobs. A GitHub `runs-on` value is selected when a job is dispatched; merely
listing a local label does not provide automatic fallback after the job is
queued. The resolver/operator lease must therefore choose a healthy local
lane before dispatch and choose the hosted value when local capacity is absent
or the lease expires.

Every rollout must prove both branches:

1. local assignment on the intended host, with the exact labels recorded;
2. hosted assignment after local health, capacity, registration, or lease
   failure, without changing the required check name.

Never manually cancel or reroute an unrelated merge-queue job just to test a
new lane. Use a dedicated workflow dispatch or a fresh PR/merge-group proof.

## Disposable-worker contract

Each local provider must enforce all of the following before it advertises an
idle runner:

- create a fresh VM/container from a pinned golden image;
- use a one-job ephemeral GitHub runner registration;
- provide no persistent checkout, writable shared host mount, or reusable
  credential;
- issue only the minimum repository-scoped, short-lived token needed for
  registration and revoke it at teardown;
- restrict egress to the endpoints required by the job class;
- record assignment, token minting/registration, job completion, teardown, and
  revocation in an operator-readable audit log;
- destroy the guest even when setup, checkout, cancellation, or the job fails.

The trusted Vellum merge-group lane, if introduced later, gets its own label
(`vellum-trusted-mg`) and its own security review. It must validate OIDC,
repository, ref, and merge-group event before minting any short-lived
repository-scoped credential. It must not place a long-lived GitHub App or
Vellum private key in the guest.

## New-repository checklist

When a new Generous Corp repository wants local CI:

1. Add a repository profile describing supported targets, safe job classes,
   hosted rollback selectors, and the required local capability labels.
2. Register the repository in the fleet controller/Shipyard configuration;
   use repository-scoped runner groups and workflow/ref allow-lists.
3. Provision a golden image and run a disposable golden proof before enabling
   a required job. The proof must include checkout, artifact handling,
   cancellation, and teardown.
4. Create a health lease with bounded capacity. Expired, missing, or unhealthy
   leases must select hosted capacity rather than queue indefinitely.
5. Dispatch one real repository-head proof and one merge-group-like proof where
   applicable. Record runner labels, host, duration, teardown, and fallback
   evidence in the repository's CI evidence.
6. Enable local-first routing only for the reviewed job class. Preserve exact
   workflow/check names and branch protection.
7. Add the profile to the Shipyard/TartCI fleet inventory and verify label
   synchronization on every serving host. A runner without the declared
   capability labels is not an eligible runner.

Shipyard should own the profile and health/lease decision; TartCI should own
the disposable provider lifecycle; the repository workflow should own the
safe/trusted job boundary. This separation prevents a new repository from
copying a host-specific label or silently widening a privileged lane.

## Health and rollback

Before enabling a lane, operators should check host reachability, provider
service state, available capacity, runner-group access, golden-image revision,
label correctness, and the most recent teardown record. During operation,
watch queue age and local assignment rate rather than only runner process
count.

Rollback is deliberately boring: restore the hosted JSON selector, disable
the local health lease/profile, and leave branch protection and merge-queue
requirements unchanged. Do not delete runner groups or rotate labels during a
queue incident; preserve evidence, stop new local assignments, and let active
disposable jobs finish or tear down.

## Current status

As of 2026-08-14, the Vellum-specific repository groups have been created, but
PR #35 is still using hosted selectors while the local lanes are separately
generalized and proved. The Mac Pro provider is currently Pulp-scoped, and its
available capacity must not be assumed to be Vellum capacity until a
Vellum-specific registration, label, teardown, and workflow proof has passed.
The Tart macOS hosts likewise need a Vellum-scoped runner registration and
golden-image proof before Vellum variables are changed.

Until those proofs exist, `Generous-Corp/vellum` explicitly uses the hosted
fallback values `VELLUM_LINUX_RUNS_ON_JSON=ubuntu-latest` and
`VELLUM_MACOS_RUNS_ON_JSON=macos-15`. This is intentional fail-open capacity
behavior: an absent or unhealthy local lane must not leave a required check
queued forever. Once a local lane is proven, Shipyard may select its exact
Vellum group/label under a bounded health lease and retain these values as the
rollback path.

This note is therefore an onboarding contract and target architecture, not a
claim that all local lanes are already enabled.
