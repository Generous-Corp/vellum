#!/usr/bin/env python3
"""Seeded controls for the mirror-observer link census: each must be reported."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_mirror_observers as b  # noqa: E402

MAP = """# Path: /x/bin/tool
# Object files:
[  0] linker synthesized
[  1] /x/obj/main.cpp.o
[  2] /v/libvellum_mirror_rehearsal_canvas_pulp_canvas.a(canvas.cpp.o)
[  3] {extra}
# Sections:
"""


def census(extra: str, kept_sources=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tool.map"
        path.write_text(MAP.format(extra=extra), encoding="utf-8")
        swapped = {"/p/core/canvas/libpulp-canvas.a": ["/v/libvellum_mirror_rehearsal_canvas_pulp_canvas.a"]}
        return b.link_census(path, swapped, kept_sources or {}, {"core/view/src/web_view_backend.cpp"})


class LinkCensus(unittest.TestCase):
    def test_clean_link_counts_vellum_objects(self):
        result = census("/p/third/libyoga.a(a.cpp.o)")
        self.assertEqual(result["vellum_objects"], 1)
        self.assertEqual(result["pulp_mirrored_objects"], 0)
        self.assertEqual(result["kept_objects_from_mirrored_sources"], [])

    def test_object_from_a_replaced_pulp_archive_is_counted(self):
        self.assertEqual(census("/p/core/canvas/libpulp-canvas.a(font.cpp.o)")["pulp_mirrored_objects"], 1)

    def test_kept_archive_object_built_from_a_mirrored_source_is_reported(self):
        kept = {"/p/core/view/libpulp-view.a": {"web_view_backend.cpp.o": "core/view/src/web_view_backend.cpp"}}
        result = census("/p/core/view/libpulp-view.a(web_view_backend.cpp.o)", kept)
        self.assertEqual(len(result["kept_objects_from_mirrored_sources"]), 1)


if __name__ == "__main__":
    unittest.main()
