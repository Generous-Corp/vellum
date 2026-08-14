# Decision 0009: local-first CI with an explicit hosted fallback

- Date: 2026-08-14
- Status: local disposable capacity is preferred; hosted capacity is a
  fail-safe fallback when the local health lease is absent or exhausted

The repository may configure a GitHub-hosted fallback now that billing is
restored, but ordinary eligible work must prefer a proven, repository-scoped
local lane. Shipyard/TartCI owns the health lease and may enable the local
selector only after proving disposable execution, isolation, teardown, and
capacity. An expired lease or unavailable local capacity must select the
reviewed hosted fallback instead of leaving a job queued indefinitely.

This decision supersedes Decision 0007's temporary prohibition on configuring
hosted capacity. It does not make hosted runners the default, authorize them
for secret-bearing or privileged work, or claim x64 coverage that has not been
proved. The ARM64 self-hosted baseline and x64 validation warning in Decision
0007 remain valid wherever a proven local lane is not available.

## Cost and concurrency guardrails

- Required check names and protected merge semantics remain unchanged.
- PR concurrency may cancel an obsolete commit for the same pull request.
- Main, tag, scheduled, manual, signing, and finalization runs use unique
  concurrency keys and are never canceled by PR concurrency.
- Local execution is preferred only for unprivileged work with a healthy,
  disposable, repository-scoped lease; hosted execution remains the fallback.
- Every new runner class requires a live proof and an explicit profile/label
  mapping before Shipyard can select it.
