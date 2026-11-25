# Native Superconvolver references

`native-820x560-luma.png` is the perceptual gate's reference: the luma of
each 2 by 2 block of a native capture, made with
`web/tests/make_native_reference.py`.

- Source capture: Pulp `super-convolver-shot` built at Pulp commit
  3a6f370295 on macOS arm64, run with no arguments and no environment
  variables (CPU Skia raster, 820 by 560 at device scale 2).
- Source capture sha256:
  `75fbf611436d7f7eef5dfc892dc9a26adabd0ddfa665fd6c2077b40d40978b6a`.
- State in that capture, measured from the same build: field clock 1/60 s
  (one paint), Rooms 16 so density 16, Flow 0, energy 0.304196358, Tracers
  mode, Mix 45, Size 1.5, Gain 0, Bypass off, Engine CPU, source
  "Synthetic room".

`field-golden.json` holds values the native field code computed: seed
hashes, flow samples, and each tracer's seed hashes, head, palette index and
head alpha.

- Source: Pulp `examples/super-convolver/field_renderer.hpp` at Pulp commit
  3a6f370295, compiled with the `super-convolver-shot` flags (Apple clang,
  `-O3`, `-ffp-contract=on`, arm64 macOS libm) into a measuring probe.
- State: the capture's field state above.
