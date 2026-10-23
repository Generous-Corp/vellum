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
from typing import Callable


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
CONTENT_ADDRESSED_RECEIPT = re.compile(r"\.apple-toolchain\.sha256-([0-9a-f]{64})\.json$")
CONTENT_ADDRESSED_BUNDLE = re.compile(
    r"\.provenance-attestation\.sha256-([0-9a-f]{64})\.sigstore\.json$")
TOOLCHAIN_RECEIPT_SCHEMA = "skia-builder.apple-toolchain-receipt.v1"
CLANG_BANNER = re.compile(r"^(Apple )?clang version [0-9][0-9A-Za-z.()_ -]*$")
LD_VERSION = re.compile(r"PROJECT:ld-[0-9][0-9.]*")
SDK_VERSION = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")
# The only workflow, repository and ref whose build-provenance attestation is
# accepted for the renderer archive and its records.
BUILDER_REPOSITORY = "danielraffel/skia-builder"
BUILDER_SIGNER_WORKFLOW = "danielraffel/skia-builder/.github/workflows/build-skia.yml"
# Records rebound from a member-identical rebuild are attested by the
# dispatch-only workflow that rebinds them, never by build-skia.yml.
REBIND_SIGNER_WORKFLOW = "danielraffel/skia-builder/.github/workflows/rebind-provenance.yml"
BUILDER_SOURCE_REF = "refs/heads/main"
AR_MAGIC = b"!<arch>\n"
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


def underscore_keys(value: object, where: str = "") -> list[str]:
    """Every dict key starting with "_", anywhere in a JSON value."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{where}.{key}" if where else str(key)
            if str(key).startswith("_"):
                found.append(path)
            found.extend(underscore_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(underscore_keys(item, f"{where}[{index}]"))
    return found


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
    owner_principal: str | None = None,
    owner_fingerprint: str | None = None,
) -> dict[str, str]:
    """Verify the SSH signature over the canonical record; never trust the state string."""
    # Resolved at call time, so the pin is the module's, never a stale default.
    owner_principal = owner_principal or OWNER_PRINCIPAL
    owner_fingerprint = owner_fingerprint or OWNER_FINGERPRINT
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


def validate_release_asset_record(
    artifact: dict[str, object], record: object, pattern: re.Pattern[str], label: str
) -> dict[str, object]:
    """A record naming one content-addressed asset of the locked release."""
    require(isinstance(record, dict), f"{label} names no release asset")
    name, digest = record.get("name"), record.get("sha256")
    require(isinstance(digest, str) and HEX_64.fullmatch(digest) is not None,
            f"{label} digest is malformed")
    match = pattern.search(name) if isinstance(name, str) else None
    require(match is not None and match.group(1) == digest,
            f"{label} asset name must carry its own SHA-256")
    require(name.startswith(str(artifact["name"]).removesuffix(".zip") + "."),
            f"{label} asset does not belong to the locked archive")
    require(record.get("url") == release_asset_url(artifact, name),
            f"{label} URL is not the locked release's asset")
    return record


REBIND_METHOD = "member-identical-rebuild"
RECEIPT_TOOLCHAIN_FIELDS = ("runner", "xcode", "compiler", "archiver", "linker", "sdk", "cpp_abi",
                            "ccache", "target")


def zip_contents(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as handle:
        members = safe_members(handle)
        return {name: handle.read(info) for name, info in members.items() if not info.is_dir()}


# Fields of a rebound manifest that carry the rebuild's license mapping and
# source identity; they must be the rebuild manifest's, unchanged.
MANIFEST_INVENTORY_FIELDS = ("schema", "license_resolution", "cell", "source_revisions", "repositories",
                             "files", "licenses", "toolchain_inputs", "target_graph", "gaps",
                             "exhaustive")


def zip_member_rows(manifest: dict, label: str) -> dict[str, str]:
    """zip member name -> sha256, refusing a manifest that lists a name twice."""
    rows = manifest.get("zip_members") or []
    names = [row.get("name") for row in rows]
    require(len(names) == len(set(names)), f"the {label} lists a zip member more than once")
    return {row.get("name"): row.get("sha256") for row in rows}


def manifest_inventory_problems(rebound: dict, rebuild: dict) -> list[str]:
    """Where a rebound manifest's license mapping differs from the rebuild manifest's."""
    problems = [field for field in MANIFEST_INVENTORY_FIELDS if rebound.get(field) != rebuild.get(field)]
    for name, row in (rebound.get("static_archives") or {}).items():
        other = (rebuild.get("static_archives") or {}).get(name) or {}
        if row.get("licenses") != other.get("licenses"):
            problems.append(f"static_archives[{name}].licenses")
        for arch, piece in (row.get("slices") or {}).items():
            members = ((other.get("slices") or {}).get(arch) or {}).get("members") or []
            mapped = [(m.get("name"), m.get("sha256"), m.get("objects"), m.get("licenses"))
                      for m in piece.get("members", [])]
            if mapped != [(m.get("name"), m.get("sha256"), m.get("objects"), m.get("licenses"))
                          for m in members]:
                problems.append(f"static_archives[{name}][{arch}].members")
    if set(rebound.get("static_archives") or {}) != set(rebuild.get("static_archives") or {}):
        problems.append("static_archives")
    # Compared as sorted lists, so a duplicated row cannot hide another.
    def mapping(rows):
        return sorted(json.dumps([r.get("name"), r.get("kind"), r.get("origin"), r.get("licenses")],
                                 sort_keys=True) for r in rows or [])
    if mapping(rebound.get("zip_members")) != mapping(rebuild.get("zip_members")):
        problems.append("zip_members")
    return problems


def verify_rebuild_provenance(
    attested_derived: dict[str, object], subjects: dict[str, Path], bundle: Path,
    runner: "GhRunner" = None,
) -> list[str]:
    """The rebuild itself must be attested by build-skia.yml on main.

    The rebound records copy the rebuild's license mapping and toolchain
    values; those are only as trustworthy as the run that produced them. A
    branch run can carry edited tools, so the rebuild archive, manifest and
    receipt must each verify against the rebuild's own build-provenance
    bundle, signed by build-skia.yml on refs/heads/main on a GitHub-hosted
    runner. The bundle is the one the signed record names.
    """
    runner = runner or run_gh
    run = attested_derived.get("rebuild_run") or {}
    head_sha = run.get("head_sha")
    require(isinstance(head_sha, str) and HEX_40.fullmatch(head_sha) is not None,
            "the attested derivation names no rebuild commit (rebuild_run.head_sha)")
    require(run.get("repository") == BUILDER_REPOSITORY and run.get("ref") == BUILDER_SOURCE_REF
            and isinstance(run.get("run_id"), int) and isinstance(run.get("run_attempt"), int),
            "the attested derivation's rebuild_run is not a build-skia.yml run on main")
    expected_invocation = (f"https://github.com/{BUILDER_REPOSITORY}/actions/runs/"
                           f"{run['run_id']}/attempts/{run['run_attempt']}")
    record = attested_derived.get("rebuild_bundle")
    require(isinstance(record, dict) and isinstance(record.get("sha256"), str)
            and HEX_64.fullmatch(record["sha256"]) is not None,
            "the attested derivation names no rebuild provenance bundle")
    require(sha256_bytes(bundle.read_bytes()) == record["sha256"],
            "the rebuild provenance bundle is not the one the signed record names")
    require(shutil.which("gh") is not None or runner is not run_gh,
            "gh is required to verify the rebuild's build provenance")
    verified, invocations = [], set()
    for label, path in sorted(subjects.items()):
        result = runner(["gh", "attestation", "verify", str(path), "--bundle", str(bundle),
                         "--repo", BUILDER_REPOSITORY,
                         "--signer-workflow", f"{BUILDER_SIGNER_WORKFLOW}@{BUILDER_SOURCE_REF}",
                         "--source-ref", BUILDER_SOURCE_REF, "--source-digest", head_sha,
                         "--deny-self-hosted-runners", "--format", "json"])
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        require(result.returncode == 0,
                f"the rebuild {label} is not attested by build-skia.yml on main: {detail[-400:]}")
        try:
            results = json.loads(result.stdout or "[]")
        except ValueError:
            results = []
        found = {(((row or {}).get("verificationResult") or {}).get("signature") or {})
                 .get("certificate", {}).get("runInvocationURI") for row in results}
        require(results and None not in found,
                f"the rebuild {label}'s attestation names no run invocation")
        invocations |= found
        verified.append(label)
    # One run built the archive and wrote the manifest and receipt: the one
    # the signed record names.
    require(len(invocations) == 1,
            "the rebuild archive, manifest and receipt were attested by different runs: "
            + ", ".join(sorted(invocations)))
    require(invocations == {expected_invocation},
            f"the rebuild was attested by {sorted(invocations)[0]}, not the signed record's "
            f"{expected_invocation}")
    return verified


def verify_rebind(
    attestation: dict[str, object], manifest_path: Path, receipt_path: Path,
    archive: Path, rebuild_archive: Path, rebuild_receipt: Path,
    rebuild_manifest: Path, rebuild_bundle: Path, runner: "GhRunner" = None,
) -> dict[str, object]:
    """Re-run the member comparison behind records derived from a rebuild.

    The locked archive's manifest and receipt may be rebound from a rebuild
    that is member-identical to it. Both records must name the same
    derivation, as must the signed record; the rebuild archive, manifest and
    receipt given here must be the ones they name and must be attested by
    build-skia.yml on main; every zip member and every static-archive member
    of the two archives must have the same content (only archive container
    metadata may differ); and the rebound records must carry the rebuild's
    license mapping and toolchain values unchanged.
    """
    manifest = json.loads(manifest_path.read_bytes())
    receipt = json.loads(receipt_path.read_bytes())
    derived = manifest.get("derived_from")
    require(isinstance(derived, dict) and derived.get("method") == REBIND_METHOD,
            "legal manifest does not record a member-identical rebuild")
    require(receipt.get("derived_from") == derived,
            "toolchain receipt and legal manifest name different derivations")
    # The signed record names the rebuild's evidence assets (with URLs); they
    # must be the ones the manifest was derived from.
    attested = (attestation.get("legal_inventory") or {}).get("derived_from")
    require(isinstance(attested, dict),
            "the signed record does not name the derivation its legal manifest records")
    for key in ("rebuild_archive", "rebuild_receipt", "rebuild_manifest", "comparison", "rebuild_bundle"):
        value = (attested.get(key) or {}).get("sha256")
        require(isinstance(value, str) and value == (derived.get(key) or {}).get("sha256"),
                f"the attested derivation's {key} differs from the legal manifest's")
    for field in REBUILD_RUN_FIELDS:
        require((attested.get("rebuild_run") or {}).get(field) == (derived.get("rebuild_run") or {}).get(field),
                f"the attested derivation's rebuild_run.{field} differs from the legal manifest's"
                if field != "head_sha" else
                "the attested derivation's rebuild commit differs from the legal manifest's")
    require(sha256_bytes(rebuild_manifest.read_bytes())
            == (derived.get("rebuild_manifest") or {}).get("sha256"),
            "the rebuild manifest is not the one the records were derived from")
    require(sha256_file(rebuild_archive) == (derived.get("rebuild_archive") or {}).get("sha256"),
            "the rebuild archive is not the one the records were derived from")
    require(sha256_bytes(rebuild_receipt.read_bytes())
            == (derived.get("rebuild_receipt") or {}).get("sha256"),
            "the rebuild receipt is not the one the records were derived from")
    # The rebuild manifest and receipt must describe the rebuild archive itself.
    rebuild_sha = sha256_file(rebuild_archive)
    artifact_name = (attestation.get("artifact") or {}).get("name")
    rebuild_manifest_record = json.loads(rebuild_manifest.read_bytes())
    for label, record in (("manifest", rebuild_manifest_record),
                          ("receipt", json.loads(rebuild_receipt.read_bytes()))):
        bound = record.get("archive")
        require(isinstance(bound, dict) and bound.get("sha256") == rebuild_sha
                and bound.get("name") == artifact_name,
                f"the rebuild {label} does not describe the rebuild archive")
    listed = zip_member_rows(rebuild_manifest_record, "rebuild manifest")
    actual = {name: sha256_bytes(data) for name, data in zip_contents(rebuild_archive).items()}
    require(listed == actual, "the rebuild manifest's zip members differ from the rebuild archive")
    provenance = verify_rebuild_provenance(
        attested, {"archive": rebuild_archive, "legal manifest": rebuild_manifest,
                   "toolchain receipt": rebuild_receipt}, rebuild_bundle, runner)
    differing = manifest_inventory_problems(manifest, json.loads(rebuild_manifest.read_bytes()))
    require(not differing, "the rebound manifest's license mapping differs from the rebuild "
                           "manifest's: " + ", ".join(differing))
    locked, rebuilt = zip_contents(archive), zip_contents(rebuild_archive)
    require(set(locked) == set(rebuilt), "the rebuild's zip members differ from the locked archive's")
    compared = 0
    for name in sorted(locked):
        data, other = locked[name], rebuilt[name]
        if data == other:
            continue
        is_archive = data.startswith(AR_MAGIC) or int.from_bytes(data[:4], "big") in (0xCAFEBABE, 0xCAFEBABF)
        require(is_archive, f"{name} differs in content between the locked archive and the rebuild")
        require(static_archive_contents(data) == static_archive_contents(other),
                f"{name}: a static-archive member, symbol table or slice differs between the "
                "locked archive and the rebuild")
        compared += 1
    rebuild = json.loads(rebuild_receipt.read_bytes())
    require(sha256_bytes(rebuild_receipt.read_bytes())
            == (derived.get("rebuild_receipt") or {}).get("sha256"),
            "the rebuild receipt is not the one the records were derived from")
    for field in RECEIPT_TOOLCHAIN_FIELDS:
        require(receipt.get(field) == rebuild.get(field),
                f"rebound receipt {field} differs from the rebuild receipt that measured it")
    return {"rebind_verified": True, "rebind_archives_with_container_differences": compared,
            "rebind_zip_members_compared": len(locked), "rebuild_provenance_verified": provenance}


REBUILD_RUN_FIELDS = ("repository", "run_id", "run_attempt", "ref", "head_sha")


REBUILD_PROOF_FLAGS = ("--rebuild-archive", "--rebuild-receipt", "--rebuild-manifest",
                       "--rebuild-provenance-bundle")


def rebuild_proofs_required(manifest_path: Path, *proofs: Path | None) -> bool:
    """Whether to re-run the rebind comparison; a derived manifest needs every rebuild proof."""
    derived = json.loads(manifest_path.read_bytes()).get("derived_from")
    if derived is None and all(proof is None for proof in proofs):
        return False
    require(len(proofs) == len(REBUILD_PROOF_FLAGS) and all(proof is not None for proof in proofs),
            "the legal manifest is derived from a rebuild; " + ", ".join(REBUILD_PROOF_FLAGS)
            + " are required")
    return True


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
    attestation: dict[str, object], manifest_path: Path, archive: Path | None = None,
    locked_archives: set[str] | None = None,
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
    require(isinstance(manifest.get("zip_members"), list) and manifest["zip_members"],
            "legal manifest inventories no zip member")
    zip_member_rows(manifest, "legal manifest")
    static_archives = manifest.get("static_archives")
    require(isinstance(static_archives, dict) and static_archives,
            "legal manifest inventories no static archive")
    if locked_archives is not None:
        require(set(static_archives) == locked_archives,
                "legal manifest static archives differ from the lock's archives")
    listed_archives = {row["name"] for row in manifest.get("zip_members", [])
                       if row.get("kind") == "static-archive"}
    require(set(static_archives) == listed_archives,
            "legal manifest static-archive inventory differs from its zip members")
    for name, row in static_archives.items():
        slices = row.get("slices") if isinstance(row, dict) else None
        require(isinstance(slices, dict) and slices, f"legal manifest lists no slices for {name}")
        for arch, piece in slices.items():
            require(isinstance(piece.get("members"), list) and piece["members"],
                    f"legal manifest lists no members for {name}[{arch}]")
            for member in piece.get("members", []):
                require(member.get("objects") and member.get("licenses")
                        and all(item in licenses for item in member["licenses"]),
                        f"legal manifest leaves {name}[{arch}] {member.get('name')} unresolved")
    if archive is not None:
        listed = zip_member_rows(manifest, "legal manifest")
        with zipfile.ZipFile(archive) as handle:
            members = safe_members(handle)
            actual = {name: sha256_bytes(handle.read(info))
                      for name, info in members.items() if not info.is_dir()}
        require(actual == listed, "legal manifest zip-member inventory differs from the archive")
        with zipfile.ZipFile(archive) as handle:
            members = safe_members(handle)
            for name, info in members.items():
                data = handle.read(info)
                if info.is_dir() or not data.startswith(AR_MAGIC) and int.from_bytes(
                        data[:4], "big") not in (0xCAFEBABE, 0xCAFEBABF):
                    continue
                recorded = static_archives.get(name)
                require(recorded is not None, f"legal manifest has no member inventory for {name}")
                relisted = static_archive_contents(data)
                require(set(relisted) == set(recorded["slices"]) or set(relisted) == {"thin"}
                        and len(recorded["slices"]) == 1,
                        f"legal manifest slices for {name} differ from the archive")
                for arch, expected in relisted.items():
                    piece = (recorded["slices"][arch] if arch != "thin"
                             else next(iter(recorded["slices"].values())))
                    got = [(member.get("name"), member.get("sha256")) for member in piece["members"]]
                    require(got == expected["members"],
                            f"legal manifest does not cover every member of {name}[{arch}]")
                    require(piece.get("symbol_tables") == expected["symbol_tables"],
                            f"legal manifest symbol tables for {name}[{arch}] differ from the archive")
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
    expected = [(artifact["name"], artifact["sha256"]), (record["name"], record["sha256"])]
    receipt = (attestation.get("build_identity") or {}).get("toolchain_receipt")
    bundle = (attestation.get("build_provenance") or {}).get("bundle")
    for extra in (receipt, bundle):
        if isinstance(extra, dict):
            expected.append((extra.get("name"), extra.get("sha256")))
    for name, digest in expected:
        row = assets.get(name)
        require(row is not None, f"release asset is missing: {name}")
        require(row.get("digest") == f"sha256:{digest}",
                f"release asset {name} digest {row.get('digest')} differs from sha256:{digest}")
    return {"release_assets_verified": len(expected)}


def attested_string(row: dict[str, object], field: str, label: str,
                    pattern: re.Pattern[str] | None = None) -> str:
    value = row.get(field)
    require(isinstance(value, str) and value.strip()
            and not any(marker in value.lower() for marker in PENDING_MARKERS),
            f"renderer build identity {label}.{field} is not attested")
    if pattern is not None:
        require(pattern.search(value) is not None,
                f"renderer build identity {label}.{field} {value!r} is not a measured value")
    return value


def validate_build_identity(build_identity: dict[str, object], artifact: dict[str, object]) -> None:
    """Structured fields in the shape the builder's toolchain receipt measures."""
    for side, version_pattern in (("compiler", CLANG_BANNER), ("linker", LD_VERSION)):
        row = build_identity.get(side)
        require(isinstance(row, dict), f"renderer build identity {side} is missing")
        attested_string(row, "vendor", side)
        attested_string(row, "version", side, version_pattern)
        path = attested_string(row, "path", side)
        require(path.startswith("/"), f"renderer build identity {side}.path must be absolute")
        require(isinstance(row.get("sha256"), str) and HEX_64.fullmatch(row["sha256"]) is not None,
                f"renderer build identity {side}.sha256 is not a SHA-256")
    sdk = build_identity.get("sdk")
    require(isinstance(sdk, dict), "renderer build identity sdk is missing")
    attested_string(sdk, "version", "sdk", SDK_VERSION)
    abi = build_identity.get("cpp_abi")
    require(isinstance(abi, dict), "renderer build identity cpp_abi is missing")
    require(abi.get("stdlib") == "libc++", "renderer build identity cpp_abi.stdlib must be libc++")
    attested_string(abi, "abi", "cpp_abi", re.compile(r"^[0-9]+$"))
    attested_string(abi, "libcpp_version", "cpp_abi", re.compile(r"^[0-9]{5,6}$"))
    validate_release_asset_record(artifact, build_identity.get("toolchain_receipt"),
                                  CONTENT_ADDRESSED_RECEIPT, "toolchain receipt")


def build_provenance_source_digest(attestation: dict[str, object], record: dict[str, object]) -> str:
    """The commit the build-provenance attestation must have been made at.

    Records a build wrote are attested by build-skia.yml at the builder commit
    the lock names, so no other main commit can attest the published archive.
    Records rebound from a rebuild are attested by rebind-provenance.yml at a
    later main commit, which the signed record names as source_digest.
    """
    if (attestation.get("legal_inventory") or {}).get("derived_from") is not None:
        digest = record.get("source_digest")
        require(isinstance(digest, str) and HEX_40.fullmatch(digest) is not None,
                "build provenance for rebound records must name the rebind commit (source_digest)")
        return digest
    digest = ((attestation.get("source_identities") or {}).get("builder") or {}).get("commit")
    require(isinstance(digest, str) and HEX_40.fullmatch(digest) is not None,
            "build provenance needs the builder commit the lock names")
    return digest


def validate_build_provenance_record(attestation: dict[str, object]) -> dict[str, object]:
    record = attestation.get("build_provenance")
    require(isinstance(record, dict), "an owner-attested record must name its build provenance")
    derived = (attestation.get("legal_inventory") or {}).get("derived_from") is not None
    expected_workflow = REBIND_SIGNER_WORKFLOW if derived else BUILDER_SIGNER_WORKFLOW
    require(record.get("repository") == BUILDER_REPOSITORY
            and record.get("signer_workflow") == expected_workflow
            and record.get("source_ref") == BUILDER_SOURCE_REF,
            "build provenance must name the pinned builder repository, workflow and ref")
    validate_release_asset_record(attestation["artifact"], record.get("bundle"),
                                  CONTENT_ADDRESSED_BUNDLE, "build provenance bundle")
    return record


def verify_toolchain_receipt_file(attestation: dict[str, object], path: Path) -> dict[str, object]:
    """Cross-check every build_identity field against the receipt that measured it."""
    identity = attestation["build_identity"]
    record = validate_release_asset_record(attestation["artifact"], identity.get("toolchain_receipt"),
                                           CONTENT_ADDRESSED_RECEIPT, "toolchain receipt")
    data = path.read_bytes()
    require(sha256_bytes(data) == record["sha256"],
            f"toolchain receipt SHA-256 {sha256_bytes(data)} differs from the attestation")
    receipt = json.loads(data)
    artifact = attestation["artifact"]
    require(receipt.get("schema") == TOOLCHAIN_RECEIPT_SCHEMA, "toolchain receipt schema is unsupported")
    bound = receipt.get("archive") or {}
    require(bound.get("sha256") == artifact["sha256"] and bound.get("name") == artifact["name"],
            "toolchain receipt describes a different archive")
    cxx = (receipt.get("compiler") or {}).get("cxx") or {}
    linker = receipt.get("linker") or {}
    for label, attested, measured in (("compiler", identity["compiler"], cxx),
                                      ("linker", identity["linker"], linker)):
        for field in ("version", "path", "sha256"):
            require(attested.get(field) == measured.get(field),
                    f"build identity {label}.{field} differs from the toolchain receipt")
    sdk_version = identity["sdk"]["version"]
    require((receipt.get("sdk") or {}).get("version") == sdk_version,
            "build identity sdk.version differs from the toolchain receipt")
    observed = receipt.get("observed_macho") or {}
    require(observed.get("sdk_versions") == [sdk_version],
            "the receipt's objects were not all built against the attested SDK")
    abi_rows = receipt.get("cpp_abi") or {}
    require(abi_rows, "toolchain receipt records no C++ ABI")
    for arch, row in abi_rows.items():
        require(row.get("libcpp_abi_version") == identity["cpp_abi"]["abi"]
                and row.get("libcpp_version") == identity["cpp_abi"]["libcpp_version"],
                f"build identity cpp_abi differs from the toolchain receipt for {arch}")
    return {"toolchain_receipt_sha256": record["sha256"],
            "toolchain_receipt_architectures": sorted(abi_rows)}


GhRunner = Callable[[list[str]], subprocess.CompletedProcess]


def run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def verify_build_provenance(
    attestation: dict[str, object], subjects: dict[str, Path], bundle: Path,
    runner: GhRunner = run_gh,
) -> dict[str, object]:
    """Verify the builder's Sigstore attestation covers each subject, offline from the bundle."""
    record = validate_build_provenance_record(attestation)
    source_digest = build_provenance_source_digest(attestation, record)
    data = bundle.read_bytes()
    require(sha256_bytes(data) == record["bundle"]["sha256"],
            "build provenance bundle SHA-256 differs from the attestation")
    require(shutil.which("gh") is not None or runner is not run_gh,
            "gh is required to verify the build provenance attestation")
    verified = []
    for label, path in sorted(subjects.items()):
        result = runner(["gh", "attestation", "verify", str(path), "--bundle", str(bundle),
                         "--repo", record["repository"],
                         "--signer-workflow", f"{record['signer_workflow']}@{record['source_ref']}",
                         "--source-digest", source_digest,
                         "--source-ref", record["source_ref"], "--deny-self-hosted-runners",
                         "--format", "json"])
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        require(result.returncode == 0,
                f"build provenance attestation does not cover the {label}: {detail[-400:]}")
        verified.append(label)
    return {"build_provenance_verified": verified}


FAT_CPU_NAMES = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "i386", 12: "arm"}
PLAIN_SUBTYPES = {"x86_64": 3, "arm64": 0, "i386": 3, "arm": 0}
NAMED_SUBTYPES = {("x86_64", 8): "x86_64h", ("arm64", 2): "arm64e", ("arm64", 1): "arm64v8"}


def fat_slice_name(cputype: int, cpusubtype: int) -> str:
    """A name unique to (cputype, cpusubtype), so no two slices share a key."""
    name = FAT_CPU_NAMES.get(cputype, f"cpu-{cputype:#x}")
    subtype = cpusubtype & 0x00FFFFFF
    if name in PLAIN_SUBTYPES and subtype == PLAIN_SUBTYPES[name]:
        return name
    return NAMED_SUBTYPES.get((name, subtype), f"{name}-subtype{subtype}")


def static_archive_contents(data: bytes) -> dict[str, dict[str, list]]:
    """Slice -> {"members": [(name, SHA-256)], "symbol_tables": [SHA-256]}.

    Independent of the builder's own parser. The symbol table is content: the
    linker resolves members through it. A fat file with two slices of the
    same CPU type and subtype is refused.
    """
    pieces: list[tuple[str, bytes]] = []
    magic = int.from_bytes(data[:4], "big") if len(data) >= 4 else 0
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        count = int.from_bytes(data[4:8], "big")
        offset = 8
        for _ in range(count):
            cputype = int.from_bytes(data[offset:offset + 4], "big")
            subtype = int.from_bytes(data[offset + 4:offset + 8], "big")
            if magic == 0xCAFEBABE:
                start = int.from_bytes(data[offset + 8:offset + 12], "big")
                size = int.from_bytes(data[offset + 12:offset + 16], "big")
                offset += 20
            else:
                start = int.from_bytes(data[offset + 8:offset + 16], "big")
                size = int.from_bytes(data[offset + 16:offset + 24], "big")
                offset += 32
            name = fat_slice_name(cputype, subtype)
            require(all(existing != name for existing, _ in pieces),
                    f"fat archive has more than one {name} slice")
            pieces.append((name, data[start:start + size]))
    else:
        pieces.append(("thin", data))
    result: dict[str, dict[str, list]] = {}
    for arch, piece in pieces:
        require(piece.startswith(AR_MAGIC), f"{arch} slice is not a static archive")
        members, tables, offset = [], [], len(AR_MAGIC)
        while offset < len(piece):
            if piece[offset:offset + 1] == b"\n":
                offset += 1
                continue
            header = piece[offset:offset + 60]
            require(len(header) == 60 and header[58:60] == b"`\n", "malformed static archive header")
            raw = header[:16].decode("ascii", "replace").rstrip(" ")
            size = int(header[48:58].decode("ascii").strip() or "0")
            body = piece[offset + 60:offset + 60 + size]
            offset += 60 + size + (size & 1)
            if raw.startswith("#1/"):
                length = int(raw[3:])
                name, body = body[:length].rstrip(b"\0").decode("utf-8", "replace"), body[length:]
            else:
                name = raw[:-1] if raw.endswith("/") else raw
            if raw == "//":
                continue
            if name.startswith("__.SYMDEF") or name in ("/", "/SYM64/"):
                tables.append(sha256_bytes(body))
                continue
            members.append((name, sha256_bytes(body)))
        result[arch] = {"members": members, "symbol_tables": tables}
    return result


def static_archive_members(data: bytes) -> dict[str, list[tuple[str, str]]]:
    return {arch: row["members"] for arch, row in static_archive_contents(data).items()}


def validate_renderer_attestation(
    renderer: dict[str, object],
    attestation: object,
    *,
    signature_path: Path = SIGNATURE_PATH,
    allowed_signers: Path = ALLOWED_SIGNERS_PATH,
    owner_principal: str | None = None,
    owner_fingerprint: str | None = None,
) -> dict[str, object]:
    """Validate the digest-to-provenance binding.

    A pending record describes the current state and cannot make the renderer
    eligible. A record that claims ``signed`` is always verified
    cryptographically, and one that claims ``exhaustive`` must name its
    content-addressed manifest, whatever the lock declares.
    """
    require(isinstance(attestation, dict),
            "renderer artifact digest has no provenance record")
    # The verifier attaches its results under "_" keys and leaves them out of
    # the signed payload; a record that already carries one would sign less
    # than it shows.
    reserved = sorted(underscore_keys(attestation))
    require(not reserved, "renderer provenance record uses reserved keys: " + ", ".join(reserved))
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
        validate_build_identity(build_identity, attestation["artifact"])
        validate_build_provenance_record(attestation)
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
    owner_principal: str | None = None,
    owner_fingerprint: str | None = None,
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
    parser.add_argument("--toolchain-receipt", type=Path,
                        help="the downloaded content-addressed Apple toolchain receipt asset")
    parser.add_argument("--provenance-bundle", type=Path,
                        help="the downloaded Sigstore bundle of the builder's provenance attestation")
    parser.add_argument("--rebuild-archive", type=Path,
                        help="the rebuild a derived manifest and receipt name (derived_from)")
    parser.add_argument("--rebuild-receipt", type=Path,
                        help="the rebuild's own toolchain receipt, to re-check the rebound one")
    parser.add_argument("--rebuild-manifest", type=Path,
                        help="the rebuild's own legal manifest, whose license mapping the rebound one must carry")
    parser.add_argument("--rebuild-provenance-bundle", type=Path,
                        help="the rebuild run's build-provenance bundle (build-skia.yml on main)")
    parser.add_argument("--release-json", type=Path,
                        help="GitHub's release JSON for the locked tag (assets[].digest)")
    parser.add_argument("--require-release-eligible", action="store_true",
                        help="fail unless the renderer is eligible, its signature verifies, and "
                             "--archive, --legal-manifest, --toolchain-receipt, "
                             "--provenance-bundle and --release-json prove every attested asset")
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
            archive_root = str(renderer["artifact"]["archive_root"]).rstrip("/") + "/"
            result.update(verify_legal_manifest_file(
                attestation, args.legal_manifest, args.archive,
                {archive_root + row["name"] for row in renderer["archives"]}))
        if args.toolchain_receipt:
            result.update(verify_toolchain_receipt_file(attestation, args.toolchain_receipt))
        if args.legal_manifest and args.toolchain_receipt and args.archive:
            if rebuild_proofs_required(args.legal_manifest, args.rebuild_archive, args.rebuild_receipt,
                                       args.rebuild_manifest, args.rebuild_provenance_bundle):
                result.update(verify_rebind(attestation, args.legal_manifest, args.toolchain_receipt,
                                            args.archive, args.rebuild_archive, args.rebuild_receipt,
                                            args.rebuild_manifest, args.rebuild_provenance_bundle))
        if args.provenance_bundle:
            subjects = {"legal manifest": args.legal_manifest, "toolchain receipt": args.toolchain_receipt,
                        "archive": args.archive}
            result.update(verify_build_provenance(
                attestation, {label: path for label, path in subjects.items() if path},
                args.provenance_bundle))
        if args.release_json:
            result.update(verify_release_assets(attestation, args.release_json))
        if args.require_release_eligible:
            require(renderer["release_eligibility"] == "eligible",
                    "GPU release is blocked: " + ", ".join(result["release_blockers"]))
            missing = [flag for flag, value in (
                ("--archive", args.archive), ("--legal-manifest", args.legal_manifest),
                ("--toolchain-receipt", args.toolchain_receipt),
                ("--provenance-bundle", args.provenance_bundle),
                ("--release-json", args.release_json)) if value is None]
            require(not missing, "release eligibility requires " + ", ".join(missing))
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
