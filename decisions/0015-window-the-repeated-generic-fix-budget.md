# Decision 0015: the repeated generic fix budget counts a recent window

- Date: 2026-11-24
- Status: accepted. The owner approved giving the budget a time window of
  about 14 days (pulp-planning `vellum-move/DECISIONS.md`, 2026-11-24, "split
  the backlog and window the repeated-fix budget").

## Problem

`maximum_repeated_generic_fixes` (2) counted every port-required and ported
event per contract key over the whole ledger, with no time limit. Its purpose,
from the extraction spec, is a stop signal: the same generic behavior keeps
needing independent fixes. A cumulative count cannot clear, so honest
migration-era port debt fills it once and keeps every later classification on
that contract over the limit. Classifying the November backlog honestly would
take `retained-ui-kernel` from 2 to 12 and `design-schema-compiler` from 2 to 8,
and fail health on every pull request, because this budget is not one of the
time budgets Decision 0013 reports instead of enforcing.

## Decision

1. `product/budgets.yaml` gains `repeated_generic_fix_window_days: 14`. A
   port-required or ported event counts only if it was classified (its latest
   resolution's `created_at`, or its `discovered_at` without one) at or after
   the judged time minus the window. The start is inclusive; there is no end,
   so an event stamped after the judged time still counts.
2. The limit stays 2.
3. The key is optional. Without it, or when a report is built without a time,
   every event counts as before. An older tree, which a release dry run or a
   stale-report check still loads, therefore judges exactly as it did, and a
   missing key can never loosen the budget. A window below one day is refused.

## Consequences

A burst of fixes to one contract still fails health. Debt older than the
window stops counting. The window only shrinks a count as time passes, so the
clock alone can clear this violation but never raise it, and Decision 0013's
reasoning about clock-driven failures does not apply. A later resolution of an
old event (port-required to ported, say) dates the event again, because doing
the port is a new fix. The release dry run's controls are
`test_a_burst_of_repeated_generic_fixes_is_refused` and
`test_repeated_generic_fixes_older_than_the_window_pass`.

Rejected alternative: raising the limit. That would also stop the budget from
catching a real burst.
