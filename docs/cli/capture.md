# Interaction capture and montages

Vellum scenarios address retained nodes by stable semantic ID. They can wait for
idle, press or click a node, and request a capture without depending on whether
the target is visible on the physical display. The native macOS backend runs
the scenario against its offscreen Skia Graphite/Dawn surface and rejects a
missing interaction target or unsupported action.

Capture one scenario:

```sh
vellum capture --scenario smoke --output artifacts/smoke.png
```

Capture a bounded matrix and compose a deterministic PNG contact sheet:

```sh
vellum capture \
  --matrix tests/capture-matrix.json \
  --montage \
  --output artifacts/montage.png
```

The versioned matrix is checked into the application:

```json
{
  "schema": "vellum.capture-matrix.v1",
  "columns": 2,
  "gap": 16,
  "background": "#181A20",
  "captures": [
    { "name": "home", "scenario": "home" },
    { "name": "settings", "scenario": "settings" }
  ]
}
```

Each source PNG remains beside the montage under
`<montage-stem>-captures/`. The compositor is an installed, dependency-free,
bounded PNG implementation. It validates CRCs, decoded dimensions, pixel
counts, compression boundaries, scanline filters, and accepted RGB/RGBA color
formats before copying pixels. This makes montage generation available in a
sterile consumer without Pillow, ImageMagick, a browser, or a visible window.

The initial driver deliberately supports only `wait-for-idle`, `press`,
`click`, and `capture`. Keyboard/text entry, pointer coordinates, accessibility
queries, and richer synchronization must be added as versioned scenario actions
with native-host tests; unknown actions fail rather than being ignored.
