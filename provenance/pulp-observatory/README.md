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

The prepared cursor's `pulp.last_scanned_commit` is the exact observed Pulp
main boundary. Verification rejects a mapped commit beyond that cursor.
Authority is not active; the report lists the remaining activation gates.
