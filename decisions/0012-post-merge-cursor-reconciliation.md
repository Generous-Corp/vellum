# Decision 0012: pull requests record events; the receiver advances the cursor

- Date: 2026-10-11
- Status: accepted (Vellum-move owner sign-off 2026-10-11, "Cursor serialization Option A")
- Supersedes: the per-pull-request reconcile commit that Decision 0008's
  observatory contract implied

## Problem

`verify_vellum_observatory_tail` required every commit after
`cursor.vellum.last_scanned_commit` to be evidence only, and accepted a merge
commit only when its tree equalled the pull request's. Each pull request
therefore had to end with its own `reconcile --write` commit rewriting
`cursor.json` and `reports/current.*`, and had to contain main's exact tip when
it merged. Two open pull requests always conflicted on those two files, and
whichever landed second had to merge main and reconcile again. With a merge
queue the same rule rejects every batch entry after the first, because the
queue's merge commit contains the other entries.

## Decision

1. A pull request records the observation events for its own mapped commits
   (`reconcile --write --events-only`) and does not touch the cursor or the
   report.
2. `verify --vellum-tail-policy coverage` (pull requests, merge-queue builds,
   pushes to `main`) accepts commits after the cursor when every mapped one
   carries its own event. A two-parent merge whose changes relative to its first
   parent are exactly its second parent's changed paths, with the second
   parent's blobs, adds nothing beyond that pull request and needs no event of
   its own; a merge whose resolution produced other content does. A pull
   request behind `main` is judged on its own commits from the merge base. The
   committed report is compared without the not-yet-reconciled events.
3. The receiver (`pulp-observatory-receiver.yml`) also runs on every push to
   `main`, reconciles it when the cursor lags behind ordinary commits (an
   evidence-only tail is not lag), and lands cursor and report through the
   existing evidence branch and pull request. That pull request is gated by
   the required checks and auto-merged by the merge steward on green; no
   human reviews it. The watchdog reports
   `vellum-cursor-lag` and breaches `cursor_lag_sla_minutes` the same way it
   does for the Pulp cursor, and asks the receiver to run.
4. Tags keep the exact `reconciled` policy: a release or authority tag must be
   fully reconciled.

## Edge cases, stated

- **Integration merge, precisely.** The rule is symmetric, so it judges a
  queue merge (first parent `main`, second parent the pull request) and an
  author's merge of `main` into a pull request alike: for every observable path
  changed on either side since the merge base, the merge carries exactly the
  `(mode, blob)` entry of the side that changed it (absent on both counts as
  equal, so a deletion lands; the mode counts, so a merge that drops a mapped
  script's executable bit is a change of its own). A path both sides changed
  is covered only when both sides and the merge hold the same entry, which is
  the same fix landing twice; a path the two sides changed differently, a path
  only the merge changed, or a path the merge did not carry over from the side
  that changed it makes the merge a change of its own. Unmapped paths are
  ignored, so two queue entries that share only unmapped files do not eject
  each other; build files under a mapped root (transitive paths) are not
  ignored, because the observation for the real change lists them.
- **A merge that drops the other side's change** (an author's merge of `main`
  that keeps the pull request's blob over `main`'s fix) shows no mapped change
  against its first parent, so no observation can describe it. The coverage
  check reports it as `merge-changes-other-side-without-first-parent-diff`; the
  remedy is to make the revert an explicit commit on the branch, which does get
  its own event and its own review. In the queue direction the same revert is
  visible against `main` and needs (and can carry) an event.
- **The same fix on both sides.** A hotfix cherry-picked into a pull request
  that `main` also landed leaves both sides with the same entry, and the merge
  is covered. Had the two sides resolved the file differently, the queue merge
  would need an event it cannot carry and the update-branch merge would show
  no first-parent change, so the only remedy is to rebase the duplicate away.
- **Octopus merges** (three or more parents) are never covered; they fail
  closed and must carry their own event.
- **A queue merge that needs its own event is unlandable by construction**:
  the merge commit does not exist until the queue creates it, so no pull
  request can carry its event. The remedy is always to update the branch
  (merge `main` into it) so the queue merge becomes a plain integration merge,
  or to record the resolved merge's event in the pull request itself.
- **The receiver backfills.** A full reconcile on `main` creates `pending`
  observation events for any mapped commit that landed without one (for
  example an administrator's direct push). Those events are not a free pass:
  they enter the ordinary review queue and age against the same budgets, and
  the push-to-`main` provenance run reports the uncovered commit before the
  receiver runs. Coverage at merge time is enforced by `provenance-verify` on
  `merge_group` and `push`; the receiver is a discovery backstop, not a second
  gate.
- **Pull requests that change the mapping or add resolution events** still
  regenerate the report, and still conflict with each other exactly as before;
  only observation events for the pull request's own commits are excluded from
  the report comparison.
- **The lag clock** starts at the committer time of the first first-parent
  landing after the cursor that the cursor does not already cover, never at
  `main`'s tip, so steady traffic cannot keep the SLA from being breached. The
  receiver's own evidence merge and a tree-identical merge are covered landings
  and do not start the clock, so the first real change after a quiet period is
  not reported as an immediate breach. Lag inside the SLA is the steady state
  and is reported, not a violation.

## What is kept

- Coverage: every mapped commit has an event, verified against Git, before
  and after the cursor.
- Append-only events, cursor monotonicity, report equality at the cursor.
- Decision 0008: merge commits, never squash or rebase.

## What is relaxed

Currency: `main` may be ahead of the cursor for at most the watchdog SLA
instead of at every commit. Rejected alternatives and the measured reasons are
recorded in the Vellum-move planning record
(`vellum-move/evidence/A-governance/cursor-serialization-proposal.md`).
