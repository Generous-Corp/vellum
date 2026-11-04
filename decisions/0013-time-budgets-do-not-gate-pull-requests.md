# Decision 0013: time-based observatory budgets do not gate pull requests

- Date: 2026-11-04
- Status: accepted (Vellum-move lead, 2026-11-04)
- Amends: Decision 0012's use of `verify` on pull requests and merge-queue builds

## Problem

`provenance-verify` judges the observatory's health budgets at the time it
runs. Two of them break with the clock alone: `overdue-event-count-exceeded`
(a pending event is past its classification deadline;
`maximum_overdue_events = 0`) and `pending-event-age-exceeded`. On 2026-11-04
at 13:35Z four Vellum events discovered on 10-31 reached their three-business-day
deadline, and from that minute every pull request and every merge-queue entry
failed `provenance-verify`. vellum#172 and #178, approved and green, were ejected
from the queue. No change a pull request could make, and no re-run, could clear
it. Only classifying the events could, which is separate work. The budget's
purpose is to make that work happen. Failing unrelated landings did not do
that; it stopped every landing while the work waited.

## Decision

1. On `pull_request` and `merge_group` runs, `verify --time-budgets report`
   returns those two budgets in `reported_time_budget_violations`, and the job
   prints a warning for each. It does not fail on them.
2. Everything else stays fatal on every event:
   - coverage gaps, such as a pull request's own mapped commit without an event;
   - the other budgets;
   - schema, append-only and ancestry checks.

   Only debt the change did not bring is reported. A pending event the change
   adds itself (absent from `--git-base`) that is already overdue or over age
   fails the run (`new-event-already-late`), and with no base the budgets are
   enforced. On every run, a new observation's `discovered_at` may not precede
   its source commit's committer time, nor lie in the future, by more than an
   hour of clock skew, so deadlines cannot be moved by backdating.
3. Pushes to `main` and release tags keep `--time-budgets enforce`, the default.
   A red `provenance-verify` on `main` holds release-tag readiness, and a tag is
   judged at its commit's time, so nothing is released while events are overdue.
4. The hourly `Observatory overdue issue` workflow keeps one issue,
   "Observatory events overdue for classification", with the current overdue
   list and the oldest pending event's age against its budget. It closes any
   duplicates, and closes the issue when the count returns
   to 0.

## Consequences

Classification debt is asked for once, in the issue and on `main`, instead of
in every queue entry. A pull request can land while events are overdue. It
cannot release, cannot introduce a gap of its own, and cannot add an event
that is already late. The negative control
is `test_time_budgets_are_reported_on_pull_requests_and_enforced_on_releases`:
the same overdue tree fails under `enforce` and passes under `report`. A gap the
pull request introduces still fails under `report`
(`test_coverage_policy_accepts_events_only_pull_request_and_rejects_missing_event`).

Rejected alternative: raising `maximum_overdue_events`. That would weaken the
release gate along with the pull-request one.
