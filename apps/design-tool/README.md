# Design tool

The JavaScript of the AI design tool, moved from Pulp's `examples/design-tool/`.
The modules share one global scope and load in order: `oklch.js`, then
`design-tool-core.js`, `-toolbar`, `-palette`, `-popup`, `-preview`, `-chat` and
`-export`. `vellum design-debug --script apps/design-tool/design-tool-core.js` loads
the whole set headlessly.

Each file is Pulp's bytes at the commit its manifest in `moves/` names, apart
from the respelling listed in `provenance/design-tool/respell.json`. Verify with:

```sh
python3 apps/design-tool/verify_verbatim_move.py --pulp-repo <pulp checkout>
```

Without `--pulp-repo` the check compares against the blob ids the manifests
record; the ctest `vellum.design-tool-verbatim-move` runs it that way. Fixes to
these files land after the move, as ordinary changes with their own manifest
update.
