# Source detection fixtures

Each directory holds an input and the `expected.json` the detector must
produce for it against `product/source-detection.json`. Every input is
synthetic and contains no design-tool data:

- `_unknown/`: an HTML page that must match no source.
- `claude/2024.10/`: a Claude Design export carrying the bundler script tag.
- `stitch/2025.04/`: a Stitch directory export (`code.html`, `DESIGN.md`, and
  a text placeholder named `screen.png`, since only its name is checked).
- `designmd/alpha/`: a DESIGN.md token specification.
- `figma-plugin/2026.05/`: a Figma plug-in scene export, matched by its
  `provenance.adapter` and `format_version` keys.

They are copied from the Pulp detector's fixtures. The DESIGN.md here is the
hand-authored one, not the upstream Google example.
