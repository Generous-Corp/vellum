# Decision 0014: two independent automated reviews replace code-owner review on release-path files

- Date: 2026-11-04
- Status: accepted (owner request, 2026-11-04: Vellum pull requests stop
  needing the owner's approval)

## Problem

`main` required code-owner review, and `.github/CODEOWNERS` gives the
release-signing policy and the code that enforces it to the owner. Every pull
request touching one of those files waited on a human, while every other pull
request lands on the standing reviewer's exact-head APPROVE through the
Shipyard steward. The owner reviews visual UX, not code, so that human review
was a queue, not a second opinion.

## Decision

1. The ruleset stops requiring code-owner review. The owner flips
   `require_code_owner_review` himself, and only after a second, adversarial
   reviewer pass signing `infra-review2` is live; until then no release-path
   pull request could collect two counted APPROVEs.
2. A pull request that changes a file CODEOWNERS names, or any file under
   `.github/workflows/` or `.github/actions/`, needs the required
   `release-path-review` status (`.github/workflows/release-path-review.yml`,
   rule `scripts/release_path_review.py`). It is `success` only when two
   counted reviewer APPROVEs name the pull request's exact head, signed by two
   different `[auto_merge] reviewer_signers` in two different reviewer sessions
   matching `[auto_merge] reviewer_stamp_sessions`, and no HOLD names that
   head. An APPROVE of an older head never counts; nothing carries forward. A
   pull request touching none of those files passes.
3. CODEOWNERS stays the source of truth for the protected files. The gate
   reads it, and the signer and session lists, from the default branch, never
   from the pull request. `release-policy` still requires CODEOWNERS and its
   own list to name the same files; the gate's workflows and script, and
   `.shipyard/config.toml` (the signer and session lists), are on both.
4. The status is posted with a dedicated GitHub App's token. Its key is a
   secret of the `release-path-review` environment, which only `main` may
   deploy to, and the ruleset binds the required context to that App. It is
   not the fleet App (`shipyard-local`): every fleet host holds that key, and
   it is also a repository secret any same-repository workflow can read.
5. The rest of the release protection is unchanged: the required
   `release-policy` check, the `release` environment that only `main` reaches,
   and the signed, immutable tag pipeline (`release-tag.yml`, the tag rulesets,
   `verify_release_tag.py`).

## Edge cases, stated

- **Who the second reviewer is.** `reviewer_signers` lists `pulp-review` and
  `infra-review2`. A release-path pull request needs both, from separate
  scheduled reviewer runs; one run signing both names does not count, because
  the sessions must differ.
- **Every trigger runs the default branch's copy.** `pull_request_target`,
  `issue_comment` and `workflow_run` all run the default branch's workflow,
  which checks out the default branch's script. A review and a merge-queue
  build would run the pull request's copy, so they only start the
  permission-less `release-path-review-trigger.yml`, whose completion runs the
  gate. A queued copy that drops the trigger posts nothing, and the queue
  ejects the entry.
- **The merge queue.** The status goes on the queue's commit. Every
  first-parent commit from it back to where it leaves `main` must be a
  two-parent queue merge; each second parent is a queued pull request's head
  and is judged at that head. Any other shape, or a range over 1,000 commits,
  is an `error`, never a pass.
- **One head, several pull requests.** A status belongs to a commit, so it is
  `success` only when every open pull request with that head passes.
- **Why workflows are protected.** A workflow a pull request adds runs with
  the repository's token and its repository secrets. Requiring two reviews of
  any workflow change keeps an unreviewed workflow from running alongside a
  release-path change, on top of the App binding.
- **Accepted residual: independence is comment text.** Any holder of the fleet
  App key can post a comment carrying a canonical-looking stamp, a listed
  signer and a matching session, so two APPROVEs are only as independent as
  the agents posting through `ghapp`. All reviewer sessions stamp from one
  host, so distinct stamp hosts cannot be required, and Vellum binds no
  `reviewer_signer_identities`. This is exactly the exposure of Shipyard's
  `review-verdict` rule, and no looser. Shipyard's `reviewer_claim_receipt`
  (a check run from a dedicated claim App) is the way to close it.
- **The environment must admit exactly `main`.** Its deployment-branch policy
  is one custom branch rule named `main`, never "protected branches" or a
  wildcard: a queued copy that adds a `merge_group` job runs on
  `gh-readonly-queue/main/*` and would otherwise reach the key.
- **Accepted residual: a late HOLD.** A HOLD posted after the queue build's
  status turned `success` does not pull the entry out of the queue; the gate
  judges the head when the queue builds it.
- **Accepted residual: deployment records.** The job runs in the
  `release-path-review` environment, so every pull-request and comment run
  records a deployment to it.
- **Follow-up: `review-verdict.yml`.** It posts with the Actions token, so a
  same-repository workflow could post its context. It is not required by the
  ruleset today; if it becomes required, it should post through the same
  environment-held App and be bound to it.
- **Reviews, not comments.** Only verdict comments count as APPROVEs, which is
  how the reviewers post. A changes-requested review on the head, from the
  fleet App or a trusted account, refuses it, and a review event re-runs the
  gate.
