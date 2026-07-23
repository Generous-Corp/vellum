# Pulp tooling-disposition observation

Vellum retains an immutable, machine-readable observation of Pulp's developer
tooling so a later Pulp consumer migration cannot silently discard a command,
flag, skill, MCP tool, or plugin registration. This record is evidence for a
future review. It does **not** transfer ownership, adopt Vellum in Pulp, or
promise that Vellum implements any candidate surface.

## Pinned baseline

The observation is anchored to Pulp commit
`b63008422a7a0657e428a3d9deb947698855b7b3`:

| Evidence | Identity |
| --- | --- |
| Pulp map | `docs/status/pulp-tooling-disposition.json` |
| Git blob | `7b8e603d9569a463cbe16c68f7d2472b52774305` |
| Content SHA-256 | `421100ebd9d15890a60fb9871a4eb96329e9fc374c9f41b7ca15208e68704446` |
| Concrete inventoried source blobs | 90, individually path/mode/blob locked |

[`pulp-tooling-disposition.v1.json`](../../provenance/pulp-tooling-disposition/pulp-tooling-disposition.v1.json)
is byte-for-byte the Pulp-authored map. It contains 54 top-level CLI commands
with their declared arguments and nested subcommands, 28 Claude commands, 55
agent skills, 65 MCP tools, and 3 plugin registrations. Each has Pulp's
`pulp-owned`, `candidate-shared-later`, or `excluded` classification.

[`source-lock.v1.json`](../../provenance/pulp-tooling-disposition/source-lock.v1.json)
binds that snapshot to the exact Pulp commit, map blob, and every concrete
source blob matched by the authoritative map's source patterns. Its status
fields explicitly remain `authority_transfer: false` and
`pulp_adoption: false`.

## Verification

Run:

```sh
python3 tools/provenance/test_verify_pulp_tooling_disposition.py
python3 tools/provenance/verify_pulp_tooling_disposition.py \
  --output /tmp/pulp-tooling-disposition-report.json
```

The verifier uses independently pinned identities and counts, strict JSON
parsing, recursive CLI argument/subcommand validation, unique-name checks, and
source-pattern coverage checks. Its negative controls prove that malformed
JSON, an omitted command, a changed source blob, and a false authority or
adoption claim all fail. Updating data-file digests alongside tampered data is
also rejected.

## Use during a later Pulp adoption review

Before any Pulp migration, generate Pulp's current authoritative map at the
exact proposed consumer commit and compare it with this observation. Every
addition, removal, flag change, and disposition change must receive an explicit
reviewed outcome. Pulp-owned and excluded surfaces remain in Pulp. A
candidate-shared-later surface may be replaced or wrapped only after Vellum has
independent product evidence and the migration names the exact Vellum release.

Do not silently refresh this baseline. A newer observation is a new versioned
snapshot and lock, with its own pinned verifier constants and negative-control
evidence. The original remains available for extraction-to-adoption history.
