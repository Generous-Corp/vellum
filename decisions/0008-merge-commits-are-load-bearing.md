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
the reused queue run. Release tags, release dry-run tags and manual dispatches
always run the full proof.
Reuse adds no trust beyond today's: a queue run already executes the pull
request's own workflow definition, so the reviewed exact head is what decides
both runs.

## Reusing the queue's proof on the push to main

The merge queue fast-forwards `main` to the commit its `merge_group` run
proved, so the push that follows carries the same commit and tree. Rebuilding
it proved nothing new and cost about as much as the queue run. The push run now
reports the queue's proof when `scripts/select_reusable_merge_group_proof.py`
finds a `merge_group` run that:

- is a run of `gpu-macos.yml` on exactly the pushed commit, on a
  `gh-readonly-queue/main/` ref, from this repository;
- succeeded, with both proof jobs `success` (not `skipped`) in its latest
  attempt, on that commit;
- built the proof or itself reported an identical-tree pull-request proof, or
  skipped it on the classifier's verdict under proven equivalence (below);
- started within the last twelve hours.

### The classifier may authorize a push skip only under proven equivalence

The classifier (`scripts/classify_gpu_proof.py`) judges pull requests and queue
entries, never a push. A push may report a queue run that skipped the proof on
the classifier's verdict only when the push proves it is that same
classification. The queue run uploads a receipt (`vellum-gpu-classifier-skip`)
and the push recomputes every fact from git and compares it to the receipt,
field by field; a missing, mistyped or different field runs the full proof:

- same inputs: the pushed commit's tree is the queue commit's tree, and the
  push's previous main tip is the base the queue run classified against, so the
  changed paths (compared by digest) are identical;
- same classifier: the script's blob at that base is the receipt's blob (the
  allowlist is part of the script, so there is no other configuration), and
  running that copy over the changed paths says `not-required` again;
- same requirements: the queue run's two required contexts both succeeded in
  the current attempt, and the pushed tree needs nothing a queue run does not
  produce. The only such item is the parity proof execution receipt, which
  release readiness binds to the push run: if the parity completion record is
  in the pushed tree the push always proves, for every kind of reuse;
- the queue run is a success of this workflow in this repository, as above.

The equivalence evidence is written to the push run's job summary. The owner
approved this narrower rule on 2026-11-15; before it, a push never reused a
classifier skip.

Any doubt, including an API error, runs the full proof. Both jobs still run
and report on the push, with only their steps skipped: `release-tag.yml` reads
the newest check run of each required context on the landed commit, and a job
skipped by its own condition would read there as unmet. The reused run's URL is
written to the job summary. Release tags, dry-run tags and dispatches never
reuse.

## Changes the GPU proof cannot see

A pull request or queue entry that changes only observatory evidence the GPU
proof never reads (`provenance/pulp-observatory/cursor.json`, `reports/*` and
`events/*.yaml`, which is what the receiver's coalesced evidence pull requests
change) skips the proof's steps. Both required jobs still run and report.
`scripts/classify_gpu_proof.py` decides. The workflow runs it as it exists in
the base, never the change's own copy, over the diff between the base and the
merged tree. For a pull request the base is its merge ref's first parent. For a
queue entry it is `merge_group.base_sha`, main's tip, so a batch is judged as a
whole rather than by its last entry. A change to the classifier or to any
workflow needs the proof by rule, as does an empty diff or a base without the
classifier. A trailing guard re-derives the verdict and fails the job if the
proof was skipped for anything else, or on any event other than a pull request
or queue entry. Pushes to `main` and release tags always run the full proof.

## Reporting a skipped proof without a Mac

When `gpu-macos-arm64` reports a reused proof or a change the proof cannot see,
every `sterile-consumer` step except one notice is skipped. The job then
selects a hosted `ubuntu-24.04` runner instead of the scarce local Mac class,
and still runs and reports as it did before. When the proof was built,
`sterile-consumer` selects exactly the runner `gpu-macos-arm64` does,
including the merge-group class on `merge_group` and push runs.

## Cancelling proofs of destroyed merge groups

A queue entry's merge group lives on
`refs/heads/gh-readonly-queue/main/pr-<n>-<sha>`. When GitHub rebuilds an
entry's group, the new group keeps `pr-<n>` with a new suffix and the old
group is destroyed. GitHub Actions offers no event for a destroyed group, since
`merge_group` has only the `checks_requested` activity type, so the old
group's proof would keep its place in the Mac queue. The hosted
`cancel-destroyed-groups` job runs on every `merge_group` run and cancels each
older unfinished `merge_group` run of `gpu-macos.yml` from this repository
whose ref names the same entry (`scripts/cancel_destroyed_queue_proofs.py`).
A pull request is the last entry of at most one live group, so an older run
for the same entry always belongs to a destroyed group. Neither proof job
needs the helper: a job skipped because a job it needs failed reports a
passing required check, so the helper must never be able to skip or satisfy a
proof. A helper error cancels nothing more and is reported as a warning.
