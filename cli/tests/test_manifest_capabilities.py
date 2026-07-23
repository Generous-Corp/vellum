from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cli.vellum_manifest import ManifestError, load_app_manifest

ROOT = Path(__file__).resolve().parents[2]


class ManifestCapabilitiesTests(unittest.TestCase):
    def project(self) -> Path:
        root = Path(tempfile.mkdtemp())
        template = (ROOT / "templates/basic/app.toml.template").read_text()
        (root / "app.toml").write_text(
            template.replace("{{PROJECT_NAME_JSON}}", "Capability Test")
            .replace("{{PROJECT_SLUG}}", "capability-test")
        )
        return root

    def test_versioned_defaults_validate(self) -> None:
        capabilities = load_app_manifest(self.project())["capabilities"]
        self.assertEqual(capabilities["commands"], "denied")
        self.assertEqual(capabilities["files"], "denied")
        self.assertEqual(capabilities["clipboard"], "denied")
        self.assertEqual(capabilities["open_url"], "denied")
        self.assertEqual(capabilities["persistence"], "denied")

    def test_each_service_accepts_its_version_and_explicit_unsupported(self) -> None:
        root = self.project()
        path = root / "app.toml"
        text = path.read_text()
        replacements = {
            'commands = "denied"': 'commands = "v1"',
            'files = "denied"': 'files = "user-selected-text-v1"',
            'clipboard = "denied"': 'clipboard = "text-v1"',
            'open_url = "denied"': 'open_url = "external-v1"',
            'persistence = "denied"': 'persistence = "state-v1"',
        }
        for before, after in replacements.items():
            text = text.replace(before, after)
        path.write_text(text)
        load_app_manifest(root)
        path.write_text(text.replace('commands = "v1"', 'commands = "unsupported"'))
        load_app_manifest(root)

    def test_old_boolean_and_unversioned_values_fail_closed(self) -> None:
        for before, invalid in (
            ('commands = "denied"', 'commands = true'),
            ('files = "denied"', 'files = "none"'),
            ('clipboard = "denied"', 'clipboard = true'),
            ('open_url = "denied"', 'open_url = "external"'),
            ('persistence = "denied"', 'persistence = "v1"'),
        ):
            root = self.project()
            path = root / "app.toml"
            path.write_text(path.read_text().replace(before, invalid))
            with self.subTest(invalid=invalid), self.assertRaises(ManifestError):
                load_app_manifest(root)


if __name__ == "__main__":
    unittest.main()
