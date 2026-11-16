#!/usr/bin/env python3
"""check_plugin_editor_async.py fails on a planted blocking call and passes the tree."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

PATH = Path(__file__).with_name("check_plugin_editor_async.py")
SPEC = importlib.util.spec_from_file_location("check_plugin_editor_async", PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

ROOT = Path(__file__).resolve().parent.parent


def make_repo(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
    for name, text in files.items():
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        subprocess.run(["git", "-C", str(tmp), "add", name], check=True)
    return tmp


def run(root: Path) -> int:
    return guard.main(["--root", str(root)])


class PluginEditorAsyncTest(unittest.TestCase):
    def test_repository_is_clean(self) -> None:
        self.assertEqual(run(ROOT), 0)

    def test_control_the_guard_sees_plugin_editor_files(self) -> None:
        files = guard.tracked_files(ROOT)
        self.assertTrue(any(guard.is_plugin_editor_path(f) for f in files))

    def test_planted_blocking_calls_fail(self) -> None:
        for call in (
            "auto r = platform::FileDialog::open_file(t, f);",
            "auto r = FileDialog::save_file(t, f);",
            "auto r = menu.show(x, y);",
            "auto r = menu.show_at_view(view);",
            "auto r = PopupMenu(items).show(0, 0);",
            "show_alert(request);",
        ):
            with self.subTest(call=call):
                root = make_repo({"framework/core/view/src/plugin_editor_x.cpp": call + "\n"})
                self.assertEqual(run(root), 1)

    def test_async_forms_pass(self) -> None:
        text = (
            "show_menu_async(scope, anchor, model, placement, done);\n"
            "FileDialog::open_async(scope, parent, request, done);\n"
            "show_alert_async(scope, parent, request, done);\n"
        )
        root = make_repo({"framework/core/view/src/plugin_editor_x.cpp": text})
        self.assertEqual(run(root), 0)

    def test_app_code_may_block(self) -> None:
        root = make_repo({"apps/app-host/main.cpp": "auto r = FileDialog::open_file(t, f);\n"})
        self.assertEqual(run(root), 0)

    def test_opt_out_needs_the_marker(self) -> None:
        line = "auto r = FileDialog::open_file(t, f); // plugin-editor-async: ok test fixture\n"
        root = make_repo({"framework/core/view/src/plugin_view_x.cpp": line})
        self.assertEqual(run(root), 0)


if __name__ == "__main__":
    unittest.main()
