# Decision 0014: an automated accident guard replaces code-owner review on release-path files

- Date: 2026-11-05
- Status: accepted. The owner chose option A of pulp-planning
  `architecture/2026-11-05-release-path-gate.md` ("go with A", 2026-11-05).

## Problem

`main` required code-owner review, and `.github/CODEOWNERS` gives the
release-signing policy and the code that enforces it to the owner. Every pull
request touching one of those files waited on a human. Every other pull
request lands on the standing reviewer's exact-head APPROVE through the
Shipyard steward. The owner reviews visual UX, not code, so that human review
was a queue, not a second opinion.

That review was also never a security boundary. The fleet App
(`shipyard-local`), whose key every fleet host holds, has administration on
the repository, so a compromised agent could lift the requirement, merge, and
put it back. The decision doc lists what a real boundary needs: release
credentials and publishing outside Vellum, and the fleet App narrowed. That is
option B, planned separately.

## Decision

**This is an accident guard, not a security boundary.** It catches an honest
agent's mistake on the release path. It does not stop anyone who holds the
fleet App's key or can run a workflow with the Actions token, and nothing
here says otherwise.

1. **Two automated reviews.** A pull request that changes a file CODEOWNERS
   names, or any file under `.github/workflows/` or `.github/actions/`, needs
   the `release-path-review` status
   (`.github/workflows/release-path-review.yml`, rule
   `scripts/release_path_review.py`). The status is `success` only when two
   counted reviewer APPROVEs name the pull request's exact head and no HOLD
   names that head. The two APPROVEs must be signed by two different
   `[auto_merge] reviewer_signers` (`pulp-review`, the standing reviewer, and
   `infra-review2`, the adversarial pass) in two different sessions matching
   `[auto_merge] reviewer_stamp_sessions`. An APPROVE of an older head never
   counts. A pull request that touches none of those files passes.
2. **The base branch judges.** The gate reads its rule, CODEOWNERS and the
   signer and session lists from the default branch, never from the pull
   request. `release-policy` runs the pull request's own
   `check_release_policy.py`, then the base branch's copy, with
   `--from-base`, on the same tree. That way a pull request cannot weaken the
   check that decides it. With `--from-base`, the tree may add policy files
   and release-policy lane entries. It may not disown a base policy file it
   still has, or drop or change a base lane entry whose script it still has.
3. **CODEOWNERS only routes review requests.** It stays the inventory of
   release-path files that `release-policy` holds equal to `POLICY_FILES`.
   That inventory now includes the gate's script and workflows and
   `.shipyard/config.toml`.
4. **The status is posted with the Actions token** and the ruleset binds it
   to GitHub Actions. No new App, key or environment is created.
5. **The rest is unchanged:** the `release` environment, the signed and
   immutable tag pipeline, and the other required checks.

## Owner steps

These are the two ruleset 24874039 edits, about 3 minutes, made only after
this lands:

1. Require the `release-path-review` status from GitHub Actions. A pull
   request already open then has no status until its next push, comment or
   review, so each open one needs a push or a comment to pick it up.
2. After one release-path pull request has gone green through it, turn off
   code-owner review.

**Undo:** turn code-owner review back on.

## Edge cases, stated

- **Every trigger runs the default branch's copy.** `pull_request_target`,
  `issue_comment` and `workflow_run` run the default branch's workflow, which
  checks out the default branch's script. A review event or a merge-queue
  build would run the pull request's own copy, so those events only start the
  permission-less `release-path-review-trigger.yml`, and its completion runs
  the gate. If a queued copy drops the trigger, nothing is posted and the
  queue ejects the entry.
- **The merge queue.** The status goes on the queue's commit. Every
  first-parent commit from it back to where it leaves `main` must be a
  two-parent queue merge. Each second parent is a queued pull request's head
  and is judged at that head. Any other shape, or a range over 1,000 commits,
  is an `error`, never a pass.
- **One head, several pull requests.** A status belongs to a commit, so it is
  `success` only when every open pull request with that head passes.
- **The base copy of release-policy runs from the pull request's workflow.**
  A pull request can edit `release-policy.yml` to skip it. That file is on the
  release path, so the edit needs both reviews, and the tree's own copy fails
  when the step is gone. Both of those catch mistakes, not attackers.
- **A deliberate loosening takes a detour.** From the base, a tree cannot
  disown a policy file it keeps, change a lane entry's arguments or drop it,
  or move a test out of the release-policy lane. A change that means to do
  one of those also edits the base-branch step of `release-policy.yml` (for
  example its `if:`) for that pull request. The edit is on the release path,
  so both reviews see it, and the next pull request is judged by the new base
  again. Growing the policy needs no detour: a tree may add policy files and
  import them, add lane entries, and make an entry `isolated`.
- **Transient API failures are retried.** The gate retries a 5xx, a timeout
  or a reset connection three times (2, 5 and 10 seconds) before it posts
  `error`. A queue batch makes a few dozen reads, and one bad read would
  otherwise eject the whole batch.
- **The signer is the posting agent.** A verdict counts only when its
  `reviewer:` line names the ghapp stamp's `agent:`, so one reviewer session
  cannot sign as the other.
- **Bootstrap.** The first pull request is judged by a base whose
  `check_release_policy.py` has no `--from-base`. The workflow detects that
  and skips the base run with a notice. From then on the base has it.
- **Accepted: independence is comment text.** Anyone holding the fleet App
  key can post a comment with a valid-looking stamp, a listed signer and a
  matching session. Any same-repository workflow can post the status. This is
  what option A accepts.
- **Accepted: a late HOLD.** A HOLD posted after the queue build's status
  turned `success` does not pull the entry out of the queue.
