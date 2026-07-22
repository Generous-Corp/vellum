# Decision 0002: use a clean CoreGraphics proof before the GPU slice

- Date: 2026-07-22
- Status: accepted for the first native-kernel checkpoint

The filtered Pulp canvas build file activates optional and unresolved source,
exports `pulp/*` headers, and requires compatibility target names. Reusing it as
Vellum's installed `Graphics` package would make the provenance quarantine
false even though that particular configuration happens to link no audio
frameworks.

The initial native smoke therefore uses a small Vellum-owned CoreGraphics
backend with no transferred Pulp source. It proves the app lifecycle, drawing
API shape, native window, pixel content floor, install/export boundary, and
sterile consumer without claiming the required Skia/Dawn GPU runtime exists.

The history-preserved Pulp canvas remains source-only input to the later GPU
slice. Before any of it becomes authoritative or installed, Vellum must create
clean Skia/Dawn targets, eliminate all unresolved dependency debt, replace
`pulp/*` public identities, and pass the forbidden-dependency and sterile-SDK
gates.
