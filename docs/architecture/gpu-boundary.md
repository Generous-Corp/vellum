# macOS GPU boundary

The first Vellum GPU slice is an independent Vellum implementation. It does
not transfer or compile the preserved `core/canvas`, `core/render`, or
`core/view` Pulp projections. Those historical paths remain non-authoritative
until they are either removed from the active tree or transferred through the
recorded two-repository authority protocol.

The public slice consists of:

- a small retained `Scene` with stable semantic IDs;
- a `SkiaDawnSurface` that owns a Dawn Metal device, Skia Graphite context,
  recorder, and either a native `CAMetalLayer` or an offscreen texture;
- explicit renderer evidence and asynchronous Graphite readback;
- content-floor analysis shared by native capture and installed consumers;
- an installed shared `Vellum::Gpu` target that hides Skia/Dawn static-link
  details from application builds.

Native self-test is deliberately fail-closed. A requested native surface is
not allowed to fall back to CoreGraphics or an offscreen surface. The test also
contains a blank-frame negative control so a broken capture detector cannot
turn an empty renderer into a passing proof.

The renderer is intentionally not yet connected to imported DesignIR or the
CLI backend. That integration must use the public scene API and must be proven
from a sterile installed SDK; reaching into Vellum source from an application
is forbidden.

Release builds pass the archive itself through `VELLUM_SKIA_ARCHIVE`; CMake
verifies its SHA-256, extracts it into the build tree, verifies the exact Skia
and Dawn library digests, and links only those two locked archives. Builds do
not discover a Pulp checkout or an ambient environment variable. Release
builds use the pinned artifact in
[`provenance/third-party-lock.json`](../../provenance/third-party-lock.json).
`VELLUM_SKIA_DIR` remains available for local development, but it is accepted
only when its Skia and Dawn libraries match the locked digests; an arbitrary
directory is not release evidence.
