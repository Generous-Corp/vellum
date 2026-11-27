"""The source-support manifest, the CLI import surface and the installed journeys agree."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "product/source-support.yaml"
sys.path.insert(0, str(ROOT / "cli"))
import vellum_cli as cli  # noqa: E402

AVAILABLE = {"supported", "experimental"}
JOURNEY = re.compile(r"(?P<path>[^:\s]+)::(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<lane>[a-z0-9-]+)\])?")


def read_sources(text: str) -> dict[str, dict[str, str]]:
    """Read the scalar fields of each entry under `sources:`."""
    sources: dict[str, dict[str, str]] = {}
    current: str | None = None
    inside = False
    for line in text.splitlines():
        if line == "sources:":
            inside = True
            continue
        if inside and line and not line.startswith(" "):
            break
        if not inside:
            continue
        entry = re.fullmatch(r"  ([a-z0-9-]+):", line)
        if entry:
            current = entry.group(1)
            sources[current] = {}
            continue
        field = re.fullmatch(r"    ([a-z_]+): (.+)", line)
        if field and current:
            sources[current][field.group(1)] = field.group(2).strip().strip('"')
    return sources


def cli_import_sources() -> set[str]:
    """Every source name the public `vellum import` parser accepts."""
    parser = cli.parser()
    commands = next(action for action in parser._actions
                    if isinstance(action, argparse._SubParsersAction))
    importer = commands.choices["import"]
    source_type = next(action for action in importer._actions
                       if "--source-type" in action.option_strings)
    return (set(source_type.choices) | set(cli.IMPORT_SHORTHAND_SOURCES)
            | set(cli.IMPORT_CONVERTER_SOURCES))


def journey_problems(name: str, reference: str, root: Path) -> list[str]:
    match = JOURNEY.fullmatch(reference)
    if not match:
        return [f"{name}: journey '{reference}' is not path::function[lane]"]
    path = root / match["path"]
    if not path.is_file():
        return [f"{name}: journey file {match['path']} does not exist"]
    text = path.read_text(encoding="utf-8")
    function = match["name"]
    problems = []
    if not re.search(rf"(?m)^\s*def {function}\(", text):
        problems.append(f"{name}: {match['path']} defines no {function}()")
    elif not function.startswith("test_") and function != "validate" and len(
        re.findall(rf"\b{function}\(", text)
    ) < 2:
        problems.append(f"{name}: {function}() is defined but never called")
    if match["lane"] and f'"{match["lane"]}"' not in text:
        problems.append(f"{name}: {match['path']} never names the lane '{match['lane']}'")
    return problems


def drift(sources: dict[str, dict[str, str]], accepted: set[str], root: Path) -> list[str]:
    problems = [f"{name}: accepted by vellum import but missing from the manifest"
                for name in sorted(accepted - set(sources))]
    for name, fields in sorted(sources.items()):
        channel = fields.get("channel")
        if channel in AVAILABLE:
            if "journey" not in fields:
                problems.append(f"{name}: {channel} source names no journey")
            else:
                problems.extend(journey_problems(name, fields["journey"], root))
        elif channel == "unavailable":
            if not any(key in fields for key in ("reason", "note", "preflight_contract")):
                problems.append(f"{name}: unavailable source states no reason")
            if name in accepted and not any(key in fields for key in ("reason", "preflight_contract")):
                problems.append(f"{name}: unavailable but accepted by vellum import without a reason")
        else:
            problems.append(f"{name}: unknown channel {channel!r}")
        if name not in accepted and channel != "unavailable":
            problems.append(f"{name}: {channel} in the manifest but vellum import does not accept it")
    return problems


class SourceSupportManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = read_sources(MANIFEST.read_text(encoding="utf-8"))
        self.accepted = cli_import_sources()

    def test_manifest_cli_and_journeys_agree(self) -> None:
        self.assertEqual(drift(self.sources, self.accepted, ROOT), [])

    def test_a_cli_source_missing_from_the_manifest_is_drift(self) -> None:
        sources = dict(self.sources)
        sources.pop("jsx")
        self.assertIn("jsx: accepted by vellum import but missing from the manifest",
                      drift(sources, self.accepted, ROOT))

    def test_an_available_source_without_a_journey_is_drift(self) -> None:
        sources = {name: dict(fields) for name, fields in self.sources.items()}
        sources["pencil"].pop("journey")
        self.assertIn("pencil: experimental source names no journey",
                      drift(sources, self.accepted, ROOT))

    def test_a_journey_that_does_not_resolve_is_drift(self) -> None:
        sources = {name: dict(fields) for name, fields in self.sources.items()}
        sources["v0"]["journey"] = "scripts/validate_installed_sdk.py::validate_missing_lane"
        self.assertIn("v0: scripts/validate_installed_sdk.py defines no validate_missing_lane()",
                      drift(sources, self.accepted, ROOT))
        sources["v0"]["journey"] = "scripts/validate_installed_sdk.py::validate_installed_frame_import[nope]"
        self.assertIn("v0: scripts/validate_installed_sdk.py never names the lane 'nope'",
                      drift(sources, self.accepted, ROOT))

    def test_an_unavailable_source_needs_a_reason(self) -> None:
        sources = {name: dict(fields) for name, fields in self.sources.items()}
        sources["illustrator"].pop("reason")
        problems = drift(sources, self.accepted, ROOT)
        self.assertIn("illustrator: unavailable source states no reason", problems)
        self.assertIn("illustrator: unavailable but accepted by vellum import without a reason", problems)

    def test_a_manifest_only_source_must_be_unavailable(self) -> None:
        sources = {name: dict(fields) for name, fields in self.sources.items()}
        sources["react-project"]["channel"] = "experimental"
        sources["react-project"]["journey"] = self.sources["pencil"]["journey"]
        self.assertIn("react-project: experimental in the manifest but vellum import does not accept it",
                      drift(sources, self.accepted, ROOT))


if __name__ == "__main__":
    unittest.main()
