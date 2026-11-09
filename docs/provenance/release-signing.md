# Release signing

A Vellum release tag is signed by CI, never by a person or an agent's host key.

## How a release happens

1. A release is a landed change to the project `VERSION` in `CMakeLists.txt` that is newer than every existing and
   retired `v*` tag. Nobody writes it by hand: after every landing on `main`, `version-at-land.yml` runs
   `scripts/version_at_land.py plan`, which counts the changes landed since the newest release tag:
   - only a change that touches a path that ships in the SDK counts (`SHIP_PATHS`, tests excluded); workflow,
     provenance, documentation and test changes release nothing on their own;
   - a `Version-Bump: vellum=<patch|minor|major>` trailer on any of its commits sets its level, and
     `Version-Bump: skip reason="..."` counts it as nothing; a `Version-Bump:` line it cannot read is skipped with a
     warning (release-policy names it on the pull request, as advice);
   - otherwise its conventional-commit titles decide: a breaking change (`type!:` or a `BREAKING CHANGE:` footer) is
     major, `feat` is minor, anything else that ships is patch;
   - while the major version is `0`, every level moves down one place (Cargo's 0.x rule): breaking is minor, and
     features and fixes are patch; an explicit `vellum=major` is minor too, so 1.0.0 is never proposed
     automatically.

   The next version is one level above the highest version any tag has used, existing or retired, so versions only
   move forward and a retired name is never reused. `scripts/version_at_land.py apply` rewrites every version
   surface (`VERSION_SURFACES`) and regenerates `scripts/INSTALLER_SHA256SUMS`, and the workflow opens the change as
   a pull request from `automation/version-bump` (or updates the one already open). The workflow has two jobs: `plan`
   runs the script with no write token and hands the bump on as a patch; `push` checks out the planned `main` commit,
   applies the patch with hooks off (refusing any path that is not a version surface), and only then mints the App
   token, for `git` and `gh` run with an empty environment. It is reviewed and lands through the merge queue like any
   other change. While `main` declares a version that is not tagged yet, the workflow
   proposes nothing: that release covers what lands meanwhile.

   Only that pull request can release. A version changed any other way is never tagged (see step 3); to release a
   higher level, put a `Version-Bump: vellum=<level>` trailer on a landed change and let version-at-land propose it.
2. The commit that lands the version is **not** tagged. A tag's `provenance.yml` run checks the Pulp observatory with
   `--vellum-tail-policy reconciled`: every commit after the cursor's `vellum.last_scanned_commit` must be
   observatory evidence. A commit's tree can never name that same commit as its cursor, so a version commit can never
   pass (this is why `v0.1.8` and `v0.1.9` never published; both are retired).
3. The observatory receiver reconciles the landing and lands its evidence commit through the queue. That push changes
   `provenance/pulp-observatory/cursor.json`, which starts `release-tag.yml`. Its first job,
   `select-release-commit`, holds no secret and no environment; `scripts/release_request.py select` decides whether
   the pushed commit is the one to tag. It is when:
   - the tag of the project version at the pushed commit does not exist yet, is not retired, and is newer than every
     existing and retired `v*` tag (otherwise the push is an ordinary reconcile and the job ends);
   - the commit that landed the version is the version-at-land bot's own bump, exactly
     (`scripts/version_at_land.py verify_bump`): the queue's merge of `automation/version-bump`, one commit authored
     and committed by `vellum-version-at-land` with its trailer, a diff equal byte for byte to the rewrite `apply`
     makes from the merge's first parent, and a version equal to what `plan` derives at that parent. A hand-set
     version, a bump with an extra hunk, a level `plan` would not choose, or a skipped number is refused. The
     signing job checks this again before `check`. The version is re-derived at the bump commit's own parent (the
     `main` it was planned from), so a change landed ahead of it in the queue does not make it unreleasable; when the
     merged tree no longer equals the rewrite, `plan` treats that number as used and proposes the next one, but only
     past a number one step above the newest tag, and only when that landing is the bot's own merge (a hand-set
     version stops `plan` until a person reverts it or retires it); the bump pull request states the number it
     passes over;
   - the version covers everything the release would ship: it is at least one required level above the highest
     existing or retired tag, where the level is the highest of every shipped change landed since the newest
     release tag through the pushed commit, including changes that landed after the bump
     (`version_at_land.coverage_problem`). A patch bump followed by a breaking change is refused, and
     version-at-land proposes the sufficient version past it;
   - the cursor at the pushed commit is, or descends from, the commit that landed the version: the newest commit on
     the pushed commit's first-parent history whose project `VERSION` differs from its first parent's, which on
     `main` is the merge queue's merge of the bump;
   - every commit after the cursor is observatory evidence, by the same rule the tag's provenance run applies: a
     linear chain that only modifies the cursor and the two reports and only adds event files, where a merge counts
     only when its tree equals the commit before it (the queue's merge of the receiver's commit).

   Any other push tags nothing: the bump merge itself, a reconcile whose cursor predates the bump, or a reconcile
   that landed behind other changes. A later reconcile then tags the release. The job's pre-tag checks
   (`scripts/pretag_checks.py`) then run the tag pipeline's content checks before any tag exists.
4. The `sign-release-tag` job runs only for a selected commit, in the `release` environment. It decides again and
   requires the same answer, then `scripts/release_request.py check` refuses unless all of these hold for the project
   version at that commit:
   - the version is not retired (`provenance/retired-release-tags.json`);
   - the tag does not exist yet (only git's "no such ref" answer counts as absent; a failed lookup refuses);
   - the version is newer than every existing and retired `v*` tag;
   - the live `GET /repos/Generous-Corp/vellum/immutable-releases` answer, read with the release App token, says
     immutable releases are enabled (`scripts/verify_immutable_release_preflight.py` validates it);
   - every status check the `main` rules require concluded `success` on the selected commit.

   The push that lands the reconcile starts those checks on the same commit, so the job first waits, up to 100
   minutes, until none of them is still running (`release_request.py pending`).
5. The job signs an annotated tag on that commit with the key in the environment's `RELEASE_TAG_SIGNING_KEY` secret.
   It verifies the tag with `scripts/verify_release_tag.py` against `provenance/release-signers.allowed_signers`
   before pushing anything.
6. It then mints a fresh release App token (the wait can outlast the first token's hour), pushes the tag and creates
   the tag's freeze ruleset (`release_request.py freeze`), which `sdk-release.yml` requires before it finalizes the
   release. The freeze is retried; if every attempt fails, the job prints the exact ruleset for the owner to apply.
   The tag push starts `gpu-macos.yml`, then `sdk-release.yml`, which reads the signer file from the tagged commit,
   not from `main`, and reads the immutable-release setting live again before it publishes.

The cursor file is evidence the receiver writes, not a policy file: it only decides *when* a release is tagged.
*What* is tagged is a commit whose content equals the cursor's commit apart from evidence, and that commit has
passed main's required checks.

Nobody approves a routine release by hand. Authorization is the bot's exact, re-derived version bump, landed through
the queue, plus the required checks on the tagged commit. The version line is parsed in one place
(`version_at_land.project_version`), which reads CMake with comments removed and refuses a second declaration.

## Release dry run

A release cannot be undone, so the whole path is rehearsed on GitHub first. A dry run starts by itself when an observatory
reconcile on `main` covers a change to a release-path file (`release_dry_run.RELEASE_PATHS`), so the commit it
tags contains the change; every Monday as a canary; and optionally by hand (Actions > Sign the release tag > Run
workflow). `dry-run-gate` decides, and refuses while a release tag's GPU proof or provenance run, or any finalizer
run, is not finished. Push runs keep one concurrency group, so a release's select and sign stay together;
scheduled and manual dry runs queue in their own group and never displace a pending release run. The reconciled range
is compared tree to tree, so a release file changed and changed back within it does not start a dry run.

1. `sign-dry-run-tag`, in the `release` environment, picks the newest observatory-reconciled commit on `main`
   (`scripts/release_dry_run.py plan`) and signs `dry-run/v<version>/<run>-<attempt>` on it with the release key. The
   tag message is the promotion attestation of `v<version>`, the release it rehearses. No freeze is created.
2. `provenance.yml` and `gpu-macos.yml` run on it as on a release tag, judging release readiness as `v<version>`.
   The tag verifier accepts the `dry-run/` namespace only for exactly that name shape, against the release key.
3. `sdk-release.yml` validates the artifacts with the sterile consumer, builds the draft with every release asset,
   verifies it as a release would be, and stops before the only call that publishes. A published release on a
   dry-run tag is refused.
4. `clean-dry-run` deletes the draft and the tag whatever happened before, acting only on a well-formed `dry-run/`
   tag and only on draft releases. The release cadence check reports a dry-run tag older than six hours, and any
   published release on one.

What a dry run cannot show: the immutable-release attestation GitHub writes after publication.



Two workflows make a missing release loud instead of silent:

- `release-watchdog.yml` opens one issue per workflow (`Release pipeline failed: <name>`) when `version-at-land.yml`
  or `release-tag.yml` fails on `main`, or when the GPU proof, provenance or SDK finalizer fails for a release tag,
  and closes it on the next successful run.
- `release-cadence-check.yml` runs every hour and judges the outcome (`scripts/release_cadence.py`): a version on
  `main` with no tag six hours after it landed, a newest unretired tag with no published release six hours after it
  was tagged, a version on `main` that is retired, or one older than the newest tag. It keeps one issue
  (`Release cadence: a Vellum version is not released`) open until a run finds nothing.

A pushed tag is frozen. When its own pipeline cannot pass, retire it in `provenance/retired-release-tags.json` (an
owner-reviewed change); the next shipped change then bumps past it.

## What the tag message attests

The signed tag message is a promotion attestation that `release_request.py attestation` builds and checks with the
verifier's own validators before anything is signed. Its `attested_by` is `release-pipeline:Generous-Corp/vellum`.
`provenance.yml` reads it from the tag, and `tools/provenance/verify_authority_expansion.py --release-readiness`
accepts exactly one of two closed shapes:

- **Authority promotion** (`vellum-authority-promotion-attestation`). It binds:
  - the promoted commit and tree;
  - the exact-boundary acknowledgement;
  - `parity-completion-1.json` at the release commit.

  The release must also show the required parity check runs and their `vellum-parity-proof-execution` receipts.
- **Mirror promotion** (`vellum-mirror-promotion-attestation`). Only `v0.1.10`, the Phase 4 promotion release
  (owner decision 2026-10-31, third sitting; `v0.1.9` was retired unpublished, owner decision 2026-11-02), and
  `v0.1.11`, the release carrying everything since 2026-10-25 under the same parity waiver (owner decision
  2026-11-01), may carry it. It binds:
  - the promoted commit and tree;
  - the exact-boundary acknowledgement;
  - the newest export receipt in `provenance/mirror/receipts/index.json` at the release commit, by SHA-256.

  It states `"parity": "not-proven-deferred-to-matrix-refreeze"`. Parity completion, parity check runs and proof
  receipts are not required. Every other release-readiness check still applies.

  The verifier refuses this shape in three cases:
  - for any other tag;
  - when a parity completion record exists at the release commit;
  - when the receipt is not the newest one in the index.

The parity re-freeze to the mirror is the next milestone. Every release after `v0.1.11` must carry the authority
promotion attestation and prove full-matrix parity.

## Who can do what

- **The key** exists only as a secret of the `release` environment, whose deployment policy allows only `main`. Pull
  request and branch workflows cannot read it, and no fleet host holds it.
- **Only `release-tag.yml` may use the environment.** The required `release-policy` check
  (`scripts/check_release_policy.py`) fails when any other workflow names it.
- **The signing policy needs two automated reviews.** `.github/CODEOWNERS` lists these files. A pull request that
  changes one, or any workflow, local action or file under `tools/mirror/`, needs the `release-path-review` status: two counted reviewer APPROVEs
  of its exact head, from `pulp-review` and the adversarial pass `infra-review2`, with no HOLD
  ([Decision 0014](../../decisions/0014-release-path-accident-guard.md)). This is an accident guard, not a security
  boundary: anyone holding the fleet App's key can post the verdicts it counts. Code-owner review stays on until the
  owner turns it off, after the status has gated one release-path pull request. The list covers:
  - the signer files: release, authority and renderer;
  - the release workflows: `release-tag.yml`, `release-policy.yml` and `version-at-land.yml`, which holds the App
    token that pushes the bump branch;
  - the release scripts;
  - the verifiers, including the immutable-release verifier;
  - the committed record the release decision trusts: the retired tags;
  - the release-path review's rule, its two workflows, and `.shipyard/config.toml`, which names the reviewer signers
    and sessions it counts;
  - the framework transfer's judges, `tools/provenance/verify_extraction.py` and
    `tools/provenance/framework_trusted_verify.py`, the modules and rule tables the judge loads
    (`content_scan_policy.py`, `verify_mirror_export.py`, `mirror_edge_patterns.json` and its pin, all under
    `tools/provenance/`, plus `provenance/mirror/link-policy.json`), the mirror tooling under `tools/mirror/` they run, and the records
    they hold the transfer to: `provenance/framework/transfer.json`, `provenance/framework/external-updates.json` and
    `provenance/framework-name-debt.json`, owned before they exist so the change that adds them needs both reviews;
  - CODEOWNERS itself.
  `release-policy` keeps that list complete: CODEOWNERS and the check's own list must name the same files, and a
  policy script may import only modules that are themselves on the list. That includes a module it
  loads by file path (`importlib`'s `spec_from_file_location`) rather than with an `import` statement; one under
  `tools/mirror/` or a workflow directory is accepted because the review already protects that whole prefix. It then runs the base branch's copy of the
  check on the pull request's tree (`--from-base`), so a pull request cannot weaken the check that judges it.
- **Checked-out code never runs next to a secret.** In `release-tag.yml` and `version-at-land.yml`, each secret
  lives in a step that runs no repository Python:
  - signing runs only `git tag`;
  - verifying runs without the key;
  - the steps that hold an App token only call `gh` and `git`; every minted token counts, whatever its step id.
  Every release script runs as `python3 -I`, so no file in the checkout can shadow a standard-library module.
  `release-policy` enforces all of this, and it also refuses a repository Python file named like a
  standard-library module.
- **One key per tag namespace.** `v*` release tags are verified against `release-signers.allowed_signers` (the
  pipeline's key); `authority/*` tags against `authority-signers.allowed_signers` (the owner key that signed them).
  `verify_release_tag.py` picks the file from the tag name itself, refuses abbreviated or repeated flags, so a
  caller cannot verify a release tag against the other key. Tags `v0.1.0`–`v0.1.8` were signed by the owner key,
  so re-running a release workflow on one of them now fails verification by design. The renderer has no signer file: skia-builder's build provenance vouches for it (`renderer-release-attestation.md`).
- **Every verifier call pins its file's key.** `release-policy` reads each `verify_release_tag.py` call and fails
  when it names another signer file, pins a principal or fingerprint other than that file's (in either `--flag value`
  or `--flag=value` form), pins through a variable outside `release-tag.yml`, or when a workflow that verifies tags
  has fewer calls than it must.
- **No reusable workflow from another repository.** A called workflow can name the `release` environment where this
  check cannot read it, so `release-policy` refuses any job that `uses:` a workflow outside this repository.
- **Only the release App can create a release tag.** `create-release-tags` refuses `v*` tag creation to everyone but
  integration 3878000 ("Shipyard Local"), the App `release-tag.yml` mints from `RELEASE_APP_ID`. The repository secret
  `SHIPYARD_APP_PRIVATE_KEY`, which the observatory receiver and version-at-land use, mints tokens for the same App, so
  a workflow holding it could create a `v*` tag. It cannot sign one, but a stray tag would burn that version name. The
  fix is the owner's: a separate App for release tags, or that key in a `main`-only environment.

Owner authorization (owner decisions in the planning repository), release signing (this key) and ordinary commit
signing (each host's own key) are three separate keys.

## Rehearse before you change the release path

A release cannot be undone: the tag, its immutable release and its frozen inputs are final, so a downstream failure
loses the version. Before changing anything on the release path, or asking for a release, replay the tag's own
pipeline against the exact commit using the checked-in entry points from `.github/workflows/release-tag.yml`
(`scripts/release_request.py` for `select`, `pending` and `check`, plus
`scripts/verify_immutable_release_preflight.py`) and `.github/workflows/sdk-release.yml`
(`scripts/verify_release_assets.py`). Dispatch the checked-in `.github/workflows/provenance.yml` and
`.github/workflows/gpu-macos.yml` at that ref, and let the successful tag run invoke `.github/workflows/sdk-release.yml`,
using the real artifacts where they exist. Include tags that must fail (`v0.1.8`, `v0.1.9`) and one that published
(`v0.1.6`), so the replay is shown able to fail.

When you verify the repository's rules, read them back (`GET /repos/Generous-Corp/vellum/rulesets/<id>`); GitHub
stores rules differently than they were posted (an `update` rule comes back without its parameters), so a POST
payload is not evidence. `gh attestation verify --bundle` accepts only files named `*.json` or `*.jsonl`.
