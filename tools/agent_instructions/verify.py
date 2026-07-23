#!/usr/bin/env python3
"""Verify Vellum's versioned agent-authoring contract against real surfaces."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any


MANIFEST_RELATIVE = Path(".agents/skills/vellum-app-authoring/manifest.v1.json")
EXPECTED_SCHEMA = "vellum.agent-instructions.v1"
EXPECTED_LIFECYCLE = (
    "create", "doctor", "import", "reimport", "build", "dev", "run",
    "test", "capture", "package",
)
EXPECTED_TOOL_OWNED = {
    "framework.lock",
    "sources/imported", "design/ir", "design/generated", "tokens/imported",
    "tokens/generated", "assets/generated", "ui/generated",
}


class VerificationError(RuntimeError):
    pass


def load_cli(repo: Path) -> Any:
    path = repo / "cli/vellum_cli.py"
    spec = importlib.util.spec_from_file_location("vellum_instruction_contract_cli", path)
    if spec is None or spec.loader is None:
        raise VerificationError(f"cannot load CLI module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def source_channels(path: Path) -> dict[str, str]:
    channels: dict[str, str] = {}
    current: str | None = None
    in_sources = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "sources:":
            in_sources = True
            continue
        if in_sources and line and not line.startswith(" "):
            break
        route = re.fullmatch(r"  ([a-z0-9-]+):", line)
        if route:
            current = route.group(1)
            continue
        channel = re.fullmatch(r"    channel: ([a-z0-9-]+)", line)
        if channel and current:
            channels[current] = channel.group(1)
    if not channels:
        raise VerificationError("source-support policy has no readable source channels")
    return channels


def cli_surface(cli: Any) -> tuple[set[str], dict[str, set[str]]]:
    root = cli.parser()
    global_flags = {
        flag
        for action in root._actions
        for flag in action.option_strings
    }
    subparsers = next(
        (action for action in root._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    if subparsers is None:
        raise VerificationError("CLI parser has no command surface")
    commands = {
        name: {
            flag
            for action in parser._actions
            for flag in action.option_strings
        }
        for name, parser in subparsers.choices.items()
    }
    return global_flags, commands


def skill_invocations(text: str, executable: str) -> dict[str, set[str]]:
    invocations: dict[str, set[str]] = {}
    prefix = executable + " "
    candidates = [
        raw_line.strip()
        for raw_line in text.splitlines()
        if raw_line.strip().startswith(prefix)
    ]
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            rf"`({re.escape(executable)} [^`\n]+)`",
            text,
        )
    )
    for line in candidates:
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            raise VerificationError(f"invalid shell invocation in skill: {line}: {error}") from error
        invocations[line] = {token for token in tokens[1:] if token.startswith("-")}
    return invocations


def invocation_command(line: str, known_commands: set[str]) -> str:
    tokens = shlex.split(line)
    matches = [token for token in tokens[1:] if token in known_commands]
    if len(matches) != 1:
        raise VerificationError(f"CLI invocation must reference exactly one real command: {line}")
    return matches[0]


def verify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    manifest_path = repo / MANIFEST_RELATIVE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read agent instruction manifest: {error}") from error
    required_keys = {
        "schema", "version", "skill", "cli", "lifecycle",
        "supportedSourceTypes", "ownership", "capabilityFailure",
        "frameworkMutationPolicy",
    }
    if not isinstance(manifest, dict) or set(manifest) != required_keys:
        raise VerificationError("agent instruction manifest has missing or unknown fields")
    if manifest["schema"] != EXPECTED_SCHEMA or manifest["version"] != 1:
        raise VerificationError("unsupported agent instruction schema/version")

    skill = manifest["skill"]
    if not isinstance(skill, dict) or set(skill) != {"name", "path"}:
        raise VerificationError("skill locator is malformed")
    skill_path = repo / str(skill["path"])
    try:
        skill_path.resolve().relative_to(repo)
    except ValueError as error:
        raise VerificationError("skill path escapes repository") from error
    if skill_path != manifest_path.parent / "SKILL.md" or not skill_path.is_file():
        raise VerificationError("skill path does not resolve to the versioned adjacent SKILL.md")
    skill_text = skill_path.read_text(encoding="utf-8")
    if EXPECTED_SCHEMA not in skill_text:
        raise VerificationError("skill does not declare its manifest schema")
    if re.search(r"(?m)^\s*(?:sh\s+)?\./scripts/install\.sh(?:\s|$)", skill_text):
        raise VerificationError(
            "downstream application instructions cannot reference a repository-relative installer"
        )

    cli = load_cli(repo)
    cli_contract = manifest["cli"]
    if cli_contract != {
        "api": cli.CLI_API_VERSION,
        "resultSchema": cli.RESULT_SCHEMA,
        "executable": "vellum",
    }:
        raise VerificationError("agent instructions do not match the CLI API/result schema")
    global_flags, commands = cli_surface(cli)

    lifecycle = manifest["lifecycle"]
    if not isinstance(lifecycle, list) or tuple(item.get("step") for item in lifecycle if isinstance(item, dict)) != EXPECTED_LIFECYCLE:
        raise VerificationError("agent lifecycle is incomplete or out of canonical order")
    cli_steps: dict[str, dict[str, Any]] = {}
    for item in lifecycle:
        if not isinstance(item, dict) or set(item) != {"step", "kind", "command", "flags"}:
            raise VerificationError("lifecycle entry is malformed")
        flags = item["flags"]
        if not isinstance(flags, list) or not flags or len(flags) != len(set(flags)) or not all(isinstance(flag, str) for flag in flags):
            raise VerificationError(f"lifecycle flags are malformed for {item['step']}")
        if item["kind"] != "cli" or item["command"] != item["step"] or item["command"] not in commands:
            raise VerificationError(f"lifecycle references unknown CLI command: {item['command']}")
        allowed = global_flags | commands[item["command"]]
        unknown = set(flags) - allowed
        if unknown:
            raise VerificationError(f"{item['command']} instructions reference unknown flags: {sorted(unknown)}")
        cli_steps[item["command"]] = item

    invocation_rows = skill_invocations(skill_text, cli_contract["executable"])
    observed_commands: set[str] = set()
    observed_flags: dict[str, set[str]] = {command: set() for command in cli_steps}
    for line, flags in invocation_rows.items():
        command = invocation_command(line, set(commands))
        observed_commands.add(command)
        allowed = global_flags | commands[command]
        unknown = flags - allowed
        if unknown:
            raise VerificationError(f"SKILL.md references unknown {command} flags: {sorted(unknown)}")
        observed_flags[command].update(flags)
    if observed_commands != set(cli_steps):
        raise VerificationError(
            f"SKILL.md lifecycle commands are incomplete: observed={sorted(observed_commands)}"
        )
    mismatched = {
        command: {
            "declared": sorted(set(cli_steps[command]["flags"])),
            "observed": sorted(observed_flags[command]),
        }
        for command in cli_steps
        if observed_flags[command] != set(cli_steps[command]["flags"])
    }
    if mismatched:
        raise VerificationError(
            f"SKILL.md lifecycle flag coverage differs from the manifest: {mismatched}"
        )
    channels = source_channels(repo / "product/source-support.yaml")
    supported = sorted(route for route, channel in channels.items() if channel == "supported")
    if manifest["supportedSourceTypes"] != supported:
        raise VerificationError(
            "agent source claims do not exactly match supported source policy: "
            f"claimed={manifest['supportedSourceTypes']} supported={supported}"
        )
    unavailable = {route for route, channel in channels.items() if channel != "supported"}
    lowered_skill = skill_text.lower()
    claimed_unavailable = sorted(
        route for route in unavailable
        if re.search(rf"(?<![a-z0-9-]){re.escape(route)}(?![a-z0-9-])", lowered_skill)
    )
    if claimed_unavailable:
        raise VerificationError(
            f"SKILL.md names unsupported source routes: {claimed_unavailable}"
        )
    source_type_values = re.findall(r"--source-type\s+([a-z0-9-]+)", skill_text)
    if not source_type_values or any(value not in supported for value in source_type_values):
        raise VerificationError("SKILL.md source-type examples are absent or unsupported")

    ownership = manifest["ownership"]
    if not isinstance(ownership, dict) or set(ownership) != {"toolOwned", "developerOwned"}:
        raise VerificationError("ownership contract is malformed")
    if set(ownership["toolOwned"]) != EXPECTED_TOOL_OWNED:
        raise VerificationError("tool-owned paths do not cover the generated/imported boundary")
    if set(ownership["toolOwned"]) & set(ownership["developerOwned"]):
        raise VerificationError("tool-owned and developer-owned paths overlap")
    failure = manifest["capabilityFailure"]
    if failure != {"status": "capability_unavailable", "exitCode": cli.EXIT_UNAVAILABLE, "fallbackAllowed": False}:
        raise VerificationError("capability failure semantics do not match the CLI")
    if manifest["frameworkMutationPolicy"] != "release-only":
        raise VerificationError("framework fixes must flow through immutable releases")

    return {
        "schema": "vellum.agent-instructions-verification.v1",
        "ok": True,
        "instruction_schema": manifest["schema"],
        "lifecycle_steps": list(EXPECTED_LIFECYCLE),
        "cli_commands": sorted(cli_steps),
        "supported_source_types": supported,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        evidence = verify(args.repo)
    except VerificationError as error:
        print(f"agent-instructions verification failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    else:
        print("agent-instructions verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
