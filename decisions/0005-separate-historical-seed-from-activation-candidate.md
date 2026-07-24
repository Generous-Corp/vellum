# Decision 0005: separate historical seed from activation candidate

- Date: 2026-07-23
- Status: accepted; activated 2026-07-24

The initial cut manifest is an immutable record of what was known at the
historical extraction base. Its `unresolved` classifications remain historical
facts and must not be rewritten to make a later authority transfer possible.
Likewise, blobs changed in Pulp after extraction must not be represented as if
they were still the historical blobs.

Authority record schema v2 therefore binds three distinct identities:

1. the historical Pulp projection and preserved Vellum seed;
2. an exact activation-candidate projection from a later prepared Pulp
   ownership commit; and
3. the active, independently implemented Vellum product projection.

The prepared Pulp ownership artifact explicitly selects the candidate slices
and exact paths. That later selection, not a historical classification, is the
transfer decision. The candidate projection records the selected paths'
current blobs and modes, while the historical projection retains their
original blobs, modes, and classifications.

Capturing a candidate does not freeze Pulp, activate Vellum authority, or
permit synchronized source copies. Pulp remains authoritative and editable.
Atomic activation must prove that the selected path set is unchanged and that
no selected source changed between the recorded candidate commit and the
landed activation commit. If either changes, a new candidate record is
required.

The v1 transfer plan and template remain historical design artifacts. New
records use the v2 plan and template.

The v2 identities were later bound by Vellum record
`a106a02816a0cde53daac83f36a6630d664f6637`, landed Pulp activation
`28d74338ff57e91bb5690308ec9502ebf2fcf09d`, and the durable Vellum
reconciliation. The historical seed and prepared candidate remain immutable
evidence; neither is rewritten to represent the active implementation.
