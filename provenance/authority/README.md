# Source-authority handoff

Source authority is active. The accepted coordinates are Vellum record commit
`a106a02816a0cde53daac83f36a6630d664f6637`, signed tag
`refs/tags/authority/native-design-kernel-v1-attempt-2`, and landed Pulp commit
`28d74338ff57e91bb5690308ec9502ebf2fcf09d`, accepted by `@danielraffel` at
`2026-07-24T12:03:00Z`. The current machine state is recorded in
`../pulp-extraction.json`, `../ownership-map.yaml`, and the observatory lock.

No file in this directory activated authority merely by existing. Activation
required the complete two-repository handshake and durable reconciliation
described below.
`transfer-plan.v2.json` names candidate Pulp legacy slices and their active
Vellum implementation boundary. `trust-policy.v1.json` pins the exact
repository, repository-scoped reader/dispatcher App, and check-producer
identities used by the fail-closed handshake. The v1 plan and pending-record
template are retained only as design lineage; new records use schema v2.

The handoff preserves three exact identities:

1. `e4f8c96fcfd19bac433252c36fdf5bfa681e6d25` is the filtered historical
   seed. Its paths, modes, and blobs must match `cut-manifest.json`.
   Historical `unresolved` classifications remain unchanged.
2. A later prepared Pulp ownership commit selects exact candidate paths and
   records their then-current blobs and modes. This snapshot does not freeze
   Pulp or transfer authority.
3. A later immutable authority-start commit is the evolved, audio-free Vellum
   implementation. The seed must be its ancestor, but no retired Pulp path or
   exact historical source blob may be active there.

This avoids the false requirement that renamed/reimplemented Vellum files stay
byte-identical to Pulp forever. Each pending record binds the historical seed
projection, the later activation-candidate projection, and the exact active
implementation projection under the explicit lineage mode
`history-seed-ancestor-active-reimplementation`.

Later authority expansions do not mutate this completed activation. Their
append-only watch and exact-boundary handshakes are specified in
[`expansions/README.md`](expansions/README.md). A watch proposal or acceptance
has no authority effect and cannot authorize implementation.

## Prepare a new record

The completed activation used this procedure. For a genuinely new attempt,
choose a new record path and authority ref. After Pulp lands its v2 prepared
ownership projection and Vellum has a reviewed product commit, build the record
from exact commits:

```sh
python3 tools/provenance/verify_authority_activation.py build-record \
  --pulp-repo /path/to/pulp \
  --pulp-ownership-commit <exact-prepared-pulp-sha> \
  --authority-start-commit <exact-vellum-ready-product-sha> \
  --authority-record-ref refs/tags/authority/<unique-authority-ref> \
  --approved-at <owner-approved-utc-timestamp> \
  --output provenance/authority/records/<unique-record>.json
```

The authority-start commit must pass `verify-ready`: trust identities and
required-check producers are pinned, their required check names match the
transfer plan, and no pending record exists yet.

Commit that generated record by itself. Create a signed annotated
`authority/*` tag at that exact record commit, run the required checks, and
publish an immutable private GitHub Release for that tag. The immutable
release is the protected-ref mechanism for a private repository on the current
GitHub plan; a mutable tag or lightweight tag is rejected. Required checks
must still be successful and bound to pinned GitHub App producers.

The `authority/**` workflow lane verifies the pending record without creating
a circular dependency on its own check runs. It checks out the record's exact
Pulp candidate and runs:

```sh
python3 tools/provenance/verify_authority_activation.py verify-pending \
  --pulp-repo /path/to/exact-pulp-candidate \
  --pulp-ownership-commit <exact-prepared-pulp-sha> \
  --record-path provenance/authority/records/<unique-record>.json \
  --authority-record-commit <exact-record-commit> \
  --expected-authority-ref refs/tags/authority/<unique-authority-ref>
```

That offline gate proves the record is structurally reproducible, is the only
change in one non-merge commit directly after the authority-start commit, and
names the checked-out authority ref. The same exact commit must receive the
independent `forbidden-deps`, `provenance-verify`, and `sterile-consumer`
checks. Live protected-ref, check-producer, and installation-scope validation
remains part of `verify-active`; `verify-pending` cannot self-attest it.

## Activate a new record

`verify-active` needs all of the following at once:

- the exact committed Vellum record and protected authority ref;
- successful Vellum provenance, forbidden-dependency, and sterile-consumer
  check runs on the record commit from pinned producers;
- a dedicated one-repository Vellum reader installation token plus its
  matching App JWT;
- the exact landed Pulp activation commit, ownership projection blob, and
  append-only authority-event blob;
- successful Pulp freeze and trusted-freeze check runs on that exact commit
  from pinned producers;
- strict Pulp branch protection binding both checks to those producers;
- a dedicated one-repository Pulp reader installation token plus its matching
  App JWT; and
- no selected Pulp source or ownership-path-set change between the recorded
  candidate commit and the activation commit.

Templates are not evidence and are rejected by the verifier. Installation
tokens expire and must be minted by the trusted workflow from the pinned App
identity; a standing opaque token secret is not an acceptable implementation.
The Pulp dispatch job separately mints a Vellum-only dispatcher installation
token from the pinned dispatcher App and private-key secret; its repository
scope is one Vellum repository and its only write capability is the Contents
permission required by `repository_dispatch`.

After the landed Pulp commit is verified, download the retained
`pulp-activation-evidence.json` and `authority-active.json` artifacts from the
successful `authority-active` job. From a clean checkout at the exact authority
record commit, materialize the durable active state:

```sh
python3 tools/provenance/finalize_authority_reconciliation.py \
  --pulp-repo /path/to/exact-pulp-history \
  --vellum-repo . \
  --record-path provenance/authority/records/<unique-record>.json \
  --authority-record-commit <exact-record-commit> \
  --pulp-evidence /path/to/pulp-activation-evidence.json \
  --active-proof /path/to/authority-active.json \
  --write \
  --output /path/to/reconciliation-result.json
```

Commit exactly the seven generated provenance/observatory files as the direct
child of the record commit, then fast-forward Vellum `main` to that
reconciliation commit. The finalizer re-proves the record-only commit, exact
Pulp transition, immutable coordinates, check producers, candidate-source
identity, and live proof before writing. Its transaction restores every
replaced file if a write fails. The ordinary observatory tail accepts only
this exact prepared-to-active seven-file transition; it does not generally
allow authority files in evidence-only commits.

That reconciliation advances the observatory and records the durable
acknowledgement. A missed dispatch is recovered from Pulp's append-only event;
it never rolls authority back and never starts source synchronization.

If selected Pulp source or the prepared ownership path set changes after a
candidate record is built, discard that pending attempt and build a new record
from a later prepared Pulp candidate commit. Never update the historical cut
manifest to represent that later state.
