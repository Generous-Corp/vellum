#!/usr/bin/env python3
"""Verify Vellum's locked renderer metadata and, optionally, the sealed asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "provenance/third-party-lock.json"
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "maccatalyst"}


class VerificationError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_lock(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    require(lock.get("schema_version") == 2, "renderer lock schema must be version 2")
    require(lock.get("status") == "incubation-byte-locked-not-release-attested",
            "renderer lock must not claim release attestation")
    runtime = lock.get("runtime_toolchain_dependencies")
    require(isinstance(runtime, dict), "runtime dependency lock is missing")
    renderer = runtime.get("renderer")
    require(isinstance(renderer, dict), "renderer dependency lock is missing")
    require(renderer.get("release_eligibility") == "blocked",
            "GPU release must remain blocked while provenance gaps are open")
    blockers = renderer.get("release_blockers")
    require(isinstance(blockers, list) and blockers, "renderer release blockers are missing")
    blocker_ids = {row.get("id") for row in blockers if isinstance(row, dict)}
    require("renderer-transitive-legal-inventory-unproven" in blocker_ids,
            "unproven transitive legal inventory is not recorded as a release blocker")

    artifact = renderer.get("artifact")
    require(isinstance(artifact, dict), "renderer artifact identity is missing")
    for field in ("sha256", "builder_commit"):
        pattern = HEX_64 if field == "sha256" else HEX_40
        require(isinstance(artifact.get(field), str) and pattern.fullmatch(artifact[field]),
                f"renderer artifact {field} is malformed")
    require(artifact.get("legal_files_in_artifact") == [],
            "lock must accurately record that the sealed artifact has no legal manifest")

    identities = renderer.get("source_identities")
    require(isinstance(identities, dict), "renderer source identities are missing")
    for name in ("skia", "dawn", "builder"):
        row = identities.get(name)
        require(isinstance(row, dict), f"{name} source identity is missing")
        require(isinstance(row.get("commit"), str) and HEX_40.fullmatch(row["commit"]),
                f"{name} source commit is malformed")

    headers = renderer.get("headers")
    require(isinstance(headers, dict), "renderer header lock is missing")
    require(isinstance(headers.get("file_count"), int) and headers["file_count"] > 0,
            "renderer header count is malformed")
    require(isinstance(headers.get("construction_specific_sha256"), str)
            and HEX_64.fullmatch(headers["construction_specific_sha256"]),
            "renderer header digest is malformed")
    require(headers.get("canonical_tree_hash") is False,
            "construction-specific header digest must not claim canonicality")

    archives = renderer.get("archives")
    require(isinstance(archives, list) and archives, "renderer archive lock is empty")
    names: set[str] = set()
    for row in archives:
        require(isinstance(row, dict) and set(row) == {"name", "sha256"},
                "renderer archive row is malformed")
        require(isinstance(row["name"], str) and row["name"].endswith(".a"),
                "renderer archive name is malformed")
        require(row["name"] not in names, f"duplicate renderer archive: {row['name']}")
        require(isinstance(row["sha256"], str) and HEX_64.fullmatch(row["sha256"]),
                f"renderer archive digest is malformed: {row['name']}")
        names.add(row["name"])

    completeness = renderer.get("bundled_components_completeness")
    require(isinstance(completeness, dict)
            and completeness.get("claim") == "observed-not-exhaustive",
            "renderer transitive inventory must not claim exhaustiveness")
    components = renderer.get("bundled_components")
    require(isinstance(components, list) and components,
            "observed renderer component inventory is missing")
    component_names = {row.get("name") for row in components if isinstance(row, dict)}
    required_components = {
        "Expat", "libjpeg-turbo", "libpng", "libwebp", "Wuffs",
        "Chromium zlib", "Abseil", "PartitionAlloc",
    }
    require(required_components <= component_names,
            "observed renderer component inventory is incomplete")

    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    dependencies = (ROOT / "DEPENDENCIES.md").read_text(encoding="utf-8")
    for name in required_components | {"Dawn and Tint", "Skia and skcms"}:
        require(name in notice, f"NOTICE.md does not cover {name}")
    for commit in (identities["skia"]["commit"], identities["dawn"]["commit"],
                   identities["builder"]["commit"]):
        require(commit in dependencies, f"DEPENDENCIES.md omits source identity {commit}")
    require("not an exhaustive legal/SBOM claim" in dependencies,
            "DEPENDENCIES.md does not disclose the transitive inventory limitation")
    return lock, renderer


def safe_members(handle: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for member in handle.infolist():
        name = member.filename
        path = PurePosixPath(name)
        mode = (member.external_attr >> 16) & 0o170000
        require(name not in members, f"duplicate ZIP member: {name}")
        require(not path.is_absolute() and ".." not in path.parts and "\\" not in name,
                f"unsafe ZIP member: {name}")
        require(mode != stat.S_IFLNK, f"symbolic-link ZIP member is forbidden: {name}")
        members[name] = member
    return members


def read_member(handle: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str) -> bytes:
    require(name in members and not members[name].is_dir(), f"artifact member is missing: {name}")
    return handle.read(members[name])


def verify_macho_observation(archive_bytes: bytes, observation: dict[str, object]) -> None:
    require(shutil.which("ar") is not None and shutil.which("otool") is not None,
            "ar and otool are required for Mach-O observation verification")
    with tempfile.TemporaryDirectory(prefix="vellum-renderer-macho-") as temporary:
        root = Path(temporary)
        archive = root / str(observation["archive"])
        archive.write_bytes(archive_bytes)
        member = str(observation["representative_member"])
        result = subprocess.run(["ar", "-p", str(archive), member], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        require(result.stdout, f"representative archive member is empty: {member}")
        obj = root / member
        obj.write_bytes(result.stdout)
        output = subprocess.run(["otool", "-l", str(obj)], check=True,
                                capture_output=True, text=True).stdout
        match = re.search(
            r"cmd LC_BUILD_VERSION.*?platform\s+(\d+).*?minos\s+(\S+).*?sdk\s+(\S+)",
            output,
            re.DOTALL,
        )
        require(match is not None, f"LC_BUILD_VERSION missing from {member}")
        platform = PLATFORMS.get(int(match.group(1)), f"platform-{match.group(1)}")
        actual = (platform, match.group(2), match.group(3))
        expected = (observation["platform"], observation["minimum_os"], observation["sdk"])
        require(actual == expected,
                f"Mach-O tuple mismatch for {member}: expected {expected}, got {actual}")


def verify_archive(path: Path, renderer: dict[str, object], require_macho: bool) -> dict[str, object]:
    artifact = renderer["artifact"]
    actual_artifact_sha = sha256_file(path)
    require(actual_artifact_sha == artifact["sha256"],
            f"renderer artifact SHA-256 mismatch: {actual_artifact_sha}")

    archive_root = str(artifact["archive_root"]).rstrip("/") + "/"
    expected_archives = {row["name"]: row["sha256"] for row in renderer["archives"]}
    headers = renderer["headers"]
    header_root = str(headers["root"]).rstrip("/") + "/"
    with zipfile.ZipFile(path) as handle:
        members = safe_members(handle)
        actual_archive_names = {
            PurePosixPath(name).name
            for name, member in members.items()
            if name.startswith(archive_root) and not member.is_dir() and name.endswith(".a")
        }
        require(actual_archive_names == set(expected_archives),
                "renderer static archive set differs from the lock")

        archive_bytes: dict[str, bytes] = {}
        for name, expected_sha in expected_archives.items():
            data = read_member(handle, members, archive_root + name)
            require(sha256_bytes(data) == expected_sha, f"renderer archive SHA-256 mismatch: {name}")
            archive_bytes[name] = data

        header_names = sorted(
            name for name, member in members.items()
            if name.startswith(header_root) and not member.is_dir()
        )
        require(len(header_names) == headers["file_count"],
                f"renderer header count mismatch: {len(header_names)}")
        digest = hashlib.sha256()
        for name in header_names:
            relative = name[len(header_root):]
            file_sha = sha256_bytes(read_member(handle, members, name))
            digest.update(f"{file_sha}  ./{relative}\n".encode("utf-8"))
        require(digest.hexdigest() == headers["construction_specific_sha256"],
                f"renderer header digest mismatch: {digest.hexdigest()}")

        dawn_header = read_member(handle, members, header_root + "dawn/dawn_version.h").decode()
        body = re.search(r"kDawnVersion\s*=\s*\{([^}]+)\}", dawn_header, re.DOTALL)
        require(body is not None, "embedded Dawn version is missing")
        embedded_dawn = "".join(f"{int(value, 16):02x}" for value in re.findall(r"0x[0-9a-fA-F]+", body.group(1)))
        require(embedded_dawn == renderer["source_identities"]["dawn"]["commit"],
                f"embedded Dawn revision mismatch: {embedded_dawn}")

        gn_args = read_member(handle, members, "build/mac-gpu/lib/gn_args.txt").decode()
        feature_aliases = {
            "graphite": "skia_enable_graphite",
            "dawn": "skia_use_dawn",
            "metal": "skia_use_metal",
        }
        for key, expected in renderer["features"].items():
            gn_key = feature_aliases.get(key, key)
            value = str(expected).lower() if isinstance(expected, bool) else str(expected)
            require(re.search(rf"^\s*{re.escape(gn_key)}\s*=\s*{re.escape(value)}\s*$", gn_args,
                              re.MULTILINE) is not None,
                    f"renderer GN argument missing or changed: {gn_key}={value}")

    macho_verified = False
    if require_macho or sys.platform == "darwin":
        observations = renderer["observed_macho"]
        verify_macho_observation(archive_bytes[observations["skia"]["archive"]],
                                 observations["skia"])
        verify_macho_observation(archive_bytes[observations["dawn"]["archive"]],
                                 observations["dawn"])
        macho_verified = True

    return {
        "artifact_sha256": actual_artifact_sha,
        "archives_verified": len(expected_archives),
        "headers_verified": len(header_names),
        "embedded_dawn_commit": embedded_dawn,
        "macho_observations_verified": macho_verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--require-macho", action="store_true")
    parser.add_argument("--require-release-eligible", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        _, renderer = load_lock(args.lock)
        result: dict[str, object] = {
            "ok": True,
            "lock": str(args.lock),
            "release_eligibility": renderer["release_eligibility"],
            "release_blockers": [row["id"] for row in renderer["release_blockers"]],
        }
        if args.archive:
            result.update(verify_archive(args.archive, renderer, args.require_macho))
        if args.require_release_eligible:
            require(renderer["release_eligibility"] == "eligible",
                    "GPU release is blocked: " + ", ".join(result["release_blockers"]))
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile,
            subprocess.CalledProcessError, VerificationError) as error:
        print(f"renderer-dependency-verify: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print("Renderer dependency lock verified.")
        if args.archive:
            print(f"Verified {result['archives_verified']} archives and "
                  f"{result['headers_verified']} headers.")
        print("GPU release eligibility remains blocked by declared provenance gaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
