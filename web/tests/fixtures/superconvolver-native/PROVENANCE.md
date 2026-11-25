# Native Superconvolver field values

`field-golden.json` holds values the native field code computed: seed
hashes, flow samples, and each tracer's seed hashes, head, palette index and
head alpha.

- Source: Pulp `examples/super-convolver/field_renderer.hpp` at Pulp commit
  3a6f370295, compiled with the `super-convolver-shot` flags (Apple clang,
  `-O3`, `-ffp-contract=on`, arm64 macOS libm) into a measuring probe.
- State: the native screenshot's, measured from that build: field clock
  1/60 s (one paint), Rooms 16 so density 16, Flow 0, energy 0.304196358,
  Tracers mode.
