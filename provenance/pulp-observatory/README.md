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
  --write --include-main-commits
```

This is the receiver's command. A full reconcile records main's own commits
and advances the cursor, so it refuses to run without
`--include-main-commits`. A branch never runs it; it uses `--events-only`,
described below.

The command writes new observation events first, then advances the cursor and
regenerates `reports/current.{json,md}`. A Vellum integration merge that adds nothing
beyond its two sides (`integration_merge_covered`: every observable path
carries exactly the entry of the side that changed it) gets no event of its
own. The sides' events cover it, and `verify` accepts it without one, so the
receiver no longer adds a pending observation that can only ever be
classified `superseded`. A merge with a mapped change of its own still gets an
event. Older events for covered merges stay valid. An interrupted run is idempotent: an
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

### Classifying your own events `framework-only`

Owner decision, 2026-11-10: an agent may classify the observation events of its
own pull request `framework-only` when the work is Vellum-native and has no
Pulp consumer. The limits:

- Only events for commits of the agent's own pull request.
- Only `framework-only`.
- Never for paths under `framework/` or `mirror/`, for a schema or API change,
  or for security or P0 work.
- The rationale shows a search of Pulp for the touched identifiers that returns
  0, and a control search on the same instrument that returns more than 0.
- The owner is the agent's own handle or session, never `@danielraffel`.
- Any later resolution overrides it.

## Pull requests: record events, leave the cursor

A pull request does not advance `cursor.json` or regenerate
`reports/current.*`. It records the observation events for its own mapped
commits and nothing else.

Only the receiver commits the report. Under the coverage policy (pull
requests, merge-queue builds and `main`), `verify` refuses a change that edits
`reports/current.{json,md}`. The exception is the receiver's reconciliation,
which it recognises by content: only observatory evidence, with the cursor
moved. `verify` then compares the committed report with the ledger as it stood
at the commit that last wrote the report, leaving out every event added since.
A pull request that classifies observations by adding resolutions therefore
leaves the report alone, and two such pull requests merge in either order
without conflict. Health and budgets are still judged on the live ledger at
the current time. A tag keeps the exact `reconciled` comparison, so it fails
on a report that lags its ledger.

A change to what the report is computed from still leaves the report alone,
although a fresh regenerate no longer matches it (a budgets change moves every
deadline, for example). Under the coverage policy `verify` accepts that
mismatch only when the report and `cursor.json` are byte-identical to the
copies their last writer committed, and the change accounts for the
difference:

- For `product/budgets.yaml`, the lock and the trust policy, the report is
  rebuilt with the writer's copies and must then match exactly.
- The `observatory*.py` generator, the event schema and the path map cannot be
  rebuilt that way, so a change to one of them since the writer is accepted,
  with or without a rebuilt input beside it. Until the receiver regenerates
  the report, that also holds for every later change, because the comparison
  is with the report's writer, not with the change's base.

Without such a change the comparison stays exact. The receiver writes the
regenerated report after the change lands, and its reconciliation and every
tag are judged exactly.

To record the branch's own observations:

```sh
python3 tools/provenance/observatory.py reconcile \
  --pulp-repo /path/to/pulp \
  --vellum-repo . \
  --pulp-target <committed-pulp-cursor-sha> \
  --vellum-target <exact-pr-head-sha> \
  --now <utc-timestamp> \
  --write --events-only
```

Run `git fetch origin main` first. By default the command leaves every commit
already on `origin/main` to main's own receiver, so a branch that has merged
main records only its own commits. If `origin/main` cannot be resolved, it
fails rather than record every commit in range. `--vellum-main <sha>` names
main explicitly. `--include-main-commits` records main's commits as well; only
the receiver has a reason to pass it. `verify` enforces
this: a change may add Vellum observations only for commits in
`git_base..HEAD`. The one exception is the receiver's own reconciliation,
recognised by its content: the change touches only observatory evidence (the
receiver's allowlist) and moves the committed Vellum cursor forward along
main's first-parent line, from one main tip to a later one, over the
observed commit.

**Operating rule: every main landing carries its own events.** A pull request
can no longer heal main's coverage by recording observations for commits
already on main. Each pull request records its own mapped commits before it
lands, and main's merge commits are covered by those events. A main landing without its events leaves every pull request failing coverage until the receiver coalesces. `verify` also
needs `--git-base` to tell a change's own events apart; `--no-git-base` is for
local checks only, and CI always passes a base.

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
health. The report's `release_blockers` list names only overdue security
events and events explicitly marked as a shared-contract release blocker; any
health failure, an overdue event of any class included, still fails
`provenance-verify` on the commit it judges.

`verify` judges health at a point in time. The committed report is rebuilt at
the cursor's `reconciled_at`, the time it describes, to prove it is not stale;
the health budgets are judged at `--now`, which defaults to the current time,
so an event that fell overdue after the last reconcile fails a pull request,
the merge queue and `main` alike. A tag's tree is frozen and cannot gain a
resolution, so `provenance-verify` judges a tag at its commit's committer time;
re-running a tag's job later gives the same answer. `--now` is for that and for
tests; nothing else should pass it.

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

Live dispatch is off unless the committed
`provenance/pulp-observatory/live-delivery.json` switch is exactly enabled by
the receiver's reviewed enable PR. Replay accepts an exact Pulp commit,
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
requests write for the evidence push, the evidence PR, the trusted enable
barrier and replay. A PR opened with an App
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
