#!/usr/bin/env python3
"""Tests for the installed-mirror GPU surface guard."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_surface_guard as guard  # noqa: E402

FWD = "#pragma once\n#if defined(PULP_GPU_RUNTIME)\nnamespace gpu_runtime {\nclass GpuSurface;\n}\n#endif\n"
SURFACE = "// surface\n#pragma once\n#include <gpu_runtime/gpu_surface.hpp>\n"
WINDOW_HOST = "#pragma once\n#include <pulp/platform/gpu_fwd.hpp>\nstruct WindowHost {};\n"
CANVAS = "#pragma once\nstruct Canvas {};\n"


class Mirror:
    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.files = {
            "mirror/core/platform/include/pulp/platform/gpu_fwd.hpp": FWD,
            "mirror/core/render/include/pulp/render/gpu_surface.hpp": SURFACE,
            "mirror/core/view/include/pulp/view/window_host.hpp": WINDOW_HOST,
            "mirror/core/canvas/include/pulp/canvas/canvas.hpp": CANVAS,
            "mirror/core/render/src/gpu_surface.cpp": "#include <gpu_runtime/gpu_surface.hpp>\n",
        }
        for path, text in self.files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)

    def install(self, prefix: Path, plan: dict) -> Path:
        """What VellumMirror.cmake installs: each include root, guarded copies over choke headers."""
        include = prefix / "mirror/include"
        for root in guard.include_roots(self.repo):
            shutil.copytree(root, include, dirs_exist_ok=True)
        for header in plan["headers"]:
            target = include / header["relative"]
            shutil.copyfile(header["copy"], target)
        shutil.copytree(plan["config_dir"], include, dirs_exist_ok=True)
        return include


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mirror = Mirror(self.root)
        self.plan = guard.write_guard(self.mirror.repo, self.root / "generated")
        self.include = self.mirror.install(self.root / "prefix", self.plan)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_choke_headers_are_computed_not_listed(self) -> None:
        self.assertEqual([h["relative"] for h in self.plan["headers"]],
                         ["pulp/platform/gpu_fwd.hpp", "pulp/render/gpu_surface.hpp"])
        # A source file is not a header, and a header that only includes a
        # choke header is guarded through it rather than itself.
        self.assertNotIn("pulp/view/window_host.hpp", [h["relative"] for h in self.plan["headers"]])
        self.assertEqual((self.root / "generated/gpu-guarded-headers.txt").read_text(),
                         "pulp/platform/gpu_fwd.hpp\npulp/render/gpu_surface.hpp\n")

    def test_guard_line_goes_right_after_pragma_once(self) -> None:
        copy = Path(self.plan["headers"][1]["copy"]).read_text()
        self.assertEqual(copy, "// surface\n#pragma once\n" + guard.GUARD_LINE + "\n"
                               "#include <gpu_runtime/gpu_surface.hpp>\n")

    def test_mirror_sources_are_untouched(self) -> None:
        for path, text in self.mirror.files.items():
            self.assertEqual((self.mirror.repo / path).read_text(), text)

    def test_choke_header_without_pragma_once_is_refused(self) -> None:
        (self.mirror.repo / "mirror/core/render/include/pulp/render/gpu_surface.hpp").write_text(
            "#include <gpu_runtime/gpu_surface.hpp>\n")
        with self.assertRaises(guard.GuardError) as caught:
            guard.write_guard(self.mirror.repo, self.root / "again")
        self.assertIn("needs #pragma once", str(caught.exception))

    def test_installed_tree_checks_clean(self) -> None:
        self.assertEqual(guard.check_installed(self.include, self.mirror.repo, require_guard=True), [])

    def test_unguarded_installed_choke_header_is_refused(self) -> None:
        # The break check: the mirrored file installed as is, without the guard.
        (self.include / "pulp/render/gpu_surface.hpp").write_text(SURFACE)
        problems = guard.check_installed(self.include, self.mirror.repo, require_guard=True)
        self.assertEqual(problems, ["installed pulp/render/gpu_surface.hpp is not its mirror source "
                                    "with the guard line"])

    def test_edited_installed_header_is_refused(self) -> None:
        (self.include / "pulp/canvas/canvas.hpp").write_text(CANVAS + "// edited\n")
        problems = guard.check_installed(self.include, self.mirror.repo, require_guard=True)
        self.assertEqual(problems, ["installed pulp/canvas/canvas.hpp is not its mirror source"])

    def test_missing_config_header_is_refused(self) -> None:
        (self.include / guard.CONFIG_RELATIVE).unlink()
        problems = guard.check_installed(self.include, self.mirror.repo, require_guard=True)
        self.assertIn(f"installed {guard.CONFIG_RELATIVE} is missing or not the generated config header",
                      problems)

    def test_required_guard_with_nothing_to_guard_is_refused(self) -> None:
        for header in self.plan["headers"]:
            (self.include / header["relative"]).unlink()
        problems = guard.check_installed(self.include, self.mirror.repo, require_guard=True)
        self.assertIn("no installed choke header was found, but the guard is required", problems)

    def test_cli_exit_codes(self) -> None:
        script = str(Path(guard.__file__))
        ok = subprocess.run([sys.executable, script, "check", "--include-dir", str(self.include),
                             "--repo", str(self.mirror.repo), "--require-guard"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        (self.include / "pulp/render/gpu_surface.hpp").write_text(SURFACE)
        bad = subprocess.run([sys.executable, script, "check", "--include-dir", str(self.include),
                              "--repo", str(self.mirror.repo), "--require-guard"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("with the guard line", bad.stderr)


@unittest.skipUnless(shutil.which("c++"), "no C++ compiler")
class GuardCompileTest(unittest.TestCase):
    """The installed guard as a compiler sees it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mirror = Mirror(self.root)
        plan = guard.write_guard(self.mirror.repo, self.root / "generated")
        self.include = self.mirror.install(self.root / "prefix", plan)
        # The forwarded gpu_runtime header the choke headers name; any content will do.
        (self.root / "gpu_runtime_stub/gpu_runtime").mkdir(parents=True)
        (self.root / "gpu_runtime_stub/gpu_runtime/gpu_surface.hpp").write_text("#pragma once\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def compile(self, header: str, *defines: str) -> subprocess.CompletedProcess:
        source = self.root / "consumer.cpp"
        source.write_text(f"#include <{header}>\nint main() {{ return 0; }}\n")
        return subprocess.run(
            ["c++", "-std=c++20", "-fsyntax-only", "-DPULP_GPU_RUNTIME=1", *defines,
             "-I", str(self.include), "-I", str(self.root / "gpu_runtime_stub"), str(source)],
            capture_output=True, text=True)

    def test_consumer_including_window_host_fails_with_the_guard(self) -> None:
        result = self.compile("pulp/view/window_host.hpp")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(guard.UNSUPPORTED_MESSAGE, result.stderr)

    def test_consumer_including_gpu_surface_fails_with_the_guard(self) -> None:
        result = self.compile("pulp/render/gpu_surface.hpp")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(guard.UNSUPPORTED_MESSAGE, result.stderr)

    def test_unrelated_header_still_compiles(self) -> None:
        result = self.compile("pulp/canvas/canvas.hpp")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mirror_build_definition_lifts_the_guard(self) -> None:
        result = self.compile("pulp/view/window_host.hpp", "-DVELLUM_MIRROR_BUILD=1")
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(shutil.which("cmake"), "no cmake")
class InstallExcludeTest(unittest.TestCase):
    """VellumMirror.cmake's install(DIRECTORY ... REGEX ... EXCLUDE) with the plan's regex."""

    def test_only_the_named_header_is_excluded(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import mirror_package  # noqa: E402
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src/include"
            for rel in ("pulp/render/gpu_surface.hpp", "pulp/render/gpu_surface.hpp.bak",
                        "pulp/other/gpu_surface.hpp", "pulp/render/skia_surface.hpp"):
                (src / rel).parent.mkdir(parents=True, exist_ok=True)
                (src / rel).write_text(rel)
            regex = mirror_package.cmake_path_suffix_regex("pulp/render/gpu_surface.hpp")
            script = root / "install.cmake"
            script.write_text(textwrap.dedent(f"""\
                file(INSTALL "{src}/" DESTINATION "{root}/out"
                    REGEX {mirror_package.cmake_quote(regex)} EXCLUDE)
                """))
            subprocess.run(["cmake", "-P", str(script)], check=True, capture_output=True)
            installed = sorted(p.relative_to(root / "out").as_posix()
                               for p in (root / "out").rglob("*") if p.is_file())
            self.assertEqual(installed, ["pulp/other/gpu_surface.hpp",
                                         "pulp/render/gpu_surface.hpp.bak",
                                         "pulp/render/skia_surface.hpp"])


if __name__ == "__main__":
    unittest.main()
