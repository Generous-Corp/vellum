# Import fidelity tools

Dependency-free tools for judging an imported render against its reference.
They read and write PNG through `cli/vellum_png.py`, so they run in a sterile
checkout with no Pillow and no font files.

- `reference_diff.py REFERENCE CANDIDATE [--threshold 0.85] [--size WxH] [--json] [--residual-json PATH]`
  scores a candidate (40% histogram cosine, 60% mean pixel distance), flags a
  blank candidate, and sorts the result into a tier: pass, close,
  wrong-variant, fundamental or blank. The histogram half is position-blind,
  so a high score does not mean the layout is right. Images are compared at
  the reference size unless `--size` is given; resizing is nearest-neighbour.
  Exit 0 is pass, 1 is fail, 2 is cannot compare.
- `montage.py --out OUT [--columns N] [--no-labels] PATH[:Label] ...` stacks
  images under titled bars so a comparison names its panels. Labels use a
  built-in 5x7 bitmap font.

For an exact pixel differential use `vellum compare`; for a similarity gate
use `vellum-design-ab`.
