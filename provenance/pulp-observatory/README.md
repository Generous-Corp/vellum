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

The active cursor's `pulp.last_scanned_commit` begins at the exact landed Pulp
activation boundary and advances through later reviewed reconciliation.
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
requested lower bound and current durable head. Keep both live delivery and
the watchdog disabled until the fixed-head catch-up has merged.

Generated evidence is pushed only to
`automation/pulp-observatory-coalesced`. The workflow opens or refreshes one
reviewed PR and never pushes main. Merge that PR with a merge commit; never
squash or rebase it. A repository-scoped token in
`VELLUM_PULP_READER_TOKEN` needs only Contents read for
`Generous-Corp/pulp`. A separate `VELLUM_RECEIVER_ADMIN_TOKEN`, scoped only to
the Vellum repository, needs Contents and Pull requests write for evidence PRs
plus Actions and Variables write for the trusted enable barrier and replay.
Keeping the credentials separate avoids a cross-owner broad token.

`.github/workflows/pulp-observatory-watchdog.yml` runs on GitHub-hosted Linux,
independent of the receiver fleet. Once
`VELLUM_RECEIVER_WATCHDOG_ENABLED=true`, it checks cursor lag, receiver run and
runner-acquisition health, missing delivery, and pending-budget pressure using
the owner and thresholds in `receiver-policy.json`. A failed watchdog run is
the alert and names the response owner in its result and job summary.
