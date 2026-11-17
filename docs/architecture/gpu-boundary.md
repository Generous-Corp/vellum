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

The scene carries a single integer schema version, `kSceneVersion`, now 13.
Version 9 is reserved and adds no fields in this build.
Version 10 adds node effects; version 11 adds animation tracks; version 12
adds plot nodes, whose samples arrive at run time; version 13 adds text layout
and draws missing glyphs as boxes (see Text layout and missing glyphs).
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
version 5: clip, mask and isolation on groups; version 6: blend modes and
DesignIR compositing fields; version 7: interned token receipts; version 8:
gradient vocabulary; version 9: vector assets (no fields yet); version 10:
effects and materials; version 11: animation tracks; version 12: plot nodes;
version 13: text layout.

`Scene::tokens` interns each design token that resolved to a colour once, as a
receipt of its path, the layer that produced the value (`primitive`,
`semantic` or `theme`) and the resolved colour. A node names its receipt by
index in `SceneNode::fill_token`, and its `fill` always holds the receipt's
value, so a renderer draws it without reading the table. The other colours of
a node, its text runs, box shadows, gradient stops and a path's stroke, are
named the same way through `SceneNode::color_tokens`; a mesh's packed vertex
colours are not. `Design` in
`@vellum/ui` emits a `tokenRefs` entry beside the resolved style colour, the
runtime interns the entries into a `tokens` array on the root of the rendered
tree, and the materializer rebuilds the table and the references. The slot
names are `backgroundColor`, `color` (a text node or an attributed text run),
`boxShadow`, `backgroundLinearGradient.stops.<n>`, and on a path `fill`,
`fill.stops.<n>` and `stroke`. A name that is not a slot of the element, or
that names a colour the element lacks, is refused with
`VELLUM_INVALID_TOKEN_REFERENCE`; a colour the node type does not draw is
reported as dropped. A short hex colour (`#fff`, `#ffff`) that `Design`
resolves is the same colour as its long form and its receipt carries the long
form; a gradient stop, box shadow or Path colour written directly in JSX takes
only the six- and eight-digit forms. A theme
switch calls `apply_token_values`, which re-resolves the table and rewrites
only the fills, text runs, shadows, stops and strokes whose receipt changed, so
the scene diff damages those nodes and no others. A text run without a slot of its own that
carries its node's old colour follows it.
A scene before version 7 that carries a table or a reference is refused with a
version diagnostic. Version gates inspect fields and tables, not only kinds:
path geometry on any node needs version 3, and an image reference on any node
or a non-empty `Scene::images` needs version 4. A rendered-tree envelope may
name its `sceneVersion`; without one the tree is built at the current
version, and with one it is built at that version and refused when it carries
a field the version lacks. At any version, a repeated path, a non-finite value, a
reference past the end of the table, a slot the node lacks, or a colour that
differs from its receipt, or a slot whose unused index or stop is not zero
is refused with `VELLUM_INVALID_TOKEN_REFERENCE` by `accept_scene`,
`make_paint_commands` and `scene_loss_diagnostics` alike, so a table edited
without its nodes is never drawn stale.

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
codec.
A host that cannot afford to decode on the render thread can hand PNG bytes to
an `ImageDecodeQueue` instead. `submit` runs those same checks on the calling
thread and queues only input that passes; worker threads hash and decode in
submission order, at most `max_concurrent` at a time and within
`max_inflight_bytes` of decoded texels, and an over-budget job waits rather
than being dropped. The queue is a standalone helper: `SkiaDawnSurface` and the
capture paths do not own or call it yet, so nothing in the renderer enforces the
following and the host must do it. Before a frame the host calls
`publish_decoded_images`, which appends the finished assets the scene still
names to `Scene::images`; the scene diff then damages exactly the nodes whose
asset arrived. Until then such a node draws the placeholder and
`pending_images` names it. A host that needs a deterministic capture calls
`wait_idle` and publishes before it snapshots. Wiring the queue into the render
and capture owners is not part of this change. `cancel_unreferenced` drops jobs
no node names, and a cancelled decode never publishes an asset. The queue never touches a
frame, a canvas or the Dawn device.
The
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

Version 5 adds three grouping fields to every node, and a scene before
version 5 that sets any of them is refused by `render` and yields no paint
commands. A `clip`, in the node's own coordinates so it moves with its
transform, is a rectangle with per-corner radii or the fill area of a path
(its stroke ignored), with anti-aliased edges. By default it limits the node's
own paint and its whole subtree, like CSS `clip-path`, and lowers to
`begin_clip` and `end_clip` commands around everything else the node emits;
a clip that does not `clips_own_paint` limits only the subtree, like CSS
`overflow`, and its brackets sit after the node's own paint, so the node's
fill and shadows stay whole. A `mask` marks a child as a mask for its
siblings: it is not drawn, its subtree is rendered offscreen, and its alpha, or
its luminance times its alpha, multiplies the parent's other children, which
are composited together first in a `begin_layer` named after the parent with
`/masked`. That layer ends with one `begin_mask` and `end_mask` pair per mask
child, so several masks multiply and a mask that draws nothing hides its
siblings; the parent's own paint is not masked, and the root cannot be a
mask. The Skia surface draws a mask into a layer composited with
destination-in, through a luma colour filter for luminance. `isolate` keeps a
node's layer even at full opacity and around a single command. Without blend
modes, isolation only changes rounding, which the GPU test bounds at two
levels. A clip whose rectangle or radii are not finite, or whose path fails
`path_is_valid`, is refused like a non-finite transform, and a scene before
version 5 that uses any of the three fields is refused with a
`VELLUM_SCENE_VERSION_MISMATCH` diagnostic. Damage clips a subtree to the
frame-space box around its clip's extent (the rectangle, or the path's control
box) mapped through the node's composed transform; a clip that spares the
node's own paint does not bound the node's own damage.
A changed clip, isolation or blend mode damages the node's subtree, and adding, removing
or changing a mask, including its opacity or transform, damages its parent's
subtree. The web shell neither clips nor masks: it
counts each clip once, skips each mask's commands so a mask is never drawn as
paint, counts the mask once, and draws its siblings unmasked. The CoreGraphics
smoke canvas takes no scene and does not lower clips, masks or isolation. The
authoring host maps CSS `overflow: hidden` or `clip` to a subtree clip of the
border box (not the padding box) with its border radii, except on text, path
and image nodes, whose glyphs, geometry or pixels are their own paint, so the clip covers
their own paint and a box shadow on them is clipped too, with a
`VELLUM_STYLE_DROPPED` warning; `auto` and `scroll`
clip the same way with a `VELLUM_STYLE_DROPPED` warning, since nothing
scrolls. `clipPath` (SVG path data in the box's coordinates) becomes a clip of
the node and its subtree; it replaces `overflow` rather than intersecting with
it, with a `VELLUM_STYLE_DROPPED` warning. `maskMode` (`alpha` or `luminance`)
makes a mask, and `isolation: isolate` isolation. A mask still takes its place
in its parent's layout, so a mask meant to cover its siblings needs explicit
`x` and `y` or a parent that does not stack it after them. A root `maskMode` is
dropped with a warning, any other invalid value fails the tree, and invalid
clip path data fails it with `VELLUM_INVALID_PATH`. Hit testing and
accessibility boxes ignore clips and masks.

## Text layout and missing glyphs

A text node without `SceneNode::text_layout` draws as every scene before
version 13 drew it: one unwrapped, left-aligned line from the top-left corner
of its box, laid out at an unbounded width. A `TextLayout` (version 13) wraps
the paragraph at the box width and sets its alignment (`start`, `center`,
`end` or `justify`, on a left-to-right base direction), its line height as a
multiple of each line's largest font size with the extra space split above and
below the glyphs (zero keeps the fonts' spacing), a line limit and an ellipsis
(U+2026) that ends the last line when the limit cuts the text. Lines past the
box's height are drawn below it, unclipped. `accept_scene` refuses a layout on
a node that is not text, an unknown alignment, a negative or non-finite line
height and an ellipsis without a line limit with `VELLUM_INVALID_TEXT_LAYOUT`,
and a layout in a scene before version 13 with `VELLUM_SCENE_VERSION_MISMATCH`.
`SkiaDawnSurface::measure_text` takes `TextMeasureOptions` to measure a
laid-out paragraph the way it is drawn; the overload without options keeps the
version 12 measurement. The web canvas shell draws laid-out text on one line
and reports `VELLUM_WEB_TEXT_LAYOUT_NOT_DRAWN`.

Glyphs come only from the packaged faces. Each run tries its own family, then
Noto Sans Arabic, Noto Sans JP and Inter, character by character; host font
fallback stays disabled, because a host font database differs between
machines and OS releases and would make captures and goldens depend on the
host. No broader packaged face is available, so a character none of those
faces covers has no glyph. From version 13 it draws as the face's
missing-glyph (.notdef) box, so the loss is visible and the text keeps its
characters; scenes before version 13 keep drawing it as `?`, so their pixels
do not change. Invalid UTF-8 draws as `?` at every version. Either way the
surface's `diagnostics()` carries one `VELLUM_TEXT_GLYPH_MISSING` warning per
text node naming each uncovered code point once, in ascending order; from
version 13 a default-ignorable code point, which the shaper draws as nothing,
is not reported. Each text's result is remembered, so an unchanged text is not
scanned again on the next frame. The
paragraph cache keys on the layout, the width it wraps at and this policy, so
the same runs at two versions or widths never share a paragraph, and every
paragraph is still built and laid out under the one SkParagraph lock.

## Blend modes

`SceneNode::blend_mode` (scene version 6) combines a node and its subtree with
what lies beneath it. `normal`, the default, draws no group. Any other mode
draws the node's subtree offscreen as one group, faded by its opacity, and
then blends that group over the backdrop, which is whatever the enclosing
group holds or the frame. A blended node is always a `begin_layer`, so a blend
never folds into a lone command's paint, and an ancestor that fades or
isolates contains the blend: inside it the backdrop is the group's own pixels.
Paint commands carry the mode on the layer's `blend_mode`; a scene before
version 6 that sets one is refused with a `VELLUM_SCENE_VERSION_MISMATCH`
diagnostic, as is a mode outside the enumeration.

The vocabulary is `BlendMode` in `scene.hpp`, with the names DesignIR and the
authoring `mixBlendMode` style use (`-` is accepted for `_`, as CSS writes
it). Tier 1 is required of every backend: `normal`, `multiply`, `screen`,
`overlay`, `darken`, `lighten`, `difference`, `exclusion`, `source_over`,
`destination_in` and `destination_out`. Tier 2 is required of Skia Graphite and
optional elsewhere: `hue`, `saturation`, `color`, `luminosity`, `color_dodge`,
`color_burn`, `hard_light`, `soft_light` and the remaining Porter-Duff
operators `clear`, `source`, `destination`, `source_in`, `source_out`,
`source_atop`, `destination_over`, `destination_atop`, `xor` and `plus`. A
backend that lacks a Tier 2 mode draws the group as `normal` and reports a
`VELLUM_BLEND_MODE_FALLBACK` warning naming the node.

| Backend | Tier 1 | Tier 2 |
| --- | --- | --- |
| Skia Graphite | every mode, as the matching Skia blend mode on the layer | every mode |
| Web canvas shell | the group drawn offscreen and composited once with `globalCompositeOperation` | none: drawn as `normal` with a warning |

The shell implements Tier 1 only. A Tier 1 blended group is drawn into an
offscreen canvas through the backend's `blendBegin(operation, alpha)` and
`blendEnd()`, then composited once with its canvas operation and its own
opacity, so overlapping members meet each other normally and the group alpha is
not multiplied into every child. A Tier 2 mode is drawn as `normal` with a
`VELLUM_BLEND_MODE_FALLBACK` warning, and the flattened layer is counted in the
loss count. `blendBegin` and `blendEnd` are new backend functions: a
`VellumCanvasBackend` written before them still renders, because the shell
checks for both before calling either and otherwise draws every blended group as
`normal` with the same warning and a counted loss.

A mode that leaves nothing of the backdrop where the group draws nothing
(`clear`, `source`, `source_in`, `source_out`, `destination_in` and
`destination_atop`, see `blend_mode_clears_backdrop`) empties the whole
backdrop, as the canvas operators do, so a node using one damages the whole
frame. Its layer, and every layer enclosing it, is unbounded, so its backdrop
scope is the whole enclosing group (or frame) rather than its own paint extent.
`source_out` belongs to this set: Porter-Duff source-out keeps none of the
destination, so beyond its source the backdrop is emptied. A mask node draws
coverage, not paint, so it takes no blend mode but `normal`; a scene with a
blended mask is refused, and the authoring host fails such a tree. Other modes touch only the pixels the group covers. The layer cache
never draws a blended group, or a plain group that holds one, from an image,
because an image is drawn on a transparent surface and the blend would meet no
backdrop; a faded or isolated group that holds a blend is its own backdrop and
caches whole. A blended group is enclosed whole when damage cuts it, like any
other group layer. The authoring host maps the `mixBlendMode` style to the mode
on any node type and fails the tree for an unknown name.

DesignIR schema version 2 adds the node `compositing` object (opacity, blend
mode, isolation, a mask mode and a mask reference to a sibling mask) with the
same names. Readers accept DesignIR versions 1 through 5. Version 3 adds
material references; version 4 adds node animation tracks; version 5 adds
plot bindings. Normalization writes the lowest version that covers the
document: 5 when a node has a plot binding, otherwise 4 when a node has tracks,
otherwise 3 when a node has a material reference, otherwise 2 when a node uses compositing, otherwise 1. Compositing
requires version 2 or later, materials require version 3, tracks require
version 4 and plot bindings require version 5; the validator and published JSON
schema enforce all four gates. A mask
reference must name a sibling that is a mask. `materializeDesign` lowers the object into the retained
`opacity`, `mixBlendMode`, `isolation` and `maskMode` styles. Because the
renderer applies a mask to every sibling, it accepts a mask reference only when
the parent holds one mask and every other child names it, and refuses anything
else rather than drawing it differently. The retained materializer refuses
populated tracks and material references, including those on hidden descendants,
until it can preserve them.

The surface draws every frame into a retained offscreen Graphite surface the
size of its target in device pixels, and presents by drawing that image into
the swapchain texture; captures read the retained surface back, through the
swapchain when there is one. `render(scene)` repaints the whole frame and stays
the deterministic capture path. `render_incremental` commits each scene to a
renderer-owned `DamageTracker`, clears only the damage rectangles, and redraws
only the paint commands whose paint extent (shadows and text overhang
included) meets them; the rest of the retained image is left as it was. The
output is byte-identical to a full redraw given the enclosure rules below,
which the partial presentation tests check on Dawn Metal. The first frame, a
resize or surface recreation, a failed or lost-device frame, a scene version,
size or background change, a host `reload`, and damage past the policy
threshold fall back to a full repaint. Hosts pass `invalidate` reasons and node ids through
`SkiaDawnSurface::invalidate`, and `frame_stats` reports the damage area, the
rectangle counts, the commands re-recorded and why a frame repainted fully.
What is cached and when it is discarded is renderer policy: the scene schema
carries no cache hints. Partial redraw is enabled only for scene versions the
damage model understands, up to `kDamageModelSceneVersion` (13), which covers
transforms, group opacity, per-corner radii, paths, images, clips, masks and
isolation on groups, blend modes, token references, gradient vocabulary,
animation tracks, plot nodes, where a plot whose samples alone change
damages only the spans of those samples, and text layout. A laid-out
paragraph's damage spans the frame's width from its box grown by a font size,
because alignment can move an unbreakable cluster past either edge, and reaches
the frame's bottom unless a line limit bounds it by that many lines of its line
height (or two and a half font sizes per line at the fonts' own spacing, which
every packaged face fits), each rounded up to a whole pixel and one more for
the shaper's per-line rounding, plus two font sizes of overhang. The renderer's layer
extents measure the laid-out lines exactly. A version the renderer accepts but the damage model
does not yet understand repaints fully with reason `damage_model_version`. A version outside
[`kMinSupportedSceneVersion`, `kSceneVersion`] is refused before anything is
drawn, and partial redraw likewise serves no version below
`kMinSupportedSceneVersion`. The layer cache compares mesh and texture
content, not pointers, so texels rewritten in place drop a cached image.

Animation tracks on a node drive its opacity, the channels of an animated
transform (translate, rotate, scale), its fill, a path's stroke colour, and the
colours of a shadow, a gradient stop or a text run. Keyframes ease linearly,
by cubic bezier, by steps, or by a damped spring timed in seconds. Tracks are
evaluated at `Scene::clock`, which the host passes for every frame; no renderer
reads a clock of its own, so the same scene at the same clock draws the same
frame byte for byte. Every consumer (lowering, acceptance, damage and the web
canvas shell) works on the scene `evaluate_tracks` produces, so damage covers
exactly the nodes whose evaluated values changed. A track holds its first value
before its first keyframe and its last value after its last, and a spring is
exactly at its target from its settle time on, so a track at rest damages
nothing. A version 10 or older scene with a track is refused with
`VELLUM_SCENE_VERSION_MISMATCH`.

`FrameDriver` (`vellum/graphics/frame_driver.hpp`) is the host loop that runs
tracks and plots. It is the scene client of `foundation::FramePump`
(`vellum/foundation/frame_pump.hpp`), which owns what is not specific to a
scene: the clock, resume handling, frame-needed gating, the thread-safe wake,
bounded render retries and the reduced-motion setting. Any content that can be
advanced and then drawn can drive the same pump with its own advance and render
functions. The host calls `frame` from its vsync source with that
source's own monotonic presentation time, on the thread that owns the scene
and the surface. The driver advances `Scene::clock` by the measured time since
the previous frame, starting from zero at the first frame, so keyframe times
are seconds since the driver's first frame. A gap over 250 ms, or the first
frame after `suspend`, advances one nominal frame so animation never catches up
on idle time; a repeated or backwards timestamp advances nothing; and with
reduced motion the clock jumps to the end of the last track. It then samples
the feed into the scene's plots once and calls the host's render function,
which for the macOS app host is `SkiaDawnSurface::render_incremental`, only
when the frame can differ from the last one drawn: the first frame, a
requested frame, a clock that moved while a track still had keyframes ahead,
or a plot whose samples changed. `FrameReport::needs_next_frame` turns false
once every track has settled and a feed has been silent for
`stale_after_ticks` ticks, and the host then stops its display link, so an
idle app draws no frames. `request_frame`, callable from any thread, is the way
back. The driver reads no clock of its own, so an injected clock gives the same
frames. The macOS app host drives it from a `CADisplayLink` (a main-thread
timer before macOS 14), pauses the link when no frame is needed, and follows
the system reduce-motion setting. It attaches no feed, so its plots stay empty
until a host supplies one with `set_feed`.

A path damages its control box outset by its stroke reach: half the width,
times the miter limit for miter joins or sqrt(2) for square and round caps,
with dashes ignored. A stroke of zero width, or with a transparent colour,
adds no reach. The box is mapped through the composed transform, and the
renderer culls with the same box. When the reach overflows to a non-finite
box, damage covers the frame.

An image damages its box, mapped through the composed transform. Replacing
the asset an image resolves to damages the images that draw it.

A clip bounds the damage of what it clips: its box, mapped through the
composed transform, limits the damage of its subtree and, unless the node
spares its own paint, of the node itself, so content clipped away damages
nothing. Changing a clip damages the clipped subtree under both the old and
the new clip, and toggling isolation damages the subtree. A mask hides its
siblings wherever it draws nothing, so adding or removing a mask, changing its
mode, opacity or transform, or a mask that stops masking damages its parent's
whole subtree. A change to what a mask draws damages where it draws.

Some content does not reproduce when a damage rectangle cuts it: path coverage,
gradient and vertex-coloured fills, images (a linear-sampled image at a
fractional position moves by a level where it is cut), a command that
composites its own opacity layer, group layers (faded, isolated and masked
groups), and the content of a clip, whose anti-aliased edge Graphite folds
into the geometry of each draw. The surface grows the damage to hold each such
command or group it touches whole, and the content of a clip whole up to the
clip's extent. This can raise the damage of a
small change sharply, for example from 7% to 72% of a frame dense with
gradients, and the policy threshold is applied again after this growth. The
surface also disables Graphite's internal multisampling. Otherwise a pass
containing a path renders multisampled, and other content in that pass
rasterizes differently from a pass without the path.

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

Effects (scene version 10, `effect.hpp`) apply to a node and its subtree as
one group: blur, colour matrix and drop shadow filter the group in chain order,
and a backdrop blur or the frosted-glass material redraws what lies beneath the
node's box first. Each effect declares a damage radius, a pass count and a
texture count; a chain over eight passes or four textures, counting the group's
own layer, is drawn without its effects and reported with
`VELLUM_EFFECT_BUDGET_EXCEEDED`. The surface records the group's members into an
offscreen image, the same image the layer cache keeps for the group, and
composites it through the chain cropped to the area the group declares, so a
change to an effect alone reuses the cached members. Damage inside a group
grows by the chain's radius and is bounded only by clips outside the group; a
colour matrix that lifts transparent pixels makes the group's whole area its
damage; and damage reaching the area a backdrop samples damages the backdrop's
box. A partial redraw encloses each effect group whole, together with the area
its backdrop samples. Materials come from `MaterialRegistry`: a scene names one
by id and carries values only, and the registry holds each material's typed
uniforms, ranges, defaults and provenance. An application material registers
through the custom component host, carries the `extension` disposition and is
reported with `VELLUM_MATERIAL_EXTENSION`, and the native renderer draws the
node without it until its component expands it.

Two more triggers are opt-in through `LayerCachePolicy::allow_inexact`: a
subtree of at least 32 commands once it repeats a frame, and any candidate once
it repeats its commands for three frames. Such an image rounds its translucent
and antialiased pixels once more when it is composited, so a frame can differ
from an uncached one by about half a level per overlapping translucent draw
(two levels on the layered test scene) and depends on the surface's history.
Even when allowed, they apply only to scenes that always repaint fully
(versions past `kDamageModelSceneVersion`, which currently covers every
version the renderer accepts, so the triggers change no accepted scene);
a scene partial redraw serves
caches only faded groups, so its partial frames stay byte-identical to full
repaints whatever each surface has cached.
The surface keeps three cross-frame resource caches under
`RenderCacheBudgets`: laid-out text paragraphs keyed by every run's text and
style (the colour with any folded opacity included), the font directory, the
text layout with the width it wraps at, and the missing-glyph policy;
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
