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
- an installed shared `Vellum::Gpu` forwarding target backed by the shared
  `Vellum::SkiaDawnProvider`, which hides Skia/Dawn static-link details from
  application builds, plus a headers-only `Vellum::DawnHeaders` target for an
  embedding host's authenticated bootstrap and compute code.

The scene carries a single integer schema version, `kSceneVersion`, starting
at 1. Fields whose defaults leave existing scenes unchanged are added without a
bump; a new node kind or a change in meaning bumps it. Node kinds are closed
per version. The surface's `render` refuses a scene with an unknown version or
node kind before drawing anything and keeps its previous frame, and
paint-command flattening skips both rather than drawing an unknown kind as a
group. The authoring materializer rejects an unknown node type with an error
diagnostic and reports authored style or content it drops as warnings on
`RenderedApplication::diagnostics`. Those diagnostics share the DesignIR
severity and disposition words but identify content by node ID, not by
DesignIR document path.

A `path` node carries a `PathGeometry`: a flat list of move, line,
quadratic, cubic, SVG elliptical arc and close commands with their values,
a nonzero or evenodd fill rule, a stroke (colour, width, miter, round or bevel
joins, butt, round or square caps, miter limit, dash array and dash offset)
and an anti-aliasing flag, shared and immutable like a mesh. The fill paint is
the node's colour and linear gradients, which resolve against the layout box,
or against the path's control box when the layout box has no area; the stroke
is a single colour. The SVG paint defaults (black fill, no stroke, width 1)
are applied by the authoring host, not by the scene. The authoring host parses an SVG `d` string once, into absolute
commands, so a scene never stores path strings. A path is drawable only within
its budget of 4096 commands and 16384 values (64 KiB of `float` data), with at
most 64 dash entries drawing at most 100000 dashes along a bound on the path's
length, finite coordinates and the exact arity per command;
`path_is_valid` states the rules, the parser refuses path data longer than
64 KiB, and the surface refuses a scene with an undrawable path before drawing,
keeping its previous frame. Damage treats a path as its control-point box,
with arcs as their whole ellipse, outset by half the stroke width times the
larger of the miter limit for miter joins and sqrt(2) for round or square
caps. Renderers that cannot draw paths,
such as the web shell, report each dropped command as a
`VELLUM_NODE_CONTENT_DROPPED` warning.

Native self-test is deliberately fail-closed. A requested native surface is
not allowed to fall back to CoreGraphics or an offscreen surface. The test also
contains a blank-frame negative control so a broken capture detector cannot
turn an empty renderer into a passing proof.

The installed CLI materializes imported DesignIR into the authoring runtime and
builds the application host against the public `Vellum::Gpu` target. Native
scenario, capture, and packaging evidence exercises that path from a sterile
installed SDK. Applications may use only the public installed targets and
headers; reaching into Vellum source or renderer internals is forbidden.

`SkiaDawnSurface::Config` retains its original layout. A host linked with
`Vellum::Gpu` and `Vellum::DawnHeaders` calls the separate versioned
`register_dawn_bootstrap` API before `create`, authenticating its Dawn header,
exported proc table, and native provider before it calls `dawnProcSetProcs`.
The `Vellum::SkiaDawnProvider` dylib owns the sole static Dawn definitions and
the process bootstrap coordinator; `Vellum::Gpu` is a forwarding frontend and
the host contributes declarations and the callback only.
Same-revision requests are idempotent, a different revision is rejected before
callback execution, and a reentrant or simultaneous initialization fails
without waiting on host code. An old `Config` caller cannot be read past and
fails closed until a host registers the bootstrap.

Release builds pass the archive itself through `VELLUM_SKIA_ARCHIVE`; CMake
verifies its SHA-256, extracts it into the build tree, verifies the locked
renderer archive set, and links only the locked members it consumes. Builds do
not discover a Pulp checkout or an ambient environment variable. Release
builds use the pinned artifact in
[`provenance/third-party-lock.json`](../../provenance/third-party-lock.json).
`VELLUM_SKIA_DIR` remains available for local development, but it is accepted
only when its Skia and Dawn libraries match the locked digests; an arbitrary
directory is not release evidence.
