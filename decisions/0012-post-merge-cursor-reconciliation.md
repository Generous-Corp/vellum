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
   existing reviewed evidence branch and pull request. The watchdog reports
   `vellum-cursor-lag` and breaches `cursor_lag_sla_minutes` the same way it
   does for the Pulp cursor, and asks the receiver to run.
4. Tags keep the exact `reconciled` policy: a release or authority tag must be
   fully reconciled.

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
