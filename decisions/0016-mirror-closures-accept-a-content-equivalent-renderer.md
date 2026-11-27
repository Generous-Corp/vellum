# Decision 0016: mirror closures accept a content-equivalent renderer archive

- Date: 2026-11-27
- Status: proposed. It is accepted when its pull request lands with the two
  counted approvals Decision 0014 requires for `tools/mirror/`.

## Problem

Each landed mirror closure records the Skia/Dawn archive it was exported
against, in every `external_inputs` row and every Skia `external_link_inputs`
row. Decision 0010 has `tools/mirror/mirror_package.py` refuse to plan unless
every pinned file matches its recorded digest, and the packager also refused
any archive other than the recorded one.

The macOS renderer was rebuilt as skia-builder `chrome/m158-attested.2` from
the same Skia and Dawn commits. Its 11 static libraries changed and none of
its 811 headers did. Under the old rule, locking it blocked the mirror build
until Pulp re-exported the closures, and the Pulp exporter is retired. So no
closure can be re-exported, and the renderer pin could not move.

## Decision

The exact archive match stays the fast path. A closure exported against
archive C may be planned against a locked archive D only when all of these
hold:

1. `provenance/renderer-attestation.json` is `builder-attested` for D, and
   `provenance/third-party-lock.json` names D with `release_eligibility:
   eligible`. CI proves both records against the builder's Sigstore
   attestation, legal manifest and toolchain receipt
   (`docs/provenance/renderer-release-attestation.md`).
2. Both records name the same Skia commit and the same Dawn commit, and the
   Skia commit is the closure's pin.
3. The closure pins at least one archive file.
4. The extracted header tree of D (`headers.root`, hashed with the lock's
   `digest_command`) equals the lock's recorded tree for D, and equals the
   tree `tools/mirror/closure_renderer_headers.json` records for C. A symlink
   or special file in the tree is refused. A planted, changed or removed
   unpinned header therefore fails, so nothing outside the closure's pinned
   files can shadow one on the include path.
5. Every file the closure pins is present in D with an identical digest.

A Skia link row naming C is then accepted, provided its pin matches. The plan
records each acceptance under `renderer_content_equivalence`, the packager
prints it, and `targets[].skia_asset_sha256` names D, the archive actually
linked. Closures that disagree on the archive they were exported against are
refused.

When the archive matches exactly and the lock records its header tree, the
extracted directory must hold that tree too. This closes the same shadowing
gap for a `VELLUM_SKIA_DIR` extract, which binds no archive digest.

## Consequences

The renderer can move to a rebuild of the same sources without a re-export.
Library bytes are not compared against the closure, because a rebuild changes
them by design. `cmake/VellumSkiaDawn.cmake` still checks every linked
library against the lock's per-library digests. Any header change, or a
change of Skia or Dawn commit, still needs a new closure. With the exporter
retired, that is a new decision.

`closure_renderer_headers.json` records the header tree of the
`chrome/m158` archive (`dca73c0e...`) that the current closures were exported
against. Anyone can recompute it from that public release asset.

Rejected alternatives:
- Accepting the prior archive by name, as `verify_extraction.py`'s
  `PRIOR_RENDERER_PIN` does for receipts. That trusts an identity rather than
  checking content.
- Re-exporting the closures. The exporter is retired.
