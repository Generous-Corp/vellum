# Renderer release attestation plan

The `chrome/m157` Skia/Dawn archive is byte locked in
[`provenance/third-party-lock.json`](../../provenance/third-party-lock.json),
but it is not release eligible until the builder produces one signed record
that binds the archive digest to its complete build inputs.

[`provenance/renderer-attestation.json`](../../provenance/renderer-attestation.json)
is the checked-in contract for that record. It currently has
`state: pending-owner-attestation`, `signature.state: missing`, and
`legal_inventory.state: observed-not-exhaustive`; those states deliberately
keep release eligibility blocked.

## Required producer record

The release-producing builder must replace the pending values without changing
the locked artifact or source tuple:

1. `artifact.sha256` must equal the archive digest and the lock entry.
2. `source_identities` must name the Skia, Dawn, and builder repositories and
   exact commits used for the archive.
3. `build_identity` must record the target, configuration, compiler identity and
   digest, linker identity and digest, and C++ standard-library ABI. No field
   may still read `pending` or `unknown`.
4. `legal_inventory` must be `exhaustive`, with a content-addressed manifest
   covering every transitive source, license, notice, and archive member.
   `legal_inventory.manifest` names the release asset: its `name` must be
   `<archive>.legal-manifest.sha256-<digest>.json`, its `sha256` must be that
   digest (and equal `manifest_sha256`), and its `url` must be the asset on the
   locked release.
5. `signature` must be `signed`, with the owner-approved issuer, the artifact
   digest as `subject`, the `vellum-renderer-attestation` namespace, and the
   digests of the signature file and of the signed payload.

## Signing (owner only)

The signed payload is the canonical record (sorted-key JSON) with the
signature block reduced to `issuer`, `subject` and `namespace`, prefixed by a
fixed domain line. The verifier prints exactly those bytes:

```sh
python3 tools/provenance/verify_renderer_dependency.py --print-signing-payload \
  > renderer-attestation.payload
ssh-keygen -Y sign -f <owner key> -n vellum-renderer-attestation renderer-attestation.payload
mv renderer-attestation.payload.sig provenance/renderer-attestation.json.sig
```

Then record `signature.state: signed`, `signature_sha256` (of the `.sig` file)
and `payload_sha256` (of the payload) in the record. Changing any signed field
afterwards invalidates the signature.

## Verification

`tools/provenance/verify_renderer_dependency.py` never trusts a state string:

- A record that says `signed` is verified with `ssh-keygen -Y verify` against
  `provenance/renderer-signers.allowed_signers`. That file must name exactly
  the owner principal, and its key fingerprint must equal the one pinned in
  the verifier, so editing the file alone cannot admit another issuer.
- A record that says `exhaustive` must name a content-addressed manifest on
  the locked release.
- `--legal-manifest <file>` checks that the downloaded manifest has the attested
  digest, describes the locked archive, claims an exhaustive inventory with no
  gaps, and leaves no member without a license. With `--archive` it also
  checks that the manifest lists every zip member with its digest.
- `--release-json <file>` (GitHub's release JSON for the tag) checks that
  GitHub's own `digest` for the archive and for the manifest asset equals the
  attested digests.

- `--toolchain-receipt <file>` checks the content-addressed receipt that
  `build_identity.toolchain_receipt` names, and requires every structured
  `build_identity` field (clang banner, ld version, tool paths and digests,
  SDK, libc++ version and ABI) to equal what the receipt measured, with every
  object built against the attested SDK.
- `--provenance-bundle <file>` runs `gh attestation verify --bundle
  --deny-self-hosted-runners` for the archive, manifest and receipt, accepting
  only `danielraffel/skia-builder` workflows pinned at `refs/heads/main`:
  `build-skia.yml` at the lock's builder commit (`--source-digest`) for records
  a build wrote, and `rebind-provenance.yml` at the commit the signed record
  names as `build_provenance.source_digest` for records rebound from a
  member-identical rebuild. Every result must name the run invocation the
  signed record names as `build_provenance.run` (`run_id`, `run_attempt`).
  A rebind bundle covers the rebound manifest, the rebound receipt and the
  member-comparison report, never the published archive: for rebound records
  the archive is not a bundle subject, and is bound instead by the rebound
  manifest's `archive.sha256` and the member comparison against the
  rebuild-attested rebuild archive. The signed record and the legal manifest
  must agree on whether the records are rebound before the subject set is
  chosen.
- With `--archive`, the manifest's static-archive inventory is re-derived from
  the archive bytes: every member of every slice must be listed with its
  digest, and the inventoried archives must be exactly the lock's.
- A manifest and receipt rebound from a member-identical rebuild carry
  `derived_from`. `--rebuild-archive` (and `--rebuild-receipt`) re-run the
  comparison: every zip member, every static-archive member and every
  `__.SYMDEF` symbol table must have the same content in both archives, fat
  slices are keyed by CPU type and subtype (a duplicate slice is refused), and
  the rebound receipt's toolchain fields must equal the rebuild receipt that
  measured them. The rebound manifest must carry the rebuild manifest's
  license mapping unchanged (files, licenses, per-member licenses, origins).
  Because the rebound records rest on the rebuild's own claims, the rebuild
  archive, manifest and receipt must each verify against the rebuild run's
  build-provenance bundle (named in the signed record's
  `legal_inventory.derived_from.rebuild_bundle`) for
  `build-skia.yml@refs/heads/main`, with `--source-digest` set to the signed
  record's `rebuild_run.head_sha` and `--deny-self-hosted-runners`. All three
  certificates must name the run invocation of the signed record's
  `rebuild_run` (repository, run, attempt); `rebuild_run` and `rebuild_bundle`
  must equal the manifest's derivation; and the rebuild manifest and receipt
  must describe the rebuild archive, with the manifest listing each of its
  zip members (once) with its digest. `--rebuild-archive`,
  `--rebuild-receipt`, `--rebuild-manifest` and `--rebuild-provenance-bundle`
  are all required whenever the manifest is derived.
- A record may not contain keys starting with `_`; the verifier reserves them
  for its own results, which are not signed.
- `verify_extraction.py` requires every landed mirror receipt's Pulp commit
  to pin this same renderer (branch, Skia, Dawn and builder commits, archive
  URL and digest).

The lock may say `release_eligibility: eligible` only when the record is
`owner-attested`, the signature verifies, the inventory is exhaustive, the
lock's `bundled_components_completeness` claims `exhaustive` for the same
manifest digest, no release blockers remain, and the lock `status` is
`byte-locked-renderer-release-attested`. `--require-release-eligible`
additionally requires `--archive`, `--legal-manifest`, `--toolchain-receipt`,
`--provenance-bundle` and `--release-json`. The SDK release
workflow fetches all of them, and any rebuild evidence the record's
`legal_inventory.derived_from` names, before it runs that check.

Pulp's render-toolchain pin must consume this same artifact/source tuple after
the Vellum record is signed. No second provider or independent Skia/Dawn pin
is introduced under D4.
