# AGENTS.md

Guidance for coding agents working in this repository. The overview is in [README.md](README.md).

## How a PR lands here

- **Before every push,** run `scripts/gates.sh` (the fast default, about 5 minutes). It runs prose lint, product-quality, the UI packages, release-policy and the authority and observatory unit tests against your committed range, and prints each check's time. `scripts/gates.sh --full` adds the mirror-export proofs and the full observatory verification (25+ minutes).
- **Open the PR and stop.** Use `shipyard pr --base main --no-arm --no-steward-handoff`; it runs `scripts/gates.sh --full` in the background. Never arm, enqueue, auto-merge or merge; admission has exactly one writer.
- **Review:** a standing reviewer posts an exact-head APPROVE or a HOLD once required checks are green. Vellum counts the `pulp-review` and `infra-review2` signers. Never approve your own team's work.
- **Admission:** this repo uses GitHub's native merge queue. The m3 steward tick (every 5 minutes) enqueues approved green heads; the queue tests up to 3 groups at once and merges up to 5 pull requests together (ALLGREEN).
- **On a HOLD:** fix what it names, with a test. Merge `origin/main` (never rebase or force-push). Push once and comment `repaired: ... at <sha>`; the PR re-enters review on its own.
- **macOS first.** A Windows- or Linux-only failure gets a `PARKED/<platform>` issue with evidence and never blocks a macOS landing.
- **Labels the pipeline skips:** `superseded`, `owner-decision`, `PARKED/<platform>`.
- Launch agents with `sr claude` / `sr codex` and use the smallest model that fits (Haiku or Codex Luna for mechanical work, Sonnet or Codex Terra/Sol for fixes, Opus/Fable/Codex Astra for security, signing and release diffs).
- Prose in docs, comments and PR bodies carries no em dashes, no workflow breadcrumbs (phases, PR numbers as explanation, dated rulings) and no padding. `python3 -I scripts/prose_lint.py` checks the lines a branch adds; the `product-quality` check runs it.
- Steward log on m3: `~/Library/Logs/shipyard-steward-tick/summary.log`.
