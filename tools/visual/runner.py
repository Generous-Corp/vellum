#!/usr/bin/env python3
"""Golden lifecycle runner for visual proof.

A fixture is a JSON file under ``<visual-root>/fixtures/<surface>/<entry>.json``.
Its golden sits at ``<visual-root>/goldens/<surface>/<entry>.png`` (or ``.json``).
``--generate`` writes goldens, ``--verify`` (the default) compares fresh
captures against them, and a failed comparison leaves the actual capture, a
diff image and a three-up montage in ``--actuals-dir`` plus a machine-readable
``--report``.

Fixture shape::

    {
      "id": "controls/knob-default",            # optional, defaults to surface/entry
      "capture": {
        "format": "png" | "json",               # default png
        "driver": "file" | "command",           # default file
        "path": "relative/to/repo/root.png",    # file driver
        "argv": ["python3", "tools/x.py"]       # command driver, stdout is the capture
      },
      "tolerance": {
        "channel": 0,        # png: per-channel distance that makes a pixel differ
        "similarity": 1.0,   # png: required share of matching pixels
        "semantic": 0.001    # json: numeric tolerance
      },
      "content_floor": false  # png: also reject a stable empty frame
    }

Defaults are exact (channel 0, similarity 1.0). A looser comparison has to be
written into the fixture, where review can see it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import differ  # noqa: E402
from vellum_png import PngError, decode_png_bytes, encode_png, montage  # noqa: E402

REPORT_SCHEMA = "vellum.visual-report.v1"
COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_SURFACE_NAME = "default"


class RunnerError(Exception):
    pass


@dataclass(frozen=True)
class Fixture:
    id: str
    surface: str
    entry: str
    path: Path
    capture_format: str
    driver: str
    capture: dict[str, Any]
    channel_tolerance: int
    similarity: float
    semantic_tolerance: float
    content_floor: bool

    @property
    def suffix(self) -> str:
        return ".png" if self.capture_format == "png" else ".json"


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for _ in range(12):
        if (current / "tools" / "visual").is_dir() and (current / "cli").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return start.resolve()


def fixtures_dir(visual_root: Path, surface: str) -> Path:
    return visual_root / "fixtures" / surface


def goldens_dir(visual_root: Path, surface: str) -> Path:
    return visual_root / "goldens" / surface


def list_fixtures(visual_root: Path, surface: str) -> list[Path]:
    root = fixtures_dir(visual_root, surface)
    return sorted(root.glob("*.json")) if root.is_dir() else []


def load_fixture(path: Path, *, surface: str | None = None) -> Fixture:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RunnerError(f"fixture {path} is unreadable: {error}") from error
    if not isinstance(raw, dict):
        raise RunnerError(f"fixture {path} must contain a JSON object")
    surface = str(surface or path.parent.name or DEFAULT_SURFACE_NAME)
    entry = path.stem
    fixture_id = str(raw.get("id") or f"{surface}/{entry}")
    capture = raw.get("capture")
    if not isinstance(capture, dict):
        raise RunnerError(f"{fixture_id}: fixture needs a capture object")
    capture_format = str(capture.get("format", "png")).lower()
    if capture_format not in {"png", "json"}:
        raise RunnerError(f"{fixture_id}: unsupported capture format {capture_format!r}")
    driver = str(capture.get("driver", "file"))
    if driver == "file" and not isinstance(capture.get("path"), str):
        raise RunnerError(f"{fixture_id}: file driver needs capture.path")
    argv = capture.get("argv")
    if driver == "command" and not (
        isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv)
    ):
        raise RunnerError(f"{fixture_id}: command driver needs a non-empty capture.argv list")
    if driver not in {"file", "command"}:
        raise RunnerError(f"{fixture_id}: unsupported capture driver {driver!r}")
    tolerance = raw.get("tolerance") if isinstance(raw.get("tolerance"), dict) else {}
    channel = tolerance.get("channel", 0)
    similarity = tolerance.get("similarity", 1.0)
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255:
        raise RunnerError(f"{fixture_id}: tolerance.channel must be an integer from 0 to 255")
    if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) or not 0 < similarity <= 1:
        raise RunnerError(f"{fixture_id}: tolerance.similarity must be above 0 and at most 1")
    return Fixture(
        id=fixture_id, surface=surface, entry=entry, path=path, capture_format=capture_format,
        driver=driver, capture=capture, channel_tolerance=channel, similarity=float(similarity),
        semantic_tolerance=differ.tolerance_from_fixture(raw), content_floor=bool(raw.get("content_floor")),
    )


def resolve_fixtures(visual_root: Path, surface: str, entries: Iterable[str] | None) -> list[Path]:
    available = list_fixtures(visual_root, surface)
    by_name = {path.stem: path for path in available}
    by_surface_name = {f"{surface}/{path.stem}": path for path in available}
    selected = list(entries or [])
    if not selected:
        return available
    chosen: list[Path] = []
    missing: list[str] = []
    for item in selected:
        if item in by_name:
            chosen.append(by_name[item])
        elif item in by_surface_name:
            chosen.append(by_surface_name[item])
        else:
            missing.append(item)
    if missing:
        raise RunnerError(
            f"unknown visual fixture(s): {', '.join(missing)} (known: {', '.join(sorted(by_surface_name))})"
        )
    return chosen


def golden_path(visual_root: Path, fixture: Fixture) -> Path:
    return goldens_dir(visual_root, fixture.surface) / f"{fixture.entry}{fixture.suffix}"


def orphaned_goldens(visual_root: Path, surface: str) -> list[Path]:
    """Goldens whose fixture is gone; they would otherwise rot unnoticed."""
    expected = {
        golden_path(visual_root, load_fixture(path, surface=surface)).resolve()
        for path in list_fixtures(visual_root, surface)
    }
    root = goldens_dir(visual_root, surface)
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir()
        if path.suffix in {".png", ".json"} and path.resolve() not in expected
    )


def capture(repo_root: Path, fixture: Fixture) -> bytes:
    if fixture.driver == "file":
        path = (repo_root / fixture.capture["path"]).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as error:
            raise RunnerError(f"{fixture.id}: capture path escapes the repository root") from error
        try:
            return path.read_bytes()
        except OSError as error:
            raise RunnerError(f"{fixture.id}: capture file is unreadable: {error}") from error
    try:
        completed = subprocess.run(
            fixture.capture["argv"], cwd=repo_root, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunnerError(f"{fixture.id}: capture command failed to run: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RunnerError(f"{fixture.id}: capture command exited {completed.returncode}: {detail}")
    return completed.stdout


def _normalize(fixture: Fixture, payload: bytes) -> bytes:
    """Validate a capture and return the bytes a golden stores."""
    if fixture.capture_format == "png":
        try:
            decode_png_bytes(payload)
        except PngError as error:
            raise RunnerError(f"{fixture.id}: capture is not a valid PNG: {error}") from error
        return payload
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RunnerError(f"{fixture.id}: capture is not valid JSON: {error}") from error
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def generate(repo_root: Path, visual_root: Path, surface: str, fixtures: list[Path]) -> int:
    for fixture_path in fixtures:
        fixture = load_fixture(fixture_path, surface=surface)
        payload = _normalize(fixture, capture(repo_root, fixture))
        if fixture.content_floor and fixture.capture_format == "png":
            stats = differ.content_stats(payload)
            if not stats.passes_content_floor():
                raise RunnerError(f"{fixture.id}: refusing to record an empty-looking golden")
        target = golden_path(visual_root, fixture)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        print(f"generated {target.relative_to(repo_root) if target.is_relative_to(repo_root) else target}")
    return 0


def _write_failure_artifacts(actuals_dir: Path | None, fixture: Fixture, golden: bytes,
                             actual: bytes, diff_png: bytes | None = None,
                             montage_png: bytes | None = None) -> dict[str, str]:
    if actuals_dir is None:
        return {}
    base = actuals_dir / fixture.surface
    base.mkdir(parents=True, exist_ok=True)
    written = {"actual": str(base / f"{fixture.entry}.actual{fixture.suffix}")}
    Path(written["actual"]).write_bytes(actual)
    if diff_png is not None:
        written["diff"] = str(base / f"{fixture.entry}.diff.png")
        Path(written["diff"]).write_bytes(diff_png)
    if montage_png is not None:
        written["montage"] = str(base / f"{fixture.entry}.montage.png")
        Path(written["montage"]).write_bytes(montage_png)
    return written


def _verify_png(fixture: Fixture, golden: bytes, actual: bytes,
                actuals_dir: Path | None) -> dict[str, Any]:
    result = differ.compare_pixels(golden, actual, fixture.channel_tolerance)
    entry: dict[str, Any] = {"id": fixture.id, "format": "png", "comparison": result.as_report()}
    problems: list[str] = []
    if not result.valid:
        problems.append(result.error)
    else:
        if not result.same_size:
            problems.append("capture dimensions differ from the golden")
        if result.similarity < fixture.similarity:
            problems.append(
                f"similarity {result.similarity:.6f} is below {fixture.similarity:g}: "
                f"{result.diff_pixels} of {result.total_pixels} pixels differ beyond channel "
                f"tolerance {fixture.channel_tolerance}"
            )
    if fixture.content_floor:
        stats = differ.content_stats(actual)
        entry["content"] = {
            "unique_colors": stats.unique_colors,
            "non_background_coverage": stats.non_background_coverage,
        }
        if not stats.passes_content_floor():
            problems.append("capture is a stable empty frame (fails the content floor)")
    if problems:
        diff_png = montage_png = None
        if result.valid:
            bounds = differ.diff_bounds(golden, actual, fixture.channel_tolerance)
            if bounds.valid:
                entry["bounds"] = {"x": bounds.x, "y": bounds.y, "width": bounds.width,
                                   "height": bounds.height}
            diff = differ.diff_image(golden, actual, fixture.channel_tolerance)
            diff_png = encode_png(diff)
            montage_png = encode_png(montage([decode_png_bytes(golden), decode_png_bytes(actual), diff],
                                             columns=3))
        entry["artifacts"] = _write_failure_artifacts(actuals_dir, fixture, golden, actual,
                                                      diff_png, montage_png)
        entry["problems"] = problems
    entry["status"] = "failed" if problems else "passed"
    return entry


def _verify_json(fixture: Fixture, golden: bytes, actual: bytes,
                 actuals_dir: Path | None) -> dict[str, Any]:
    differences = differ.compare(json.loads(golden.decode("utf-8")),
                                 json.loads(actual.decode("utf-8")),
                                 tolerance=fixture.semantic_tolerance)
    entry: dict[str, Any] = {"id": fixture.id, "format": "json", "difference_count": len(differences)}
    if differences:
        entry["problems"] = differ.format_differences(differences).splitlines()
        entry["artifacts"] = _write_failure_artifacts(actuals_dir, fixture, golden, actual)
    entry["status"] = "failed" if differences else "passed"
    return entry


def verify(repo_root: Path, visual_root: Path, surface: str, fixtures: list[Path], *,
           actuals_dir: Path | None = None) -> tuple[int, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    for fixture_path in fixtures:
        fixture = load_fixture(fixture_path, surface=surface)
        actual = _normalize(fixture, capture(repo_root, fixture))
        golden_file = golden_path(visual_root, fixture)
        if not golden_file.is_file():
            artifacts = _write_failure_artifacts(actuals_dir, fixture, b"", actual)
            results.append({"id": fixture.id, "status": "failed", "artifacts": artifacts,
                            "problems": [f"missing golden {golden_file.name}; generate it explicitly"]})
            continue
        golden = golden_file.read_bytes()
        try:
            results.append(
                _verify_png(fixture, golden, actual, actuals_dir) if fixture.capture_format == "png"
                else _verify_json(fixture, golden, actual, actuals_dir)
            )
        except PngError as error:
            results.append({"id": fixture.id, "status": "failed",
                            "problems": [f"golden is not a valid PNG: {error}"]})
    for orphan in orphaned_goldens(visual_root, surface):
        results.append({"id": f"{surface}/{orphan.stem}", "status": "failed",
                        "problems": [f"orphaned golden {orphan.name} has no fixture"]})
    failed = [result for result in results if result["status"] != "passed"]
    for result in results:
        if result["status"] == "passed":
            print(f"ok {result['id']}")
    for result in failed:
        print(f"FAILED {result['id']}\n  " + "\n  ".join(result["problems"]), file=sys.stderr)
    if failed:
        print(f"regenerate deliberately with: runner.py --generate --surface {surface} --entry NAME",
              file=sys.stderr)
    return (1 if failed else 0), results


def build_report(surface_results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    flat = [result for results in surface_results.values() for result in results]
    return {
        "schema": REPORT_SCHEMA,
        "passed": all(result["status"] == "passed" for result in flat),
        "totals": {
            "fixtures": len(flat),
            "passed": sum(1 for result in flat if result["status"] == "passed"),
            "failed": sum(1 for result in flat if result["status"] != "passed"),
        },
        "surfaces": surface_results,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or verify visual goldens.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true", help="Write goldens from fresh captures.")
    mode.add_argument("--verify", action="store_true", help="Compare captures to goldens (default).")
    parser.add_argument("--surface", action="append", default=None, help="Surface to run; repeatable.")
    parser.add_argument("--entry", action="append", default=None, help="Fixture entry; repeatable.")
    parser.add_argument("--all", action="store_true", help="Every fixture; required with --generate.")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--visual-root", type=Path, default=None,
                        help="Directory holding fixtures/ and goldens/; default <repo>/tools/visual.")
    parser.add_argument("--actuals-dir", type=Path, default=None,
                        help="Where failed captures, diffs and montages are written.")
    parser.add_argument("--report", type=Path, default=None, help="Write a JSON report here.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = (args.repo_root or find_repo_root(Path.cwd())).resolve()
    visual_root = (args.visual_root or repo_root / "tools" / "visual").resolve()
    if args.all and args.entry:
        print("error: pass --all or --entry, not both", file=sys.stderr)
        return 2
    if args.generate and not (args.all or args.entry):
        print("error: --generate needs --all or --entry NAME, so no golden is rewritten by accident",
              file=sys.stderr)
        return 2
    if args.generate and args.all and args.report:
        print("error: --report describes verification, not generation", file=sys.stderr)
        return 2
    actuals_dir = args.actuals_dir.resolve() if args.actuals_dir else None
    surface_results: dict[str, list[dict[str, Any]]] = {}
    status = 0
    try:
        plan = []
        for surface in args.surface or ["default"]:
            fixtures = resolve_fixtures(visual_root, surface, args.entry)
            if not fixtures:
                print(f"error: no visual fixtures for surface {surface!r}", file=sys.stderr)
                return 2
            plan.append((surface, fixtures))
        for surface, fixtures in plan:
            if args.generate:
                status = max(status, generate(repo_root, visual_root, surface, fixtures))
            else:
                code, results = verify(repo_root, visual_root, surface, fixtures, actuals_dir=actuals_dir)
                surface_results[surface] = results
                status = max(status, code)
    except (RunnerError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.report and not args.generate:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(build_report(surface_results), indent=2) + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
