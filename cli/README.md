# Vellum CLI

The CLI is a dependency-free Python authoring shell. Project creation, lock
validation, prerequisite diagnosis, installed DesignIR import/reimport, and
non-mutating deterministic `design check`/`design diff` work today. The pinned
macOS 15.0+ arm64 GPU SDK installs a native backend for
build, finite run, scenario test, capture, and `.app` packaging; SDKs without
that payload and unsupported targets fail clearly.

`vellum dev` is a CLI-owned deterministic source watcher. It drives installed
build/run capabilities and records a versioned JSONL transcript; native and
browser reload behavior is documented in
[`docs/cli/dev.md`](../docs/cli/dev.md).

Run its tests with:

```sh
python3 -m unittest discover -s cli/tests -v
```

The executable filesystem contract is documented in
[`docs/cli/import-reimport.md`](../docs/cli/import-reimport.md).

## Illustrator and PDF artwork

Use `vellum import illustrator artwork.ai --out artwork.svg` (or a PDF input)
to convert one page through Poppler's `pdftocairo`. The command prints the SVG
and its hash-bound provenance JSON path. Poppler is an external, user-installed
tool. Vellum never bundles it and only invokes its GPL-licensed executable.
