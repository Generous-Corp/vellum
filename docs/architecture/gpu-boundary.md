# macOS GPU boundary

The first Vellum GPU slice is an independent Vellum implementation. It does
not transfer or compile the preserved `core/canvas`, `core/render`, or
`core/view` Pulp projections. Those historical paths were removed from
Vellum's active tree. Source authority for the selected mapped legacy slices
later transferred to the independently implemented Vellum boundary through the
recorded two-repository protocol; no historical Pulp source was restored.

The public slice consists of:

- a small retained `Scene` with stable semantic IDs;
- a `SkiaDawnSurface` that owns a Dawn Metal device, Skia Graphite context,
  recorder, and either a native `CAMetalLayer` or an offscreen texture;
- explicit renderer evidence and asynchronous Graphite readback;
- content-floor analysis shared by native capture and installed consumers;
- one serialized SkParagraph builder/layout boundary around the shared
  packaged-font collection, with stable caller-owned run values and randomized
  Release plus AddressSanitizer concurrency coverage;
- an installed shared `Vellum::Gpu` target that hides Skia/Dawn static-link
  details from application builds, plus a headers-only `Vellum::DawnHeaders`
  target for an embedding host's authenticated bootstrap and compute code.

Native self-test is deliberately fail-closed. A requested native surface is
not allowed to fall back to CoreGraphics or an offscreen surface. The test also
contains a blank-frame negative control so a broken capture detector cannot
turn an empty renderer into a passing proof.

The installed CLI materializes imported DesignIR into the authoring runtime and
builds the application host against the public `Vellum::Gpu` target. Native
scenario, capture, and packaging evidence exercises that path from a sterile
installed SDK. Applications may use only the public installed targets and
headers; reaching into Vellum source or renderer internals is forbidden.

`SkiaDawnSurface::Config` requires a versioned `DawnBootstrap` callback and
the expected Dawn revision. A host linked with `Vellum::Gpu` and
`Vellum::DawnHeaders` may authenticate its Dawn header, exported proc table,
and native provider before it calls `dawnProcSetProcs`. The GPU dylib owns the
sole static Dawn definitions and the process bootstrap coordinator; the host
contributes declarations and the callback only. Same-revision requests are
idempotent, a different revision is rejected before callback execution, and a
reentrant or simultaneous initialization fails without waiting on host code.

Release builds pass the archive itself through `VELLUM_SKIA_ARCHIVE`; CMake
verifies its SHA-256, extracts it into the build tree, verifies the locked
renderer archive set, and links only the locked members it consumes. Builds do
not discover a Pulp checkout or an ambient environment variable. Release
builds use the pinned artifact in
[`provenance/third-party-lock.json`](../../provenance/third-party-lock.json).
`VELLUM_SKIA_DIR` remains available for local development, but it is accepted
only when its Skia and Dawn libraries match the locked digests; an arbitrary
directory is not release evidence.
