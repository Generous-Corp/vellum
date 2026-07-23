# Prepared source-authority handoff

Nothing in this directory activates authority. `transfer-plan.v1.json` names
candidate Pulp legacy slices and their active Vellum implementation boundary.
`trust-policy.v1.json` is deliberately disabled and contains null repository,
App, and check-producer identities, so the active verifier cannot pass today.

The handoff preserves two exact identities:

1. `e4f8c96fcfd19bac433252c36fdf5bfa681e6d25` is the filtered historical
   seed. Its paths, modes, and blobs must match `cut-manifest.json`.
2. A later immutable authority-start commit is the evolved, audio-free Vellum
   implementation. The seed must be its ancestor, but no retired Pulp path or
   exact historical source blob may be active there.

This avoids the false requirement that renamed/reimplemented Vellum files stay
byte-identical to Pulp forever. Each pending record instead binds the exact
source projection to the exact active implementation projection under the
explicit lineage mode `history-seed-ancestor-active-reimplementation`.

## Prepare a record

After Pulp lands its v2 prepared ownership projection and Vellum has a reviewed
product commit, build the record from exact commits:

```sh
python3 tools/provenance/verify_authority_activation.py build-record \
  --pulp-repo /path/to/pulp \
  --pulp-ownership-commit <exact-prepared-pulp-sha> \
  --authority-start-commit <exact-vellum-product-sha> \
  --authority-record-ref refs/heads/authority/native-design-kernel-v1 \
  --approved-at <utc-timestamp> \
  --output provenance/authority/records/native-design-kernel-v1.json
```

Commit that generated record by itself. Protect the authority ref at that exact
record commit. Required checks must be strict and bound to pinned GitHub App
producers.

## Activate

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
- no mapped Pulp source change between the recorded extraction base and the
  activation commit.

Templates are not evidence and are rejected by the verifier. Installation
tokens expire and must be minted by the trusted workflow from the pinned App
identity; a standing opaque token secret is not an acceptable implementation.
The Pulp dispatch job separately mints a Vellum-only dispatcher installation
token from the pinned dispatcher App and private-key secret; its repository
scope is one Vellum repository and its only write capability is the Contents
permission required by `repository_dispatch`.

After the landed Pulp commit is verified, the observatory advances and records
the acknowledgement. A missed dispatch is recovered from Pulp's durable event;
it never rolls authority back and never starts source synchronization.
