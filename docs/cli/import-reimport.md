# Import and reimport

The installed DesignIR backend connects the public Python `vellum` CLI to
`@vellum/design-ir`. This experimental lane accepts JSON in any of these
contracts:

- a credential-free `2026.05-figma-plugin-v1` export containing only the
  generic Figma node subset;
- a Vellum adapter source model containing `source`, `root`, optional `tokens`,
  `assets`, and `diagnostics` fields;
- canonical `https://vellum.dev/schemas/design-ir/v1` JSON.

The `--source-type figma` route decodes the generic, credential-free Figma
plugin envelope directly. It preserves Figma node IDs, tokens, assets, source
provenance, and unsupported-property diagnostics. It fails closed if an export
contains audio-widget kinds or audio-specific binding fields. It does not decode
an arbitrary `.fig`, REST response, or provider archive. The machine-readable route contract is
[`product/source-support.yaml`](../../product/source-support.yaml); unavailable
Claude Design, React-project, HTML, REST, and `.fig` routes are not silently
accepted by the normal CLI.

From an installed local-development CLI:

```sh
vellum create "Import App" --directory ./import-app
cd ./import-app
vellum import ../revision-a.source.json --source-type figma --as main
vellum reimport --source ../revision-b.source.json --as main
```

Both commands support `--json`. The CLI writes deterministic, reviewable files:

```text
sources/imported/main/<revision>/       immutable source and asset snapshot
design/ir/sources/main.designir.json    canonical source DesignIR
design/ir/app.designir.json             active aggregate for the one-source v0
design/generated/                       component definitions and typed IDs
design/overlays/main.authored.json      developer-owned bindings and overrides
design/reports/                         import/reimport diagnostics and candidates
design/import.lock.json                 accepted active revision and hashes
tokens/imported/                        generated primitive tokens
assets/generated/                       copied assets and provenance manifest
ui/generated/                           materialized UI and resolved bindings
src/, components/, native/              developer-owned and never rewritten
```

Edit `design/overlays/main.authored.json` to add bindings, reviewed aliases,
structured visual overrides, semantic tokens, or theme overrides. Reimport
reapplies that file without rewriting it. If a binding or override no longer
resolves, reimport fails, keeps the current DesignIR and lock active, and writes
the candidate DesignIR plus a conflict report for review. Fix the overlay and
rerun the same command. A source snapshot revision can never be replaced with
different bytes.

`cli/tests/test_import_backend.py` executes this public command sequence,
checks deterministic output in different filesystem locations, proves authored
files remain byte-identical, and exercises the rejected-conflict path.
