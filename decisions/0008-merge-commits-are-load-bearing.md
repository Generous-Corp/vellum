# Decision 0008: merge commits are load-bearing for provenance

- Date: 2026-07-25
- Status: merge commit is the only permitted way to land a pull request

Pull requests land on `main` as **merge commits**. Squash and rebase are not
permitted, and both are disabled in repository settings.

This is not a history-aesthetics preference. The observatory keys every
observation event to its `source_commit`, and `coverage_gaps` walks
`scan_base_commit..last_scanned_commit` requiring an event for each mapped
commit — plus the reverse, that every observation's commit falls inside the
scanned range. Squash and rebase both rewrite the feature commits, so those
commits stop existing in `main`'s history and:

1. `cursor.vellum.last_scanned_commit` names a commit no clone of `main` can
   resolve, failing `verify_active_cursor_ancestry`;
2. every event recorded for the pull request reports
   `observation-outside-reconciled-cursor`; and
3. `verify_event_against_git` cannot validate an event whose commit is gone.

The failure is not confined to `main`. Any branch cut from `main` inherits the
broken cursor, so `provenance-verify` fails there too — including on the very
pull request that would repair it. Recovering requires rewriting `main`.

Decision 0001 already rejected a squash copy of the prepared history "because it
loses authorship and path/commit correspondence." This decision records that the
same property is load-bearing for every ordinary pull request, not just the
seed.

## What enforces it

Three layers, because the first two only cover automation:

1. **Repository settings** — `allow_squash_merge` and `allow_rebase_merge` are
   `false`; `allow_merge_commit` is `true`. This is the only layer that stops a
   human choosing the wrong method in the web UI.
2. **`scripts/merge_on_green.py`** — the merge steward arms GitHub-native
   auto-merge with `mergeMethod: MERGE`; its pre-queue fallback, used only when
   GitHub refuses to arm an already-mergeable head, sends `merge_method: "merge"`.
   When a `merge_queue` ruleset protects `main`, the queue's own
   `merge_method: MERGE` lands the commit and direct merges are refused, so the
   ruleset becomes the enforcing layer for automation.
3. **`scripts/test_merge_on_green.py`** — asserts the steward requests `MERGE` /
   `merge` and rejects `squash` and `rebase` in both spellings, so the
   automation cannot regress silently.

## Merge queue

A GitHub merge queue is compatible with this decision as long as its merge
method is `MERGE`: the queue lands one two-parent merge commit per pull request
and keeps every feature commit reachable, exactly like the steward's merge.
Every required check therefore also runs on `merge_group` events (the queue's
`gh-readonly-queue/main/*` refs), and the provenance verifier evaluates the
queue's merge commit. The observatory evidence tail accepts that commit only
while the pull request already contains `main`'s tip (its tree equals the head's
tree), which is the same condition the steward's merge met; batching more than
one entry at a time waits for the post-merge cursor reconciliation recorded in
the Vellum-move decisions.

## Consequences

`main` carries one merge commit per pull request and retains each feature
commit. That is the intended shape: an authority record's commit must remain
reachable to stay auditable.

If a merge method is ever reconsidered, the observatory's coverage model has to
change first — the cursor and events would need to survive a commit rewrite.
Until then, treat the settings above as part of the provenance contract.

Rejected alternative: teaching the observatory to tolerate rewritten commits.
That would mean an event could no longer be verified against the history it
describes, which removes the guarantee the observatory exists to provide.
