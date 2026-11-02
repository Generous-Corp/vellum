# Renderer release attestation

Vellum links a prebuilt Skia/Dawn archive published by
[skia-builder](https://github.com/danielraffel/skia-builder). The archive is
byte locked in
[`provenance/third-party-lock.json`](../../provenance/third-party-lock.json),
and it is release eligible only on skia-builder's own published records for
that exact archive. No owner key is involved: the build-provenance
attestation is what vouches for the bytes.

[`provenance/renderer-attestation.json`](../../provenance/renderer-attestation.json)
names those records. Its `state` is `pending-build-provenance` while the
locked archive has no such records (the lock then stays `blocked`), and
`builder-attested` once it names them.

## What makes the renderer eligible

For the archive the lock pins, all three of skia-builder's records must
verify:

1. **Build provenance.** The `<archive>.provenance-attestation.sha256-<digest>.sigstore.json`
   asset is a Sigstore bundle. `gh attestation verify --bundle` (real Sigstore:
   certificate chain, Rekor inclusion, signature) must accept it for the
   archive, the legal manifest and the toolchain receipt, each pinned to:
   - repository `danielraffel/skia-builder`;
   - signer workflow `danielraffel/skia-builder/.github/workflows/build-skia.yml@refs/heads/main`
     and source ref `refs/heads/main`;
   - source commit equal to the lock's builder commit (`--source-digest`);
   - predicate type `https://slsa.dev/provenance/v1`;
   - OIDC issuer `https://token.actions.githubusercontent.com`;
   - a GitHub-hosted runner (`--deny-self-hosted-runners`).

   The verifier then re-checks what Sigstore verified: exactly one
   attestation per subject; every certificate field above plus the run
   invocation the record names (`build_provenance.run`); and that the
   statement Sigstore verified is the bundle's own. That statement must name
   the archive, manifest and receipt by name and digest, and must say the build
   was `build-skia.yml` on `refs/heads/main` of the builder repository, at the
   builder commit, on a GitHub-hosted runner, in the named run. None of these
   values is read from the record: the verifier pins them, and the record
   must repeat them.
2. **Legal manifest.** The `<archive>.legal-manifest.sha256-<digest>.json`
   asset must have its attested digest, describe the locked archive, record
   the lock's Skia and Dawn revisions as the ones built, claim an exhaustive
   inventory with no gaps, and carry every license text with a matching
   digest. Every zip member is re-derived from the archive and must be listed
   with its digest; every static archive must be inventoried (exactly the
   lock's archives); and every member and symbol table of every slice is
   re-derived from the archive bytes and must be listed, each object with at
   least one license from the manifest.
3. **Toolchain receipt.** The `<archive>.apple-toolchain.sha256-<digest>.json`
   asset must have its attested digest and describe the locked archive. It
   supplies the build identity: the record's compiler and linker (version,
   path, digest), SDK and C++ ABI must equal what it measured, every object
   must have been built against that SDK, it must have built the locked
   target and configuration, and its SDK and minimum OS must equal the lock's
   observed Mach-O tuple.

GitHub's own release-asset digests (`--release-json`) must also equal the
archive's and all three records'.

## Updating the record

Point the lock at the new archive first (digests, source identities, header
and archive inventory, observed Mach-O tuple). Then download the archive and
its three records from the locked release, and the release JSON, and let the
verifier derive and prove the record:

```sh
python3 tools/provenance/verify_renderer_dependency.py \
  --archive skia-build-mac-universal-gpu-release.zip \
  --legal-manifest skia-build-mac-universal-gpu-release.legal-manifest.sha256-<d>.json \
  --toolchain-receipt skia-build-mac-universal-gpu-release.apple-toolchain.sha256-<d>.json \
  --provenance-bundle skia-build-mac-universal-gpu-release.provenance-attestation.sha256-<d>.sigstore.json \
  --release-json release.json \
  --emit-attestation provenance/renderer-attestation.json
```

`--emit-attestation` writes the record only after every proof above passes.
Then set the lock's `release_eligibility` to `eligible`, its status to
`byte-locked-renderer-release-attested`, clear `release_blockers`, and record
`bundled_components_completeness.claim: exhaustive` with the manifest's digest.

## Where it is checked

- `provenance.yml` runs the contract tests, including the real Sigstore tests
  against the published chrome/m158 bundle (`VELLUM_REQUIRE_REAL_SIGSTORE=1`
  makes them mandatory), and, when the lock is eligible, downloads the
  builder's records and runs `--require-release-eligible` on every commit.
- `sdk-release.yml` runs `--require-release-eligible` against the lock and
  record at the exact release source commit before publishing.
- `--require-release-eligible` fails unless the lock declares the renderer
  eligible and `--archive`, `--legal-manifest`, `--toolchain-receipt`,
  `--provenance-bundle` and `--release-json` are all given and all verify.
  Without it, the verifier checks the lock and record and any records given,
  but never reports the renderer eligible.
- Every workflow runs the verifier with `python3 -I`, and the verifier refuses
  to run as a script without `-I`/`-P`: otherwise a module or package beside
  it named like a standard-library module (`tools/provenance/subprocess/`)
  would run in place of that module and could fake `gh`'s answer.
  `check_release_policy.py` fails an unisolated verifier call in any workflow,
  and any module, package or compiled module named like a standard-library
  module in `.`, `scripts/` or `tools/provenance/`.
- The workflows build the asset URLs from the pinned builder release and the
  record's asset names (which must be plain file names), so the runner never
  fetches a URL the record chose.
- The release gate (sdk-release.yml) runs on Linux without `--require-macho`;
  the Mach-O SDK and minimum OS there come from the attested toolchain
  receipt, cross-checked against the lock's observed tuple. gpu-macos checks
  the Mach-O tuple directly.
- The per-commit proof in provenance.yml is a release-readiness signal, not a
  merge gate unless that check is required by the repository's ruleset; the
  hard gate is the release step.
- The verifier pins the builder, so it is a release-policy file: CODEOWNERS
  gives it to the owner and `scripts/check_release_policy.py` keeps the two
  lists one inventory.
- `verify_extraction.py` requires every landed mirror receipt's Pulp commit
  to pin this same renderer (branch, Skia, Dawn and builder commits, archive
  URL and digest).

Pulp's render-toolchain pin must consume this same artifact/source tuple. No
second provider or independent Skia/Dawn pin is introduced under D4.
