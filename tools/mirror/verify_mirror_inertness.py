#!/usr/bin/env python3
"""Prove Pulp's two Phase-6 view-audio debts are inert in a Vellum package.

Pulp defers two compatibility debts in headers that would ship with the view
units (TEAM-LEAD RULING 2026-10-11, Phase-6-deferred compatibility debts):

- ``vellum-header-audio-widgets-include``: ``pulp/view/widgets.hpp`` includes
  the audio widgets only under ``PULP_VIEW_HAS_AUDIO_WIDGETS``, a definition
  that only Pulp's view-audio target publishes;
- ``design-import-audio-forward-decls``: ``pulp/view/design_import.hpp``
  forward-declares ``Meter``/``WaveformView`` and keeps deprecated bind
  virtuals, which must stay name-only.

Such a debt may be deferred only if a sterile consumer of the installed
package shows it inert. This checker reads a consumer that was configured
against the installed package and built with depfiles, and asserts:

1. ``no-widgets-gate-definition``: the guard is defined by no exported target
   property, no installed header, and no consumer compile command;
2. ``no-audio-headers``: no consumer translation unit includes a view-audio
   header, and the package ships none;
3. ``no-audio-symbols``: neither the consumer image nor any packaged library
   defines or references a view-audio symbol; while the package carries no
   view unit, no ``pulp::view`` symbol may appear at all;
4. ``name-only-forward-declarations``: no symbol of the forward-declared
   classes or the deprecated bind virtuals appears anywhere.

It also records whether each debt's header ships in the package. A run that
read nothing (no depfiles, no compile commands, an image without symbols) is
refused, so a broken instrument cannot read as a pass.

Exit codes: 0 inert, 1 a debt is not inert, 2 the evidence is unusable.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "vellum.mirror-inertness-proof.v1"
GUARD = "PULP_VIEW_HAS_AUDIO_WIDGETS"
DEBTS = {
    "vellum-header-audio-widgets-include": "pulp/view/widgets.hpp",
    "design-import-audio-forward-decls": "pulp/view/design_import.hpp",
}
# Headers of Pulp's view-audio target and its Pulp-kept siblings: the
# audio/ subtree, the meter/waveform/spectrum family and the named widgets.
AUDIO_HEADER = re.compile(
    r"(?:^|/)pulp/view/(?:audio/|(?:widgets/)?(?:"
    r"audio\w*|meter\w*|level_display\w*|spectrum\w*|visuali[sz]\w*|waveform\w*"
    r"|eq_curve_view|midi_\w+|musical_typing_keyboard\w*|plugin_manager_panel"
    r"|sample_asset_drop_target|step_grid_view|graph_editor_view"
    r"|register_audio_widgets|hosted_editor_attachment)\.h\w*\Z)"
)
VIEW_HEADER = re.compile(r"(?:^|/)pulp/view/")
# Demangled view-audio symbols: the widget classes and their registration.
AUDIO_SYMBOL = re.compile(
    r"\bpulp::view::(?:\w+::)*(?:"
    r"Meter|LevelDisplay|WaveformView|Waveform\w*|Spectrum\w*|Visualiz\w*"
    r"|EqCurveView|AudioBridge|AudioInspector\w*|MidiBinding|MidiKeyboard\w*"
    r"|MusicalTypingKeyboard\w*|StepGridView|GraphEditorView|PluginManagerPanel"
    r"|SampleAssetDropTarget|HostedEditorAttachment|register_audio_widgets"
    r"|bind_meter|bind_waveform_display)\b"
)
FORWARD_DECLARED = re.compile(r"\bpulp::view::(?:Meter|WaveformView|\w+::bind_meter|\w+::bind_waveform_display)\b")
VIEW_SYMBOL = re.compile(r"\bpulp::view::")


class EvidenceError(RuntimeError):
    pass


def compile_command_findings(commands: list[dict[str, Any]]) -> list[str]:
    findings = []
    pattern = re.compile(rf"-D\s*{GUARD}\b")
    for entry in commands:
        text = entry.get("command") or " ".join(entry.get("arguments", []))
        if pattern.search(text) or GUARD in text:
            findings.append(f"{entry.get('file')}: compile command defines or names {GUARD}")
    return findings


def package_definition_findings(package_files: Iterable[tuple[str, str]]) -> list[str]:
    findings = []
    define = re.compile(rf"^\s*#\s*define\s+{GUARD}\b", re.M)
    for name, text in package_files:
        if name.endswith(".cmake") and GUARD in text:
            findings.append(f"{name}: exported package names {GUARD}")
        elif define.search(text):
            findings.append(f"{name}: header defines {GUARD}")
    return findings


def depfile_includes(text: str) -> list[str]:
    """Paths a make-style depfile lists, with continuations joined."""
    joined = text.replace("\\\n", " ")
    _, _, deps = joined.partition(":")
    return [token.replace("\\ ", " ") for token in re.split(r"(?<!\\)\s+", deps.strip()) if token]


def ninja_deps(text: str) -> dict[str, list[str]]:
    """Parse `ninja -t deps`: an output line, then its indented inputs."""
    deps: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            if current is None:
                raise EvidenceError("ninja deps listing starts with an input")
            deps[current].append(line.strip())
        else:
            current = line.split(": #deps", 1)[0]
            if "(VALID)" not in line:
                raise EvidenceError(f"ninja deps for {current} are stale")
            deps[current] = []
    return deps


def consumer_dependencies(build: Path) -> dict[str, list[str]]:
    """Every header each consumer translation unit read, per object file.

    Ninja folds compiler depfiles into its deps log and deletes them, so a
    Ninja build is read through `ninja -t deps`; other generators leave the
    depfiles next to the objects.
    """
    if (build / ".ninja_deps").is_file():
        ninja = shutil.which("ninja")
        if not ninja:
            raise EvidenceError("the consumer was built with Ninja, but ninja is not on PATH")
        listed = subprocess.run([ninja, "-C", str(build), "-t", "deps"],
                                capture_output=True, text=True, check=False)
        if listed.returncode:
            raise EvidenceError(f"ninja -t deps failed: {listed.stderr.strip()}")
        return {name: inputs for name, inputs in ninja_deps(listed.stdout).items()
                if name.endswith(".o")}
    return {
        path.relative_to(build).as_posix(): depfile_includes(path.read_text(encoding="utf-8"))
        for path in sorted(build.rglob("*.o.d"))
    }


def header_findings(depfiles: dict[str, list[str]], package_headers: Iterable[str]) -> list[str]:
    findings = []
    for depfile, includes in sorted(depfiles.items()):
        for include in includes:
            if AUDIO_HEADER.search(include):
                findings.append(f"{depfile}: consumer includes view-audio header {include}")
    for header in package_headers:
        if AUDIO_HEADER.search(header):
            findings.append(f"package ships view-audio header {header}")
    return findings


def symbol_findings(symbols: dict[str, list[str]], *, view_unit_packaged: bool) -> tuple[list[str], list[str]]:
    audio, forward = [], []
    for image, names in sorted(symbols.items()):
        for name in names:
            if AUDIO_SYMBOL.search(name) or (not view_unit_packaged and VIEW_SYMBOL.search(name)):
                audio.append(f"{image}: {name}")
            if FORWARD_DECLARED.search(name):
                forward.append(f"{image}: {name}")
    return audio, forward


def image_symbols(path: Path) -> list[str]:
    nm = shutil.which("nm")
    filt = shutil.which("c++filt")
    if not nm or not filt:
        raise EvidenceError("nm and c++filt are required")
    # -j lists every defined and undefined symbol name, one per line.
    listed = subprocess.run([nm, "-j", str(path)], capture_output=True, text=True, check=False)
    if listed.returncode:
        raise EvidenceError(f"nm failed on {path}: {listed.stderr.strip()}")
    names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    demangled = subprocess.run([filt, "-n"], input="\n".join(names), capture_output=True, text=True, check=False)
    if demangled.returncode:
        raise EvidenceError("c++filt failed")
    return [line for line in demangled.stdout.splitlines() if line]


def verify(prefix: Path, consumer_build: Path, image: Path) -> dict[str, Any]:
    mirror = prefix / "mirror"
    if not (mirror / "lib/cmake/VellumMirror").is_dir():
        raise EvidenceError(f"{prefix} has no packaged mirror units")
    receipts = sorted((mirror / "share/provenance").glob("*.json"))
    if len(receipts) != 1:
        raise EvidenceError("the package must carry exactly one export receipt")
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    units = sorted(unit["name"] for unit in receipt.get("units", []))
    view_unit_packaged = any(name.startswith("view") for name in units)

    commands_path = consumer_build / "compile_commands.json"
    try:
        commands = json.loads(commands_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"consumer compile commands are unreadable: {error}") from error
    if not commands:
        raise EvidenceError("consumer has no compile commands")
    depfiles = consumer_dependencies(consumer_build)
    if len(depfiles) < len(commands) or not all(depfiles.values()):
        raise EvidenceError("not every consumer translation unit left a depfile")
    if not any("pulp/" in include for includes in depfiles.values() for include in includes):
        raise EvidenceError("consumer depfiles name no packaged header; the consumer did not use the package")

    package_files = []
    package_headers = []
    for path in sorted(item for item in prefix.rglob("*") if item.is_file()):
        relative = path.relative_to(prefix).as_posix()
        if path.suffix in {".cmake", ".h", ".hh", ".hpp", ".hxx", ".inc", ".ipp"}:
            package_files.append((relative, path.read_text(encoding="utf-8", errors="replace")))
        if path.suffix in {".h", ".hh", ".hpp", ".hxx"}:
            package_headers.append(relative)
    libraries = sorted((mirror / "lib").glob("*.dylib"))
    if not libraries:
        raise EvidenceError("the package carries no mirror libraries")
    symbols = {}
    for path in [image, *libraries]:
        names = image_symbols(path)
        if not names:
            raise EvidenceError(f"{path} lists no symbols")
        symbols[path.name] = names
    if not any("pulp::" in name for name in symbols[image.name]):
        raise EvidenceError("the consumer image references no packaged symbol")

    definition = compile_command_findings(commands) + package_definition_findings(package_files)
    headers = header_findings(depfiles, package_headers)
    audio_symbols, forward_symbols = symbol_findings(symbols, view_unit_packaged=view_unit_packaged)
    debt_headers = {
        debt: any(header.endswith(path) for header in package_headers)
        for debt, path in DEBTS.items()
    }
    checks = {
        "no-widgets-gate-definition": not definition,
        "no-audio-headers": not headers,
        "no-audio-symbols": not audio_symbols,
        "name-only-forward-declarations": not forward_symbols,
    }
    return {
        "schema": SCHEMA,
        "inert": all(checks.values()),
        "export_id": receipt.get("export_id"),
        "pulp_sha": receipt.get("pulp_sha"),
        "mirror_sha": receipt.get("mirror_sha"),
        "packaged_units": units,
        "debts": {
            debt: {"header": DEBTS[debt], "header_in_package": present}
            for debt, present in debt_headers.items()
        },
        "checks": checks,
        "evidence": {
            "compile_commands": len(commands),
            "depfiles": len(depfiles),
            "package_headers": len(package_headers),
            "view_headers_in_package": sum(1 for h in package_headers if VIEW_HEADER.search(h)),
            "images": {name: len(names) for name, names in symbols.items()},
        },
        "findings": {
            "no-widgets-gate-definition": definition,
            "no-audio-headers": headers,
            "no-audio-symbols": audio_symbols[:50],
            "name-only-forward-declarations": forward_symbols[:50],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", type=Path, required=True, help="installed SDK CMake prefix")
    parser.add_argument("--consumer-build", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True, help="the linked consumer executable")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        proof = verify(args.prefix.resolve(), args.consumer_build.resolve(), args.image.resolve())
    except (OSError, EvidenceError) as error:
        print(f"vellum-mirror-inertness: unusable evidence: {error}", file=sys.stderr)
        return 2
    rendered = json.dumps(proof, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if proof["inert"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
