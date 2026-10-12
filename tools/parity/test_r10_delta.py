from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_harness as ph
import test_parity_harness as base


class R10DeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="r10-reg-")
        self.root = Path(self.tmp.name)
        self.tool = self.root / "fake.py"
        self.tool.write_text(base.FAKE_TOOL.format(cli_dir=str(base.CLI_DIR), ignore_input=False))
        (self.root / "src").mkdir()
        (self.root / "src/source.json").write_text(json.dumps(base.SOURCE))
        (self.root / "src/assets").mkdir()
        (self.root / "src/assets/tint.txt").write_text("warm")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_copy(self, source: str, destination: str = "out/copied.json", artifact: str | None = None,
                 recursive: bool = False) -> dict:
        impl = base.implementation(self.tool)
        impl["steps"] += [
            {"id": "mkdir", "argv": ["/bin/mkdir", "-p", "out", "copydest", "sub"]},
            {"id": "touch", "argv": ["/usr/bin/touch", "made.json", "sub/nested.json"]},
            {"id": "copy", "argv": ["/bin/cp", *( ["-R"] if recursive else []), source, destination]},
        ]
        case = base.corpus()["cases"][0] | {"_corpus_dir": str(self.root)}
        ph.LANE_ARTIFACTS["delta"] = (artifact or destination,)
        try:
            return ph.run_side(case, impl, {"fake": self.tool}, self.root / "src", self.root / "side")
        finally:
            ph.LANE_ARTIFACTS.pop("delta", None)

    def test_f1_source_spellings_and_f3_directories_are_recorded(self) -> None:
        for spelling in ("./made.json", str(self.root / "side/made.json"), "sub/../made.json"):
            run = self.run_copy(spelling)
            self.assertEqual(run["produced_by"]["out/copied.json"]["reads"], ["made.json"])
        run = self.run_copy("sub", "copydest", "copydest/sub/nested.json", recursive=True)
        self.assertEqual(run["produced_by"]["copydest/sub/nested.json"]["reads"], ["sub/nested.json"])

    def test_f2_and_f8_passthrough_cannot_produce_selected_outputs(self) -> None:
        impl = base.implementation(self.tool)
        impl["passthrough_binaries"] = [{"path": "/bin/cp", "sha256": ph.sha256_file(Path("/bin/cp")),
                                         "justification": "copy staged input"}]
        ph.LANE_ARTIFACTS["delta"] = ("render.png",)
        try:
            case = base.corpus()["cases"][0] | {"_corpus_dir": str(self.root)}
            impl["steps"].append({"id": "copy", "argv": ["/bin/cp", "source.json", "render.png"]})
            run = ph.run_side(case, impl, {"fake": self.tool}, self.root / "src", self.root / "side")
            identity = {"tools": {"fake": {"sha256": ph.sha256_file(self.tool)}},
                        "passthrough_binaries": impl["passthrough_binaries"]}
            self.assertTrue(ph.provenance_errors(run, identity, "x", ["delta"]))
            bad = dict(impl, passthrough_binaries=[{"path": sys.executable,
                                                     "sha256": ph.sha256_file(Path(sys.executable)),
                                                     "justification": "bad"}])
            with self.assertRaises(ph.HarnessError):
                ph.validate_implementation(bad)
        finally:
            ph.LANE_ARTIFACTS.pop("delta", None)

    def test_f4_hashes_script_from_execution_directory(self) -> None:
        side = self.root / "side"
        side.mkdir()
        (self.root / "declared.py").write_text("print('declared')")
        (side / "gen.py").write_text("print('forged')")
        info = ph.producing_binary([sys.executable, "gen.py"], side)
        self.assertEqual(info["script_sha256"], ph.sha256_file(side / "gen.py"))
        self.assertNotEqual(info["script_sha256"], ph.sha256_file(self.root / "declared.py"))

    def test_f5_script_and_module_delegation_is_marked(self) -> None:
        script = self.root / "writer.sh"
        script.write_text("#!/bin/sh\ntrue\n")
        self.assertTrue(ph.producing_binary(["/bin/sh", str(script)]).get("delegated"))
        self.assertTrue(ph.producing_binary([sys.executable, "-m", "http.server"]).get("delegated"))

    def test_f6_staging_rechecks_after_copy(self) -> None:
        outside = self.root / "outside.json"
        outside.write_bytes(b"outside")
        source = self.root / "src/source.json"
        original = ph.resolve_under
        def racing(root: Path, rel: str, label: str) -> Path:
            result = original(root, rel, label)
            if rel == "source.json" and source.exists() and not source.is_symlink():
                source.unlink()
                source.symlink_to(outside)
            return result
        ph.resolve_under = racing
        try:
            case = base.corpus()["cases"][0] | {"_corpus_dir": str(self.root)}
            with self.assertRaises(ph.HarnessError):
                ph.stage_case(case, self.root / "src", self.root / "staged")
        finally:
            ph.resolve_under = original

    def test_r8_regular_source_pin_is_checked(self) -> None:
        case = base.corpus()["cases"][0] | {"_corpus_dir": str(self.root),
                                             "provenance": {"license": "MIT", "repository": "x/y",
                                             "commit": "a" * 40, "sha256": "0" * 64}}
        with self.assertRaises(ph.HarnessError):
            ph.stage_case(case, self.root / "src", self.root / "pinned")

    def test_f7_external_manifest_and_commit_are_required(self) -> None:
        ext = self.root / "external"
        ext.mkdir()
        (ext / "source.json").write_text("{}")
        (ext / "assets").mkdir()
        (ext / "assets/a.bin").write_bytes(b"a")
        subprocess.run(["git", "init", "-q", str(ext)], check=True)
        subprocess.run(["git", "-C", str(ext), "add", "."], check=True)
        subprocess.run(["git", "-C", str(ext), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(ext), "rev-parse", "HEAD"], text=True).strip()
        files = {"source.json": ph.sha256_file(ext / "source.json"),
                 "assets/a.bin": ph.sha256_file(ext / "assets/a.bin")}
        case = {"case_id": "ext", "source": "source.json", "source_kind": "fake-json",
                "source_base": "external:tp", "stage": ["assets"], "width": 1, "height": 1,
                "lanes": [], "provenance": {"license": "MIT", "repository": "x/y",
                "commit": commit, "sha256": files["source.json"], "files": files},
                "_external_roots": {"tp": str(ext)}}
        self.assertEqual(ph.stage_case(case, self.root / "src", self.root / "staged"), files)
        bad = dict(case, provenance=dict(case["provenance"], commit="a" * 40))
        with self.assertRaises(ph.HarnessError):
            ph.stage_case(bad, self.root / "src", self.root / "bad")

    def test_s1_in_root_symlink_and_s2_non_string_region_are_rejected(self) -> None:
        (self.root / "src/link").symlink_to(self.root / "src/source.json")
        case = base.corpus()["cases"][0] | {"source": "link", "_corpus_dir": str(self.root)}
        with self.assertRaises(ph.HarnessError):
            ph.stage_case(case, self.root / "src", self.root / "staged")
        bad = base.corpus()["cases"][0] | {"regions": [{"name": 1, "x": 0, "y": 0, "w": 1, "h": 1}]}
        with self.assertRaises(ph.HarnessError):
            ph.validate_corpus(base.corpus([bad]))


if __name__ == "__main__":
    unittest.main()
