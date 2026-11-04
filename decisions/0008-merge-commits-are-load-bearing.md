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

Two layers:

1. **Repository settings** — `allow_squash_merge` and `allow_rebase_merge` are
   `false`; `allow_merge_commit` is `true`. This is the only layer that stops a
   human choosing the wrong method in the web UI.
2. **The `main` merge queue ruleset** — its `merge_method: MERGE` lands the
   commit, and direct merges are refused. Admission is the Shipyard steward
   tick's job only; no workflow in this repository arms auto-merge or merges
   (the former `merge-on-green.yml` workflow and its script were removed
   because they armed without an exact-head approval).

## Merge queue

A GitHub merge queue is compatible with this decision as long as its merge
method is `MERGE`: the queue lands one two-parent merge commit per pull request
and keeps every feature commit reachable, exactly like any other merge.
Every required check therefore also runs on `merge_group` events (the queue's
`gh-readonly-queue/main/*` refs), and the provenance verifier evaluates the
queue's merge commit. The observatory evidence tail accepts that commit only
while the pull request already contains `main`'s tip (its tree equals the head's
tree), which is the condition every landing meets; batching more than
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

## Local macOS required checks

The required `gpu-macos-arm64` and `sterile-consumer` contexts also run for
`merge_group` events. When the Vellum local macOS selector is enabled, both
jobs use the repository variable `VELLUM_LOCAL_MACOS_RUNS_ON_JSON`; deleting
that variable restores the hosted `macos-15` fallback. Local execution also
depends on the runner group being restricted to this workflow, public-repository
access being explicitly enabled, and approval being required for outside
contributors. TartCI admits only runs whose `head_repository.full_name` is the
first-party Vellum repository, so a fork head is rejected at admission. A
merge-group run for a fork must therefore remain hosted or be maintainer
approved before it can use local capacity; the workflow `if:` guard alone is
not a security boundary. The `vm` lane has no cross-repository yield and can
occupy a free host slot until its job completes, so the evidence must include
concurrent Pulp lease observations.

## Exact-tree reuse of the GPU proof in the merge queue

When no other change lands on `main` between a pull request's proof and its
queue entry, the queue's merge commit carries exactly the tree the pull
request's merge ref did. In that case the `merge_group` run of the Locked
macOS GPU proof may satisfy its heavy build, render and test steps with an
exact-tree reuse receipt instead of rebuilding them. Both required jobs still
run and report; only steps are skipped, never a job, because a job skipped by
its own condition reports a passing required check.

`scripts/select_reusable_gpu_proof.py` permits reuse only when all of these
hold, and any doubt or error runs the full proof:

- the queue commit has two parents and the earlier run is a `pull_request`
  run of `gpu-macos.yml` on its second parent, from this repository, never a
  fork;
- both proof jobs concluded `success` (not `skipped`) in that run's latest
  attempt;
- that attempt uploaded a receipt, written only after every proof step
  passed, naming this exact git tree, run and attempt;
- that attempt started no more than six hours before the queue run. A pull
  request is normally approved and queued well within that window, and it
  bounds how far the reused result can drift from the world the queue sees.

The proof steps that read state outside the tree, the Pulp ancestry check in
`verify_extraction.py` and the attested view-audio archive fetch, run again in
the reused queue run. Pushes to `main` and release tags always run the full
proof, so every landed commit is still proven from scratch after it lands.
Reuse adds no trust beyond today's: a queue run already executes the pull
request's own workflow definition, so the reviewed exact head is what decides
both runs.
