# Vellum

> **Work in progress. Under active development, experimental, 0.x.**
> APIs, schemas, CLI names, and the working name itself change without notice.
> Not production software, and not accepting external users or contributions yet.
> Published publicly for transparency and CI, not as an invitation to depend on it.

## What this is

Vellum is an experimental, audio-free application framework built to test one
product question: **can a developer import a design, add TypeScript or JavaScript
behavior, and ship a GPU-rendered application without Chromium or an OS WebView as
the primary UI runtime?**

It is an exploration, not a product. Nothing here is stable, and the answer to
that question is still being established.

## What it includes

Executable in-tree today:

- **Native C++ kernel** — audio-free, with a retained scene graph
- **DesignIR import/reimport** — deterministic design ingestion
- **JS/TS/JSX runtime** — retained-tree, hardened, on a JavaScriptCore host
- **GPU rendering** — retained scene through Skia Graphite, Dawn, and Metal
- **Installable CMake SDK** — macOS 15.0+ arm64 artifact built with a pinned renderer
- **Project CLI** — create, import, design check, build, capture, package
- **Experimental browser proof** — the shared C++ runtime and paint traversal
  compiled to Wasm, with Canvas2D as an explicitly identified presentation shell

The installed SDK bundles authored TS/JS/JSX plus optional imported DesignIR,
builds and runs a real `.app`, executes finite scenarios, captures a GPU PNG, and
emits an ad-hoc-signed package from installed bytes only.

## What this is not

- Not arbitrary HTML, CSS, DOM, website, or React-DOM compatibility
- Not an audio, MIDI, DSP, plug-in-format, or plug-in-hosting framework
- Not a claim of one renderer or WebGPU backend on every target
- Not a public, stable, production-supported framework
- Not a claim of smaller binaries, lower memory, or better performance than
  Electron, Tauri, Flutter, Qt, or React Native without equivalent benchmarks

## Status and compatibility

Projects pin one exact framework version, source commit, target tuple, SDK
checksum, CLI API, and JS package identity in `framework.lock`. **Exact-pin
compatibility is the only promise** — there is no universal C++ ABI promise, and
schemas and CLI names may change between reviewed upgrades.

## License

MIT — see [LICENSE.md](LICENSE.md). Extracted-history attribution and third-party
terms are documented in [NOTICE.md](NOTICE.md) and
[DEPENDENCIES.md](DEPENDENCIES.md).
