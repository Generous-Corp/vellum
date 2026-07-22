# Source ownership

Vellum is an independent-product experiment. Pulp does not consume it, and
this repository is not yet authoritative for the preserved Pulp slices.

The machine-readable source of truth is
[`provenance/ownership-map.yaml`](../provenance/ownership-map.yaml). Its
activation state is currently `prepared`. New code explicitly marked
`framework-reimplemented-no-transfer` is Vellum-owned; matching Pulp code is
neither copied nor frozen by that designation.

## Authority transfer

A Pulp-derived slice transfers only through one reviewed, two-repository
handshake:

1. Vellum publishes an immutable pending authority record containing the exact
   source base, cut manifest digest, transferred paths, and Vellum commit.
2. Pulp verifies that record, changes the same exact slices from
   `pulp-authoritative-untransferred` to
   `framework-authoritative-transferred`, and lands an append-only transition
   event with the Vellum commit.
3. Vellum records the landed Pulp commit, advances its observatory cursor, and
   makes the transition active.

Until all three steps complete, Pulp remains authoritative and Vellum must not
claim the extracted files as its maintained product source.

After activation, general-purpose fixes and features originate only in Vellum.
Pulp may keep its legacy implementation running. An urgent Pulp fix is either
Pulp-specific, a named one-way Vellum backport, or a short-lived emergency
exception. It is never an editable synchronized copy.

## Abandonment or reversal

Stopping the experiment does not silently restore shared ownership. A reviewed
reversal must be recorded in both repositories and choose either a frozen Pulp
snapshot for a defined support period or an explicitly independent Pulp-owned
fork. The observatory remains append-only through the decision.

## Current blockers

The prepared history seed must not be activated until:

- the older Burl experiment receives a ratified, truthful disposition;
- required checks can be enforced on this private repository;
- dedicated least-privilege cross-repository credentials exist;
- the active projection contains no unresolved or forbidden dependency row;
- the exact merge result and counterpart commits are verified on both sides.
