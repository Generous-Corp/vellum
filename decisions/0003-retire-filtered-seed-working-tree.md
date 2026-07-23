# Decision 0003: retire the filtered seed from the active working tree

- Date: 2026-07-22
- Status: accepted

The prepared projection was useful for preserving authorship and proving an
exact extraction boundary, but leaving it editable at the active tip would
create ambiguous ownership and invite accidental reuse of product-specific
assumptions. Vellum therefore removes the 232 projected files under `core/`,
the vendored font and NanoSVG paths, `packages/pulp-import-ir/`, and
`tools/figma-plugin/`.

This is an ordinary Git-tracked deletion, not a history rewrite. The filtered
seed commit, original cut manifest, path specification, and filter maps remain
immutable and verifiable. Vellum's active implementation consists only of
separately authored Vellum modules.

The extraction debt is closed because quarantined source no longer exists at
the active tip, not because the 39 historical unresolved classifications were
retrospectively reclassified. A verifier now proves that:

- the historical seed still matches the byte-locked manifest;
- no retired prefix exists at the active tip;
- no active source file is an exact copy of a historical projected blob;
- active source has no Pulp namespace or product-specific audio/plug-in edge;
- SDK archives and sterile installs pass the same contamination boundary.

Future code may use the historical implementation as design input, but it must
enter through a reviewed Vellum-owned change. No synchronization relationship
is established.
