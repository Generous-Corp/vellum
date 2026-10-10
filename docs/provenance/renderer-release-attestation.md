# Renderer release attestation plan

The `chrome/m153` Skia/Dawn archive is byte locked in
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
   digest, linker identity and digest, and C++ standard-library ABI.
4. `legal_inventory` must be `exhaustive`, with a content-addressed manifest
   covering every transitive source, license, notice, and archive member.
5. `signature` must be `signed`, with the owner-approved issuer, subject, and
   signature digest over the canonical record and artifact digest.

The verifier accepts the pending record so ordinary provenance checks can
describe the current state, but it fails closed when the artifact digest has
no matching record. A release workflow must require the signed and exhaustive
states before changing `release_eligibility` to `eligible`.

Pulp's render-toolchain pin must consume this same artifact/source tuple after
the Vellum record is signed. No second provider or independent Skia/Dawn pin
is introduced under D4.
