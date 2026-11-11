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

The scene carries a single integer schema version, `kSceneVersion`, now 4.
A change that can alter rendered output bumps it: a new node kind, a new field
that changes how a scene is drawn, or a change in meaning. A change that only
adds metadata keeps it. Consumers accept versions from
`kMinSupportedSceneVersion` (1) to `kSceneVersion`. Node kinds are closed
per version. The surface's `render` refuses a scene with an unknown version or
node kind before drawing anything and keeps its previous frame, and
paint-command flattening skips both rather than drawing an unknown kind as a
group. The authoring materializer rejects an unknown node type with an error
diagnostic and reports authored style or content it drops as warnings on
`RenderedApplication::diagnostics`. Those diagnostics share the DesignIR
severity and disposition words but identify content by node ID, not by
DesignIR document path. Versions are claimed in order: version 4: image nodes;
version 5: clip, mask and isolation on groups (reserved).

Nodes carry an optional affine transform about their origin, a group opacity,
and per-corner radii beside the uniform `corner_radius` shorthand. All three
change rendered output, so they arrived with version 2. A version 1 scene that
sets any of them is refused by `render` and yields no paint commands. Paint
commands keep untransformed scene-absolute `bounds` and add the composed
transform, so a consumer that ignores it still sees the old geometry. A faded
subtree that draws more than one command is bracketed by `begin_layer` and
`end_layer` commands whose bounds are the union of the members' transformed
paint extents (box, shadow offset and spread, and 1.5 blur radii); text can
overflow its box, so a layer holding text is unbounded. A lone faded command
carries the opacity itself, and the Skia surface still gives it a bounded
layer when its own paint overlaps (several shadows, a gradient over a fill,
decorated text, a mesh). Plain text folds the opacity into its glyph paint, so
glyphs that overlap one another, under negative letter spacing or in joined
scripts, blend twice where they meet. The canvas-backed web shell flattens
layers to per-command alpha and counts layers, meshes, transforms, and
per-corner radii it cannot draw (`vellum_web_lowering_loss_count`); it does not
count the gradients and shadows it also skips. The CoreGraphics smoke canvas
takes no scene and does not lower these properties. A scene with a non-finite
transform, opacity, or radius is refused like an unknown kind.

The authoring `transform` key takes CSS transform functions, matched without
regard to case, with lengths in px or percent of the box. A list that does not
parse is dropped with a `VELLUM_STYLE_DROPPED` warning and the node is drawn
untransformed; only a list that composes to a non-finite matrix fails the
tree. Interaction and accessibility boxes are the axis-aligned box of each
node as drawn, so a rotated control hit-tests on that box, and a node whose
transform has a zero determinant gets no interaction or text-input box.

A `path` node carries a `PathGeometry`: a flat list of move, line,
quadratic, cubic, SVG elliptical arc and close commands with their values,
a nonzero or evenodd fill rule, a stroke (colour, width, miter, round or bevel
joins, butt, round or square caps, miter limit, dash array and dash offset)
and an anti-aliasing flag, shared and immutable like a mesh. The fill paint is
the node's colour and linear gradients, which resolve against the layout box,
or against the path's control box when the layout box has no area; the stroke
is a single colour. The SVG paint defaults (black fill, no stroke, width 1)
are applied by the authoring host, not by the scene. The authoring host parses
an SVG `d` string once, into absolute commands, so a scene never stores path
strings. The path kind arrived with version 3, so a version 1 or 2 scene with a
path node is refused by `render` and yields no paint commands. A path command
carries the composed transform and opacity like any other; a lone faded path
folds the opacity into its paint unless it draws more than one operation (a
fill with a gradient, or a fill and a stroke), and then gets a bounded layer
over its paint extent, the control box outset by the stroke reach. A path is
drawable only within its budget of 4096 commands and 16384 values (64 KiB of `float` data), with at
most 64 dash entries drawing at most 100000 dashes along a bound on the path's
length, finite coordinates and the exact arity per command;
`path_is_valid` states the rules, the parser refuses path data longer than
64 KiB, and the surface refuses a scene with an undrawable path before drawing,
keeping its previous frame. Damage treats a path as its control-point box,
with arcs as their whole ellipse, outset by half the stroke width times the
larger of the miter limit for miter joins and sqrt(2) for round or square
caps. The web shell does not draw paths and counts each one in
`vellum_web_lowering_loss_count`.

An `image` node carries an `ImageReference`: the content hash of an asset
(`sha256:` and 64 lowercase hex digits, the DesignIR convention), an optional
source rectangle in image pixels, a fit (fill, contain, cover or none, as CSS
`object-fit`) and nearest or linear sampling. The decoded pixels live beside
the tree in `Scene::images`, premultiplied RGBA8 `ImageAsset`s that are shared
and immutable like a mesh texture, at most 8192 pixels on a side (the WebGPU
default texture limit) and 256 MiB.
`make_image_asset` premultiplies straight alpha once, and
`SkiaDawnSurface::decode_image_asset` decodes a PNG into an asset named by the
SHA-256 of its bytes, refusing bytes that do not match an expected hash. It
refuses input over 64 MiB (`kMaxEncodedImageBytes`) and a header whose
dimensions are over the image budgets before it hashes the bytes or builds a
codec. The
node's fill paints behind the image and its corner radii clip both;
`image_placement` is the pure fit and crop the renderer draws with. An image
whose asset the scene does not hold draws `kMissingImagePlaceholder` over its
box and is reported as a `VELLUM_IMAGE_ASSET_MISSING` warning by
`scene_loss_diagnostics` and `SkiaDawnSurface::diagnostics`; an image without
a reference or with a malformed one, and a scene whose assets are invalid or
list a hash twice, are refused; an asset is invalid when its texel buffer does
not match its size or a colour channel is above its alpha. Every backend
decides through `accept_scene`, so the web shell refuses the scenes the
surface refuses, with the same reason and diagnostics. The kind arrived with version 4, so an earlier
scene with an image node is refused with a `VELLUM_SCENE_VERSION_MISMATCH`
diagnostic. Damage treats an image as its box and compares its asset by
pointer; a command's paint extent, `transformed_paint_bounds`, is that box mapped
through the command's transform. The surface remembers each asset's texel digest under its content
hash while the hash still names the same asset object, so an unchanged image
is not rehashed each frame, and uploads through the texture cache below; hosts
should keep an unchanged asset's object rather than rebuild it each frame. An
image with children is refused, and a source rectangle that selects nothing of
its asset draws only the fill and is reported with the same warning. The
damage tracker and the surface hold the previous scene's assets until the next
frame. The
web shell draws the placeholder and counts each image in
`vellum_web_lowering_loss_count`; a refused scene draws nothing, `vellum_web_render`
returns 0, and `vellum_web_refused`, `vellum_web_refusal_reason` and the
`vellum_web_diagnostic_*` accessors report the refusal. The authoring materializer lowers an `image`
whose `source` is a SHA-256 content hash, with `fit`, `sampling` and
`sourceRect` attributes; any other source keeps only the box and reports
`VELLUM_NODE_CONTENT_DROPPED`.

The surface draws every frame into a retained offscreen Graphite surface the
size of its target in device pixels, and presents by drawing that image into
the swapchain texture; captures read the retained surface back, through the
swapchain when there is one. `render(scene)` repaints the whole frame and stays
the deterministic capture path. `render_incremental` commits each scene to a
renderer-owned `DamageTracker`, clears only the damage rectangles, and redraws
only the paint commands whose paint extent (shadows and text overhang
included) meets them; the rest of the retained image is left as it was, and
the output is byte-identical to a full redraw. The first frame, a resize or
surface recreation, a failed or lost-device frame, a scene version, size or
background change, a host `reload`, and damage past the policy threshold fall
back to a full repaint. Hosts pass `invalidate` reasons and node ids through
`SkiaDawnSurface::invalidate`, and `frame_stats` reports the damage area, the
rectangle counts, the commands re-recorded and why a frame repainted fully.
What is cached and when it is discarded is renderer policy: the scene schema
carries no cache hints. Partial redraw is enabled only for scene versions the
damage model understands, up to `kDamageModelSceneVersion` (1); a version 2,
3 or 4 scene, which can carry transforms, group opacity, per-corner radii,
paths and images, repaints fully until the damage model learns those fields.

The surface also caches subtrees as Graphite images, under a renderer-owned
`LayerCachePolicy` keyed by node id (`layer_cache.hpp`); scenes carry no
caching hints. A subtree whose node is not the root, has an id no other node
shares, and draws at least two commands is a candidate. A faded group is cached
from its first frame, and an opacity change alone reuses its image; its image
composites exactly as the uncached offscreen group layer did, so captures are
byte-identical with and without caching. Only the outermost cached subtree on
a path is drawn from its image, which covers the subtree's paint extent in
device pixels, with text measured, clipped to the target. An image is dropped
when a command in it changes (content, bounds, structure or a replaced mesh),
when its node leaves the scene, when an enclosing subtree is cached instead,
on a resize or scale change, a host `reload` or an `invalidate` naming a node
inside it, a failed frame, a lost device, or a policy change;
`layer_cache_stats` counts each reason. Images are capped at 32 and at twice
the target's area, and a subtree the budget refuses, or whose image changed
before it was ever drawn from, waits a few frames before it is tried again.

Two more triggers are opt-in through `LayerCachePolicy::allow_inexact`: a
subtree of at least 32 commands once it repeats a frame, and any candidate once
it repeats its commands for three frames. Such an image rounds its translucent
and antialiased pixels once more when it is composited, so a frame can differ
from an uncached one by about half a level per overlapping translucent draw
(two levels on the layered test scene) and depends on the surface's history.
Even when allowed, they apply only to scenes that always repaint fully
(versions past `kDamageModelSceneVersion`); a scene partial redraw serves
caches only faded groups, so its partial frames stay byte-identical to full
repaints whatever each surface has cached.
The surface keeps three cross-frame resource caches under
`RenderCacheBudgets`: laid-out text paragraphs keyed by every run's text and
style (the colour with any folded opacity included) and the font directory;
linear-gradient shaders keyed by their exact end points, colours, positions and
tile mode; and GPU textures uploaded from mesh textures and image assets,
keyed by size and the SHA-256 of the texels rather than by address, so mesh
texels rewritten in place are uploaded again; an image asset's digest is
remembered per asset object, so image assets must be replaced, never
rewritten in place. Keys compare floats by their bits. A hit returns the
object a miss would build from the same inputs, so captures are byte-identical
with
every cache on or off; the render-cache GPU test checks that for each capture
fixture, on the partial-redraw path, and for each key input. Each cache has an
entry and a byte budget and evicts its least recently used entry first; an
entry larger than the byte budget is refused, and a zero budget turns the
cache off. Paragraph bytes are an estimate, since Skia does not report them.
Once a resize settles, meaning the size has held for
`gpu_runtime::SwapchainResizeState::kSettleFrames` presented frames, the
entries no frame has used since the resize are dropped; a device loss drops
every entry, and a frame that fails before it is submitted drops the textures
it uploaded.
`SkiaDawnSurface::telemetry` reports the frame's damage, the offscreen layers
it opened, each cache's hits, misses, evictions, trims and bytes, the texture
(atlas) bytes and Graphite's GPU time, and the capture harness writes it into
its evidence JSON.
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
