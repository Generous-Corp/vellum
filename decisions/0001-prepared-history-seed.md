# Decision 0001: preserve a non-authoritative Pulp history seed

- Date: 2026-07-22
- Status: accepted for preparation only
- Source: `Generous-Corp/pulp@2ccff748f0d59da34b01ce1fbceabcf19f452731`

We preserve the smallest audited set of Pulp canvas, render, retained-view,
DesignIR, macOS-host, capture, fixture, legal, and third-party paths with
`git-filter-repo`. The complete path/blob manifest and rewritten maps live in
`provenance/`.

This commit does not transfer product authority. The raw seed is intentionally
not treated as a buildable framework: 39 unresolved Pulp-shaped files remain
source-only quarantine, and no active target may link or install them. New
audio-free boundaries are implemented separately so provenance and framework
reorganization remain distinguishable.

Rejected alternatives:

- a squash copy, because it loses authorship and path/commit correspondence;
- a subtree, mirror, or bidirectional synchronization mechanism, because it
  creates two editable owners;
- making Pulp consume Vellum as the initial proof, because it tests migration
  compatibility rather than independent product value.
