# Decision 0001: preserve a non-authoritative Pulp history seed

- Date: 2026-07-22
- Status: accepted for historical preparation; active-tree quarantine superseded by Decision 0003
- Source: `Generous-Corp/pulp@2ccff748f0d59da34b01ce1fbceabcf19f452731`

We preserve the smallest audited set of Pulp canvas, render, retained-view,
DesignIR, macOS-host, capture, fixture, legal, and third-party paths with
`git-filter-repo`. The complete path/blob manifest and rewritten maps live in
`provenance/`.

This commit did not transfer product authority. The raw seed was intentionally
not treated as a buildable framework: 39 unresolved Pulp-shaped files were
held in source-only quarantine while new Vellum boundaries were implemented.
Decision 0003 later retired the full projection from the active tip while
preserving this commit and the immutable provenance records.

Rejected alternatives:

- a squash copy, because it loses authorship and path/commit correspondence;
- a subtree, mirror, or bidirectional synchronization mechanism, because it
  creates two editable owners;
- making Pulp consume Vellum as the initial proof, because it tests migration
  compatibility rather than independent product value.
