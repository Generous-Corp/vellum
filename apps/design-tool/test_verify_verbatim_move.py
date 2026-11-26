"""The verbatim-move check, on synthetic files and a throwaway Pulp repository.

The last case runs the check on the real manifests in apps/design-tool/moves/.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("verify_verbatim_move", HERE / "verify_verbatim_move.py")
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

PULP_TEXT = b"// sample\nvar cpp = 'pulp::Color(' + hex + ')';\nvar n = 'pulp_theme';\n"
MOVED_TEXT = b"// sample\nvar cpp = 'vellum::Color(' + hex + ')';\nvar n = 'vellum_theme';\n"
RESPELL = [{"old": "pulp::", "new": "vellum::", "count": 1},
           {"old": "pulp_theme", "new": "vellum_theme", "count": 1}]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True,
                          check=True).stdout.strip()


class VerbatimMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "vellum"
        self.pulp = Path(self.temp.name) / "pulp"
        (self.root / "apps/design-tool").mkdir(parents=True)
        (self.pulp / "examples/design-tool").mkdir(parents=True)
        (self.pulp / "examples/design-tool/tool.js").write_bytes(PULP_TEXT)
        git(self.pulp, "init", "-q")
        git(self.pulp, "add", "-A")
        git(self.pulp, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "-m", "fixture")
        self.commit = git(self.pulp, "rev-parse", "HEAD")
        self.moved = self.root / "apps/design-tool/tool.js"
        self.moved.write_bytes(MOVED_TEXT)
        self.manifest = Path(self.temp.name) / "move.json"
        self.write_manifest(RESPELL)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, respell: list[dict], blob: str | None = None) -> None:
        self.manifest.write_text(json.dumps({
            "schema": check.SCHEMA, "source_commit": self.commit,
            "files": [{"path": "apps/design-tool/tool.js", "source_path": "examples/design-tool/tool.js",
                       "source_blob": blob or check.git_blob_id(PULP_TEXT), "respell": respell}],
        }), encoding="utf-8")

    def run_check(self, *extra: str) -> int:
        return check.main(["--root", str(self.root), *extra, str(self.manifest)])

    def test_exact_move_with_listed_respelling_passes(self) -> None:
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(self.run_check("--pulp-repo", str(self.pulp)), 0)

    def test_one_changed_character_fails(self) -> None:
        self.moved.write_bytes(MOVED_TEXT.replace(b"sample", b"sampLe"))
        self.assertEqual(self.run_check(), 1)

    def test_unlisted_respelling_fails(self) -> None:
        self.write_manifest(RESPELL[:1])
        self.assertEqual(self.run_check(), 1)

    def test_wrong_respelling_count_fails(self) -> None:
        self.write_manifest([dict(RESPELL[0], count=2), RESPELL[1]])
        self.assertEqual(self.run_check(), 1)

    def test_recorded_blob_must_match_pulp(self) -> None:
        wrong = check.git_blob_id(PULP_TEXT + b"\n")
        self.write_manifest(RESPELL, blob=wrong)
        self.assertEqual(self.run_check("--pulp-repo", str(self.pulp)), 1)

    def test_respelling_already_in_pulp_is_refused(self) -> None:
        text = PULP_TEXT + b"// vellum_theme\n"
        (self.pulp / "examples/design-tool/tool.js").write_bytes(text)
        git(self.pulp, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-qam", "second")
        self.commit = git(self.pulp, "rev-parse", "HEAD")
        self.moved.write_bytes(MOVED_TEXT + b"// vellum_theme\n")
        self.write_manifest([RESPELL[0], dict(RESPELL[1], count=2)], blob=check.git_blob_id(text))
        self.assertEqual(self.run_check("--pulp-repo", str(self.pulp)), 1)

    def test_malformed_manifest_is_a_usage_error(self) -> None:
        self.manifest.write_text('{"schema": "other"}', encoding="utf-8")
        self.assertEqual(self.run_check(), 2)

    def test_checked_in_manifests_verify(self) -> None:
        self.assertEqual(check.main([]), 0)


if __name__ == "__main__":
    unittest.main()
