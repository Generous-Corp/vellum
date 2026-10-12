# Pulp/Vellum change observatory

This directory is an append-only review ledger, not a source synchronizer.
`tools/provenance/observatory.py` scans both Git histories from the checked-in
cursor, maps changed and renamed paths to contracts, records patch IDs and
include/schema/build deltas, and names the destination contract tests. It never
copies or applies a patch.

The checked-in `*.yaml` files use JSON syntax. JSON is a strict YAML subset and
keeps the verifier dependency-free inside credential-bearing workflows.

## Reconcile

Run against immutable commits, not floating names:

```sh
python3 tools/provenance/observatory.py reconcile \
  --pulp-repo /path/to/pulp \
  --vellum-repo . \
  --pulp-target <exact-pulp-main-sha> \
  --vellum-target <exact-vellum-main-sha> \
  --now <utc-timestamp> \
  --write
```

The command writes new observation events first, then advances the cursor and
regenerates `reports/current.{json,md}`. An interrupted run is idempotent: an
existing event is verified against Git and is never overwritten.

Discovery defaults to `pending`; it is not a port decision. Review the event
before committing it, or add a separate append-only resolution event later.
The only supported dispositions are `pending`, `not-applicable`,
`port-required`, `ported`, `superseded`, `Pulp-only`, and `framework-only`.

An observation may have a chain of append-only resolution events when its
reviewed state changes again. Resolution timestamps must be unique for that
observation; effective state is projected in `created_at`, then event-ID order,
so the latest resolution wins independently of filename order. Add a later
resolution to expand or close an obligation—never edit or delete an earlier
observation or resolution.

## Pull requests: record events, leave the cursor

A pull request does not advance `cursor.json` or regenerate
`reports/current.*`. It records the observation events for its own mapped
commits and nothing else:

```sh
python3 tools/provenance/observatory.py reconcile \
  --pulp-repo /path/to/pulp \
  --vellum-repo . \
  --pulp-target <committed-pulp-cursor-sha> \
  --vellum-target <exact-pr-head-sha> \
  --now <utc-timestamp> \
  --write --events-only
```

`provenance-verify` runs `verify --vellum-tail-policy coverage` on pull
requests, merge-queue builds and `main`: every mapped commit after the cursor
must carry its own event in the tree, a merge that adds nothing beyond the pull
request it lands is covered by that pull request's events, and the committed
report is compared without those not-yet-reconciled events. Tags keep the
exact `reconciled` policy. After a landing the receiver
(`pulp-observatory-receiver.yml`, `push: main`) reconciles `main`, advances the
cursor and report, and opens the evidence PR on
`automation/pulp-observatory-coalesced`; the watchdog bounds the lag with
`cursor_lag_sla_minutes`. A pull request that still ends with its own
reconcile commit remains valid: an exactly reconciled evidence tail passes
under either policy.

## Verify

```sh
python3 tools/provenance/observatory.py verify \
  --pulp-repo /path/to/pulp \
  --vellum-repo . \
  --pulp-target <exact-observed-pulp-main-sha> \
  --vellum-target <exact-vellum-head-sha>
```

Verification recomputes every derived event field from the source repository,
proves each mapped commit through the cursor has an event, and rejects a mapped
commit after the cursor. Unmapped commits do not create false divergence work.
With `--git-base`, committed events may only be added; modification, deletion,
copy, or rename fails.

Classification deadlines and stop thresholds come from
`product/budgets.yaml`. Security/P0 events are due within 24 hours; ordinary
correctness, schema, importer, rendering, and platform events within three
business days; other events within seven days. Overdue events, excess backlog,
repeated generic fixes, cursor gaps, and excessive observatory effort fail
health. A release is blocked only by an overdue security event or an event
explicitly marked as a shared-contract release blocker.

The active cursor's `pulp.last_scanned_commit` began at the exact landed Pulp
activation boundary and advanced through reviewed reconciliation until
Decision 0011 froze Pulp observation at
`aa630815630ca259b743651dacdc335f3d94a39a`. Pulp observation covers history only
through that commit; later Pulp history is covered by the extraction program's
inheritance ledger and by Decision 0010's export receipts. The
`vellum.last_scanned_commit` cursor still advances with every change.
Verification rejects a mapped commit beyond the checked-in cursor. Authority
is active; the generated report records change-ledger health and currently has
no activation blockers.

## Automated receiver and watchdog

`.github/workflows/pulp-observatory-receiver.yml` is the only live mutation
path for ordinary `pulp-change-landed` delivery, trusted replay, and cutover.
All three operations share the `vellum-pulp-observatory-cursor` non-cancelling
concurrency group. That group is mutual exclusion, not a queue: every surviving
run fetches Pulp `main`, verifies the append-only durable event range, and
coalesces through the newest covered head. The completed one-time authority
activation remains independently protected; it must not enter the receiver's
lossy pending slot because activation cannot be coalesced or replaced.

Live dispatch is off unless the reviewed repository variable
`VELLUM_RECEIVER_LIVE` is exactly `true`. Replay accepts an exact Pulp commit,
requires it to descend from the committed cursor and be landed on freshly
fetched Pulp `main`, and fails closed on missing event coverage. The `enable`
operation succeeds only when the committed cursor already covers both the
requested lower bound and current durable head. Live Pulp delivery stays off
until the fixed-head catch-up has merged; the Vellum-cursor path (`push: main`)
and the watchdog are always on.

Generated evidence is pushed only to
`automation/pulp-observatory-coalesced`. The workflow opens or refreshes one
reviewed PR and never pushes main. Merge that PR with a merge commit; never
squash or rebase it. Credentials are minted per run from the same GitHub App
the merge steward uses (`actions/create-github-app-token` with the
`SHIPYARD_APP_*` secrets): a read-only token scoped to `Generous-Corp/pulp`
for the fresh Pulp checkout, and a Vellum-only token with Contents and Pull
requests write plus Actions and Variables write for the evidence push, the
evidence PR, the trusted enable barrier and replay. A PR opened with an App
token triggers its required checks; one opened with `GITHUB_TOKEN` would not,
and could never land.

`.github/workflows/pulp-observatory-watchdog.yml` runs hourly on GitHub-hosted
Linux, independent of the receiver fleet, with no configuration gate: it
checks Pulp and Vellum cursor lag, receiver run and runner-acquisition health,
missing delivery, and pending-budget pressure using the owner and thresholds in
`receiver-policy.json`. Vellum cursor lag is measured from the first
first-parent landing after the cursor (`receiver_watchdog_state.py`), is the
steady state between a landing and its evidence PR, and becomes a violation
only past `cursor_lag_sla_minutes`. A failed watchdog run is the alert and
names the response owner in its result and job summary.
