# Release signing

A Vellum release tag is signed by CI, never by a person or an agent's host key.

## How a release happens

1. Open a pull request that bumps the project `VERSION` in `CMakeLists.txt` and sets
   `provenance/release-request.json`:

   ```json
   {"schema": "vellum.release-request.v1", "version": "0.1.9"}
   ```

   It is reviewed and lands through the merge queue like any other change.
2. When it lands, `release-tag.yml` runs on `main` in the `release` environment.
   `scripts/release_request.py check` refuses the request unless all of these hold:
   - the version matches the project version;
   - the version is not retired (`provenance/retired-release-tags.json`);
   - the tag does not exist yet;
   - every status check the `main` rules require concluded `success` on the commit that landed the request.
3. The job signs an annotated tag on that commit with the key in the environment's `RELEASE_TAG_SIGNING_KEY` secret.
   It verifies the tag with `scripts/verify_release_tag.py` against `provenance/release-signers.allowed_signers`.
4. It then pushes the tag with the release App's token and creates the tag's freeze ruleset
   (`release_request.py freeze`), which `sdk-release.yml` requires before it finalizes the release. The tag push starts
   `gpu-macos.yml`, then `sdk-release.yml`.

Nobody approves a routine release by hand. Authorization is the reviewed pull request plus the required checks.

## Who can do what

- **The key** exists only as a secret of the `release` environment, whose deployment policy allows only `main`. Pull
  request and branch workflows cannot read it, and no fleet host holds it.
- **Only `release-tag.yml` may use the environment.** The required `release-policy` check
  (`scripts/check_release_policy.py`) fails when any other workflow names it.
- **The signing policy is owner-reviewed.** `.github/CODEOWNERS` gives these files to the owner, and `main` requires
  code-owner review:
  - the allowed-signers file;
  - the two release workflows;
  - the release scripts;
  - the verifier;
  - CODEOWNERS itself.
  `release-policy` keeps that list complete.
- **Checked-out code never runs next to a secret.** Each secret lives in a step that runs no repository Python:
  - signing runs only `git tag`;
  - verifying runs without the key;
  - the freeze ruleset is computed before the App token is minted.
  Every release script runs as `python3 -I`, so no file in the checkout can shadow a standard-library module.
  `release-policy` enforces all of this, and it also refuses a repository Python file named like a
  standard-library module.
- **Every verifier pins the same key.** `release-policy` fails when an `--expected-principal` or
  `--expected-fingerprint` pin in any workflow differs from the allowed-signers file.
- **Only the release App can create a release tag.** It is the only bypass actor on `protect-release-tags`. Its key
  also lives only in the `release` environment.

Owner authorization (owner decisions in the planning repository), release signing (this key) and ordinary commit
signing (each host's own key) are three separate keys.
