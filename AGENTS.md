# AGENTS.md

Guidance for coding agents working in this repository. The overview is in [README.md](README.md).

## How a PR lands here

- **Open the PR and stop.** Use `ghapp pr create -R Generous-Corp/vellum --base main`. Never arm, enqueue, auto-merge or merge; admission has exactly one writer.
- **Review:** a standing reviewer posts an exact-head APPROVE or a HOLD once required checks are green. Vellum counts the `pulp-review` and `infra-review2` signers. Never approve your own team's work.
- **Admission:** this repo uses GitHub's native merge queue. The m3 steward tick (every 5 minutes) enqueues approved green heads, and the queue batches up to 5.
- **On a HOLD:** fix what it names, with a test. Merge `origin/main` (never rebase or force-push). Push once and comment `repaired: ... at <sha>`; the PR re-enters review on its own.
- **macOS first.** A Windows- or Linux-only failure gets a `PARKED/<platform>` issue with evidence and never blocks a macOS landing.
- **Labels the pipeline skips:** `superseded`, `owner-decision`, `PARKED/<platform>`.
- Launch agents with `sr claude` / `sr codex` and use the smallest model that fits (Haiku or Codex Luna for mechanical work, Sonnet or Codex Terra/Sol for fixes, Opus/Fable/Codex Astra for security, signing and release diffs).
- Steward log on m3: `~/Library/Logs/shipyard-steward-tick/summary.log`.
