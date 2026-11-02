# Release signing

A Vellum release tag is signed by CI, never by a person or an agent's host key.

## How a release happens

1. Open a pull request that bumps the project `VERSION` in `CMakeLists.txt` and sets
   `provenance/release-request.json`:

   ```json
   {"schema": "vellum.release-request.v1", "version": "0.1.10"}
   ```

   It is reviewed and lands through the merge queue like any other change.
2. The commit that lands the request is **not** tagged. A tag's `provenance.yml` run checks the Pulp observatory with
   `--vellum-tail-policy reconciled`: every commit after the cursor's `vellum.last_scanned_commit` must be
   observatory evidence. A commit's tree can never name that same commit as its cursor, so a request commit can never
   pass (this is why `v0.1.8` and `v0.1.9` never published; both are retired).
3. The observatory receiver reconciles the landing and lands its evidence commit through the queue. That push changes
   `provenance/pulp-observatory/cursor.json`, which starts `release-tag.yml`. Its first job,
   `select-release-commit`, holds no secret and no environment; `scripts/release_request.py select` decides whether
   the pushed commit is the one to tag. It is when:
   - the requested tag does not exist yet (otherwise the push is an ordinary reconcile and the job ends);
   - the cursor at the pushed commit is, or descends from, the commit that landed the request: the newest commit on
     the pushed commit's first-parent history whose `release-request.json` differs from its first parent's, which on
     `main` is the merge queue's merge of the request;
   - every commit after the cursor is observatory evidence, by the same rule the tag's provenance run applies: a
     linear chain that only modifies the cursor and the two reports and only adds event files, where a merge counts
     only when its tree equals the commit before it (the queue's merge of the receiver's commit).

   Any other push tags nothing: the request merge itself, a reconcile whose cursor predates the request, or a
   reconcile that landed behind other changes. A later reconcile then tags the release.
4. The `sign-release-tag` job runs only for a selected commit, in the `release` environment. It decides again and
   requires the same answer, then `scripts/release_request.py check` refuses unless all of these hold:
   - the version matches the project version;
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

Nobody approves a routine release by hand. Authorization is the reviewed pull request plus the required checks.

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
- **The signing policy is owner-reviewed.** `.github/CODEOWNERS` gives these files to the owner, and `main` requires
  code-owner review:
  - the signer files: release, authority and renderer;
  - the two release workflows;
  - the release scripts;
  - the verifiers, including the immutable-release verifier;
  - the committed records the release decision trusts: the release request and the retired tags;
  - CODEOWNERS itself.
  `release-policy` keeps that list complete: CODEOWNERS and the check's own list must name the same files, and a
  policy script may import only modules that are themselves on the list.
- **Checked-out code never runs next to a secret.** Each secret lives in a step that runs no repository Python:
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
  so re-running a release workflow on one of them now fails verification by design. The renderer attestation has its own `renderer-signers.allowed_signers`, pinned in its verifier.
- **Every verifier call pins its file's key.** `release-policy` reads each `verify_release_tag.py` call and fails
  when it names another signer file, pins a principal or fingerprint other than that file's (in either `--flag value`
  or `--flag=value` form), pins through a variable outside `release-tag.yml`, or when a workflow that verifies tags
  has fewer calls than it must.
- **No reusable workflow from another repository.** A called workflow can name the `release` environment where this
  check cannot read it, so `release-policy` refuses any job that `uses:` a workflow outside this repository.
- **Only the release App can create a release tag.** This needs a `creation` rule on `protect-release-tags` with the
  release App as its only bypass actor. Until the owner adds it, anyone with write access can create a `v*` tag, and a
  stray tag would block that version forever.

Owner authorization (owner decisions in the planning repository), release signing (this key) and ordinary commit
signing (each host's own key) are three separate keys.
