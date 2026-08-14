# Interaction capture and montages

Vellum scenarios address retained nodes by stable semantic ID. They can wait for
idle, press or click a node, replace a controlled text input value, dispatch one
of the bounded semantic keys, and request a capture without depending on whether
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

Compare two captures without Pillow or ImageMagick:

```sh
vellum compare reference.png actual.png --diff artifacts/diff.png --json
```

The `vellum.pixel-comparison.v1` result records exact dimensions, crop,
compared and differing pixel counts, mean absolute error, maximum channel
error, threshold, similarity, and pass/fail. A comparison passes only when no
pixel exceeds the explicit threshold (zero by default). The optional diff PNG
is deterministic: unchanged pixels are opaque black and changed pixels carry
the per-channel absolute error. Crops must be explicit and in bounds; a
dimension mismatch or malformed PNG fails closed.

An editable flow remains inspectable JSON and preserves action order:

```json
{
  "schema": "vellum.scenario.v1",
  "name": "rename-board",
  "viewport": { "width": 640, "height": 400 },
  "steps": [
    { "action": "input", "target": "board-title", "text": "Roadmap" },
    { "action": "key", "target": "board-title", "key": "Enter" },
    { "action": "capture", "name": "renamed" }
  ]
}
```

`input` replaces the complete controlled value and emits `onChange` with
`{value,inputType:"scenario"}`. It does not simulate individual keystrokes.
`key` accepts exactly `Enter`, `Escape`, `Backspace`, `Tab`, the four arrow
keys, `Home`, `End`, and `Delete`. `Enter` also emits `onSubmit` when authored;
`Backspace` removes the final Unicode grapheme through `onChange`. Other keys
require `onKeyDown`. Scenarios are limited to 1,000 actions, 1,024-byte target
IDs, and 64 KiB UTF-8 input values. Unknown fields, unknown actions, malformed
payloads, missing targets, and unavailable handlers fail rather than being
ignored.

This is semantic retained-tree automation, not browser automation. Pointer
coordinates, accessibility queries, arbitrary key chords, arbitrary IME input,
and richer synchronization are not supported.

The web proof launcher places Chromium's DevTools Protocol on a private
loopback port and exposes it only through the short-lived
`vellum.cdp-admission.v1` proxy. CDP discovery and WebSocket paths require a
fresh bearer token; the token is never included in endpoint metadata or logs.
The launcher also disables the proxy and rejects non-loopback browser access.
This is an admission boundary, not a claim that the current semantic scenario
API exposes arbitrary CDP operations. Navigation, DOMSnapshot, computed-style,
asset extraction, and bounded CDP interaction remain subsequent P4 work.

The installed web lane also contains the bounded CDP client. It authenticates
discovery and the WebSocket upgrade through the admission proxy, permits only
navigation to numeric loopback HTTP(S) URLs and the snapshot command, and
limits computed-style names and protocol messages. It intentionally does not
expose arbitrary JavaScript evaluation or arbitrary CDP commands.
