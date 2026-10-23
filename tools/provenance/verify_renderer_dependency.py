#!/usr/bin/env python3
"""Verify Vellum's locked renderer metadata and, optionally, the sealed asset."""

from __future__ import annotations

import argparse
import copy
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
ATTESTATION_PATH = ROOT / "provenance/renderer-attestation.json"
SIGNATURE_PATH = ROOT / "provenance/renderer-attestation.json.sig"
ALLOWED_SIGNERS_PATH = ROOT / "provenance/release-signers.allowed_signers"
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "maccatalyst"}

# The owner-approved issuer. The attestation is accepted only when it is
# signed by this principal's key, and that key must be the one pinned here,
# so editing the allowed-signers file alone cannot admit a new issuer.
OWNER_PRINCIPAL = "daniel.raffel@gmail.com"
OWNER_FINGERPRINT = "SHA256:eMlEuZcnW6NJDrKQBuN5EX1q69bOlIRPl6XmutVTgcs"
SIGNATURE_NAMESPACE = "vellum-renderer-attestation"
SIGNING_DOMAIN = b"vellum.renderer-provenance.v1 signed record\n"
LEGAL_MANIFEST_SCHEMA = "skia-builder.legal-manifest.v1"
CONTENT_ADDRESSED_MANIFEST = re.compile(r"\.legal-manifest\.sha256-([0-9a-f]{64})\.json$")
PENDING_MARKERS = ("pending", "unknown")
LOCK_STATUS_BLOCKED = "incubation-byte-locked-not-release-attested"
LOCK_STATUS_ELIGIBLE = "byte-locked-renderer-release-attested"


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


def signing_payload(attestation: dict[str, object]) -> bytes:
    """The exact bytes the owner signs: the canonical record and artifact digest.

    The record is canonical JSON with the signature block reduced to the
    fields the signature itself depends on (issuer, subject, namespace). The
    state and digests of the signature file are excluded because they are
    written after signing.
    """
    signature = attestation.get("signature")
    require(isinstance(signature, dict), "renderer attestation signature block is missing")
    # Verification results are attached under "_" keys; they are never signed.
    record = {key: value for key, value in attestation.items() if not key.startswith("_")}
    record["signature"] = {key: signature.get(key) for key in ("issuer", "subject", "namespace")}
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return SIGNING_DOMAIN + canonical.encode("utf-8") + b"\n"


def allowed_signer_principals(path: Path) -> list[str]:
    principals: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        require(len(fields) >= 3, "allowed-signers file contains a malformed entry")
        principals.extend(fields[0].split(","))
    return principals


def verify_signature(
    attestation: dict[str, object],
    signature_path: Path,
    allowed_signers: Path,
    *,
    owner_principal: str = OWNER_PRINCIPAL,
    owner_fingerprint: str = OWNER_FINGERPRINT,
) -> dict[str, str]:
    """Verify the SSH signature over the canonical record; never trust the state string."""
    signature = attestation["signature"]
    require(shutil.which("ssh-keygen") is not None,
            "ssh-keygen is required to verify the renderer attestation signature")
    require(signature.get("issuer") == owner_principal,
            f"renderer attestation issuer {signature.get('issuer')!r} is not the owner")
    require(signature.get("namespace") == SIGNATURE_NAMESPACE,
            f"renderer attestation signature namespace must be {SIGNATURE_NAMESPACE}")
    artifact = attestation.get("artifact")
    require(isinstance(artifact, dict) and signature.get("subject") == artifact.get("sha256"),
            "renderer attestation signature subject must be the artifact digest")
    require(signature_path.is_file(),
            f"renderer attestation is marked signed but {signature_path.name} is missing")
    require(allowed_signers.is_file(), f"allowed-signers file is missing: {allowed_signers}")
    require(allowed_signer_principals(allowed_signers) == [owner_principal],
            "allowed-signers must name exactly the owner principal")
    fingerprints = [
        fields[1] for row in subprocess.run(
            ["ssh-keygen", "-lf", str(allowed_signers)],
            check=True, capture_output=True, text=True).stdout.splitlines()
        if len(fields := row.split()) >= 2
    ]
    require(fingerprints == [owner_fingerprint],
            f"allowed-signers key {fingerprints} is not the pinned owner key")
    signature_bytes = signature_path.read_bytes()
    require(signature.get("signature_sha256") == sha256_bytes(signature_bytes),
            "renderer attestation signature_sha256 does not match the signature file")
    payload = signing_payload(attestation)
    require(signature.get("payload_sha256") == sha256_bytes(payload),
            "renderer attestation payload_sha256 does not match the canonical record")
    result = subprocess.run(
        ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", owner_principal,
         "-n", SIGNATURE_NAMESPACE, "-s", str(signature_path)],
        input=payload, capture_output=True, check=False,
    )
    detail = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    require(result.returncode == 0,
            f"renderer attestation signature does not verify: {detail or result.returncode}")
    return {"principal": owner_principal, "fingerprint": owner_fingerprint,
            "payload_sha256": sha256_bytes(payload), "verification": detail}


def release_asset_url(artifact: dict[str, object], name: str) -> str:
    return (f"{artifact['repository']}/releases/download/{artifact['release_tag']}/{name}")


def validate_legal_inventory_record(attestation: dict[str, object]) -> dict[str, object]:
    """Check the exhaustive inventory names one content-addressed release asset."""
    inventory = attestation["legal_inventory"]
    artifact = attestation["artifact"]
    manifest = inventory.get("manifest")
    require(isinstance(manifest, dict), "exhaustive legal inventory names no manifest asset")
    name, digest = manifest.get("name"), manifest.get("sha256")
    require(isinstance(digest, str) and HEX_64.fullmatch(digest) is not None,
            "legal manifest digest is malformed")
    require(inventory.get("manifest_sha256") == digest,
            "legal inventory manifest_sha256 differs from its manifest asset")
    match = CONTENT_ADDRESSED_MANIFEST.search(name) if isinstance(name, str) else None
    require(match is not None and match.group(1) == digest,
            "legal manifest asset name must carry its own SHA-256")
    archive_stem = str(artifact["name"]).removesuffix(".zip")
    require(name.startswith(archive_stem + "."),
            "legal manifest asset does not belong to the locked archive")
    require(manifest.get("url") == release_asset_url(artifact, name),
            "legal manifest URL is not the locked release's asset")
    return manifest


def verify_legal_manifest_file(
    attestation: dict[str, object], manifest_path: Path, archive: Path | None = None
) -> dict[str, object]:
    """Verify the downloaded manifest is the attested one and covers the archive."""
    record = validate_legal_inventory_record(attestation)
    data = manifest_path.read_bytes()
    require(sha256_bytes(data) == record["sha256"],
            f"legal manifest SHA-256 {sha256_bytes(data)} differs from the attestation")
    manifest = json.loads(data)
    artifact = attestation["artifact"]
    require(manifest.get("schema") == LEGAL_MANIFEST_SCHEMA, "legal manifest schema is unsupported")
    require(manifest.get("exhaustive") is True and manifest.get("gaps") == [],
            "legal manifest does not claim an exhaustive inventory")
    described = manifest.get("archive")
    require(isinstance(described, dict) and described.get("sha256") == artifact["sha256"]
            and described.get("name") == artifact["name"],
            "legal manifest describes a different archive")
    licenses = manifest.get("licenses")
    require(isinstance(licenses, dict) and licenses, "legal manifest has no license texts")
    for row in manifest.get("zip_members", []):
        require(isinstance(row, dict) and row.get("kind") in
                {"static-archive", "packaged-file", "builder-metadata"},
                "legal manifest zip member row is malformed")
        if row["kind"] != "static-archive":
            require(row.get("licenses") and all(item in licenses for item in row["licenses"]),
                    f"legal manifest leaves {row.get('name')} without a license")
    for name, row in (manifest.get("static_archives") or {}).items():
        for arch, piece in row.get("slices", {}).items():
            for member in piece.get("members", []):
                require(member.get("objects") and member.get("licenses")
                        and all(item in licenses for item in member["licenses"]),
                        f"legal manifest leaves {name}[{arch}] {member.get('name')} unresolved")
    if archive is not None:
        listed = {row["name"]: row["sha256"] for row in manifest.get("zip_members", [])}
        with zipfile.ZipFile(archive) as handle:
            members = safe_members(handle)
            actual = {name: sha256_bytes(handle.read(info))
                      for name, info in members.items() if not info.is_dir()}
        require(actual == listed, "legal manifest zip-member inventory differs from the archive")
    return {"legal_manifest_sha256": record["sha256"],
            "legal_manifest_members": len(manifest.get("zip_members", [])),
            "legal_manifest_licenses": len(licenses)}


def verify_release_assets(attestation: dict[str, object], release_json: Path) -> dict[str, object]:
    """Check GitHub's own asset digests for the archive and its legal manifest."""
    release = json.loads(release_json.read_text(encoding="utf-8"))
    artifact = attestation["artifact"]
    require(release.get("tag_name") == artifact["release_tag"],
            "release JSON is not the locked release tag")
    assets = {row.get("name"): row for row in release.get("assets", []) if isinstance(row, dict)}
    record = validate_legal_inventory_record(attestation)
    for name, digest in ((artifact["name"], artifact["sha256"]), (record["name"], record["sha256"])):
        row = assets.get(name)
        require(row is not None, f"release asset is missing: {name}")
        require(row.get("digest") == f"sha256:{digest}",
                f"release asset {name} digest {row.get('digest')} differs from sha256:{digest}")
    return {"release_assets_verified": 2}


def validate_build_identity(build_identity: dict[str, object]) -> None:
    for side in ("compiler", "linker"):
        row = build_identity.get(side)
        require(isinstance(row, dict), f"renderer build identity {side} is missing")
        for field in ("vendor", "version"):
            value = row.get(field)
            require(isinstance(value, str) and value.strip()
                    and not any(marker in value for marker in PENDING_MARKERS),
                    f"renderer build identity {side}.{field} is not attested")
        require(isinstance(row.get("sha256"), str) and HEX_64.fullmatch(row["sha256"]) is not None,
                f"renderer build identity {side}.sha256 is not a SHA-256")
    abi = build_identity.get("cpp_abi")
    require(isinstance(abi, dict), "renderer build identity cpp_abi is missing")
    for field in ("stdlib", "abi"):
        value = abi.get(field)
        require(isinstance(value, str) and value.strip()
                and not any(marker in value for marker in PENDING_MARKERS),
                f"renderer build identity cpp_abi.{field} is not attested")


def validate_renderer_attestation(
    renderer: dict[str, object],
    attestation: object,
    *,
    signature_path: Path = SIGNATURE_PATH,
    allowed_signers: Path = ALLOWED_SIGNERS_PATH,
    owner_principal: str = OWNER_PRINCIPAL,
    owner_fingerprint: str = OWNER_FINGERPRINT,
) -> dict[str, object]:
    """Validate the digest-to-provenance binding.

    A pending record describes the current state and cannot make the renderer
    eligible. A record that claims ``signed`` is always verified
    cryptographically, and one that claims ``exhaustive`` must name its
    content-addressed manifest, whatever the lock declares.
    """
    require(isinstance(attestation, dict),
            "renderer artifact digest has no provenance record")
    require(attestation.get("schema") == "vellum.renderer-provenance.v1",
            "renderer provenance schema is missing or unsupported")
    artifact = attestation.get("artifact")
    locked_artifact = renderer.get("artifact")
    require(isinstance(artifact, dict) and isinstance(locked_artifact, dict),
            "renderer provenance artifact identity is missing")
    require(artifact.get("sha256") == locked_artifact.get("sha256"),
            "renderer artifact digest has no matching provenance record")
    for field in ("repository", "release_tag", "name"):
        require(artifact.get(field) == locked_artifact.get(field),
                f"renderer provenance artifact {field} differs from the lock")

    locked_identities = renderer.get("source_identities")
    identities = attestation.get("source_identities")
    require(isinstance(locked_identities, dict) and isinstance(identities, dict),
            "renderer provenance source identities are missing")
    for name in ("skia", "dawn", "builder"):
        locked = locked_identities.get(name)
        recorded = identities.get(name)
        require(isinstance(locked, dict) and isinstance(recorded, dict),
                f"renderer provenance identity is missing: {name}")
        require(recorded.get("commit") == locked.get("commit"),
                f"renderer provenance commit differs from the lock: {name}")

    build_identity = attestation.get("build_identity")
    require(isinstance(build_identity, dict),
            "renderer compiler/linker/ABI identity is missing")
    for field in ("target", "configuration", "compiler", "linker", "cpp_abi"):
        require(field in build_identity,
                f"renderer build identity is missing: {field}")
    legal_inventory = attestation.get("legal_inventory")
    require(isinstance(legal_inventory, dict)
            and legal_inventory.get("state") in {"observed-not-exhaustive", "exhaustive"},
            "renderer legal inventory state is invalid")
    signature = attestation.get("signature")
    require(isinstance(signature, dict)
            and signature.get("state") in {"missing", "signed"},
            "renderer attestation signature state is invalid")
    require(attestation.get("state") in {"pending-owner-attestation", "owner-attested"},
            "renderer attestation state is invalid")
    if legal_inventory["state"] == "exhaustive":
        validate_legal_inventory_record(attestation)
    attestation = copy.deepcopy(attestation)
    if signature["state"] == "signed":
        attestation["_verified_signature"] = verify_signature(
            attestation, signature_path, allowed_signers,
            owner_principal=owner_principal, owner_fingerprint=owner_fingerprint)
    if attestation["state"] == "owner-attested":
        require(signature["state"] == "signed",
                "an owner-attested record must carry a verified signature")
        require(legal_inventory["state"] == "exhaustive",
                "an owner-attested record must carry an exhaustive legal inventory")
        validate_build_identity(build_identity)
    return attestation


def require_release_eligible_record(attestation: dict[str, object]) -> None:
    """The conditions under which the lock may say ``eligible``."""
    require(attestation.get("state") == "owner-attested",
            "renderer eligibility requires an owner-attested provenance record")
    require(attestation.get("signature", {}).get("state") == "signed"
            and "_verified_signature" in attestation,
            "renderer eligibility requires a cryptographically verified signature")
    require(attestation.get("legal_inventory", {}).get("state") == "exhaustive",
            "renderer eligibility requires an exhaustive legal inventory")


def load_lock(
    path: Path,
    attestation_path: Path = ATTESTATION_PATH,
    *,
    signature_path: Path = SIGNATURE_PATH,
    allowed_signers: Path = ALLOWED_SIGNERS_PATH,
    owner_principal: str = OWNER_PRINCIPAL,
    owner_fingerprint: str = OWNER_FINGERPRINT,
) -> tuple[dict[str, object], dict[str, object]]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    require(lock.get("schema_version") == 2, "renderer lock schema must be version 2")
    runtime = lock.get("runtime_toolchain_dependencies")
    require(isinstance(runtime, dict), "runtime dependency lock is missing")
    renderer = runtime.get("renderer")
    require(isinstance(renderer, dict), "renderer dependency lock is missing")
    eligibility = renderer.get("release_eligibility")
    require(eligibility in {"blocked", "eligible"},
            "renderer release_eligibility must be blocked or eligible")
    eligible = eligibility == "eligible"
    require(lock.get("status") == (LOCK_STATUS_ELIGIBLE if eligible else LOCK_STATUS_BLOCKED),
            "renderer lock status does not match its release eligibility")
    require(renderer.get("attestation_record") == "provenance/renderer-attestation.json",
            "renderer attestation record path is missing or unexpected")
    blockers = renderer.get("release_blockers")
    if eligible:
        require(blockers == [], "an eligible renderer must have no open release blockers")
    else:
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
            and completeness.get("claim") == ("exhaustive" if eligible else "observed-not-exhaustive"),
            "renderer transitive inventory claim does not match release eligibility")
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
    if not eligible:
        require("not an exhaustive legal/SBOM claim" in dependencies,
                "DEPENDENCIES.md does not disclose the transitive inventory limitation")
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VerificationError("renderer artifact digest has no provenance record") from error
    attestation = validate_renderer_attestation(
        renderer, attestation, signature_path=signature_path, allowed_signers=allowed_signers,
        owner_principal=owner_principal, owner_fingerprint=owner_fingerprint)
    if eligible:
        require_release_eligible_record(attestation)
        require(completeness.get("legal_manifest_sha256")
                == attestation["legal_inventory"]["manifest_sha256"],
                "the lock's exhaustive claim names a different legal manifest")
    renderer["_attestation"] = attestation
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
    require(all(shutil.which(tool) is not None for tool in ("ar", "lipo", "otool")),
            "ar, lipo, and otool are required for Mach-O observation verification")
    with tempfile.TemporaryDirectory(prefix="vellum-renderer-macho-") as temporary:
        root = Path(temporary)
        archive = root / str(observation["archive"])
        archive.write_bytes(archive_bytes)
        architectures = subprocess.run(
            ["lipo", "-archs", str(archive)], check=True, capture_output=True, text=True
        ).stdout.split()
        # m150 used a thin archive while m153 is universal. Thin the latter
        # before ar reads a member, preserving the lock's macOS-arm64 contract.
        if len(architectures) > 1:
            require("arm64" in architectures,
                    f"universal archive omits arm64: {archive.name}")
            arm64_archive = root / f"arm64-{archive.name}"
            subprocess.run(
                ["lipo", "-thin", "arm64", str(archive), "-output", str(arm64_archive)],
                check=True, capture_output=True, text=True
            )
            archive = arm64_archive
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
    parser.add_argument("--attestation", type=Path, default=ATTESTATION_PATH)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--require-macho", action="store_true")
    parser.add_argument("--signature", type=Path, default=SIGNATURE_PATH,
                        help="SSH signature over the canonical attestation record")
    parser.add_argument("--allowed-signers", type=Path, default=ALLOWED_SIGNERS_PATH)
    parser.add_argument("--legal-manifest", type=Path,
                        help="the downloaded content-addressed legal manifest asset")
    parser.add_argument("--release-json", type=Path,
                        help="GitHub's release JSON for the locked tag (assets[].digest)")
    parser.add_argument("--require-release-eligible", action="store_true",
                        help="fail unless the renderer is eligible, its signature verifies, and "
                             "--legal-manifest and --release-json prove the attested manifest")
    parser.add_argument("--print-signing-payload", action="store_true",
                        help="write the exact bytes the owner signs to stdout and exit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.print_signing_payload:
        try:
            sys.stdout.buffer.write(signing_payload(
                json.loads(args.attestation.read_text(encoding="utf-8"))))
        except (OSError, ValueError, VerificationError) as error:
            print(f"renderer-dependency-verify: {error}", file=sys.stderr)
            return 1
        return 0
    try:
        _, renderer = load_lock(args.lock, args.attestation, signature_path=args.signature,
                                allowed_signers=args.allowed_signers)
        attestation = renderer["_attestation"]
        result: dict[str, object] = {
            "ok": True,
            "lock": str(args.lock),
            "release_eligibility": renderer["release_eligibility"],
            "release_blockers": [row["id"] for row in renderer["release_blockers"]],
            "attestation_state": attestation["state"],
            "signature_verified": "_verified_signature" in attestation,
        }
        if args.archive:
            result.update(verify_archive(args.archive, renderer, args.require_macho))
        if args.legal_manifest:
            result.update(verify_legal_manifest_file(attestation, args.legal_manifest, args.archive))
        if args.release_json:
            result.update(verify_release_assets(attestation, args.release_json))
        if args.require_release_eligible:
            require(renderer["release_eligibility"] == "eligible",
                    "GPU release is blocked: " + ", ".join(result["release_blockers"]))
            require(args.legal_manifest is not None and args.release_json is not None,
                    "release eligibility requires --legal-manifest and --release-json")
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
        if renderer["release_eligibility"] == "eligible":
            print("GPU release is eligible: the signed, exhaustive provenance record verified.")
        else:
            print("GPU release eligibility remains blocked by declared provenance gaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
