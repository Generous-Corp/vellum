#!/usr/bin/env python3
"""Verify Vellum's locked renderer and the builder records that vouch for it.

The renderer is a prebuilt Skia/Dawn archive published by skia-builder. It is
release eligible only on the builder's own records for the exact archive the
lock pins:

- a Sigstore build-provenance attestation, verified with real Sigstore
  (`gh attestation verify`), signed by `build-skia.yml` on `refs/heads/main`
  of `danielraffel/skia-builder` at the lock's builder commit on a
  GitHub-hosted runner, whose SLSA v1 statement covers the archive, its legal
  manifest and its toolchain receipt by name and digest;
- the content-addressed legal manifest, re-derived against the archive bytes
  and claiming an exhaustive inventory;
- the content-addressed Apple toolchain receipt, which supplies the compiler,
  linker, SDK and C++ ABI identity recorded in the attestation record.

No owner key is involved: the record in provenance/renderer-attestation.json
only names those records, and every name, digest and field in it is checked
against them.
"""

import sys

# Run as a script, the script's own directory comes first on sys.path, so a
# module or package beside it named like a standard-library module would run
# in place of that module. The verifier refuses to run that way; release
# workflows invoke it with -I. Checked before any other import, which is also
# why this module has no `from __future__` import.
if __name__ == "__main__" and not (sys.flags.isolated or getattr(sys.flags, "safe_path", False)):
    sys.exit("renderer-dependency-verify: run with python3 -I (or -P), so no module beside the "
             "script can shadow the standard library")

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "provenance/third-party-lock.json"
ATTESTATION_PATH = ROOT / "provenance/renderer-attestation.json"
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "maccatalyst"}

RECORD_SCHEMA = "vellum.renderer-provenance.v2"
STATE_PENDING = "pending-build-provenance"
STATE_ATTESTED = "builder-attested"
RECORD_KEYS = {"schema", "state", "artifact", "source_identities", "build_identity",
               "legal_inventory", "build_provenance"}
LEGAL_MANIFEST_SCHEMA = "skia-builder.legal-manifest.v1"
CONTENT_ADDRESSED_MANIFEST = re.compile(r"\.legal-manifest\.sha256-([0-9a-f]{64})\.json$")
CONTENT_ADDRESSED_RECEIPT = re.compile(r"\.apple-toolchain\.sha256-([0-9a-f]{64})\.json$")
CONTENT_ADDRESSED_BUNDLE = re.compile(
    r"\.provenance-attestation\.sha256-([0-9a-f]{64})\.sigstore\.json$")
TOOLCHAIN_RECEIPT_SCHEMA = "skia-builder.apple-toolchain-receipt.v1"
CLANG_BANNER = re.compile(r"^(Apple )?clang version [0-9][0-9A-Za-z.()_ -]*$")
LD_VERSION = re.compile(r"PROJECT:ld-[0-9][0-9.]*")
SDK_VERSION = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")

# The one builder whose build provenance can vouch for the renderer: this
# repository, this workflow, this ref, on a GitHub-hosted runner, with a SLSA
# v1 provenance statement issued through GitHub Actions' OIDC issuer. None of
# these values is read from the record; the record must repeat them.
BUILDER_REPOSITORY = "danielraffel/skia-builder"
BUILDER_REPOSITORY_URL = f"https://github.com/{BUILDER_REPOSITORY}"
BUILDER_WORKFLOW_PATH = ".github/workflows/build-skia.yml"
BUILDER_SIGNER_WORKFLOW = f"{BUILDER_REPOSITORY}/{BUILDER_WORKFLOW_PATH}"
BUILDER_SOURCE_REF = "refs/heads/main"
BUILDER_SIGNER_URI = f"https://github.com/{BUILDER_SIGNER_WORKFLOW}@{BUILDER_SOURCE_REF}"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
RUNNER_ENVIRONMENT = "github-hosted"
# GitHub's numeric ids for the builder repository and its owner. A repository
# deleted and re-created under the same name gets new ids.
BUILDER_REPOSITORY_ID = "1247180457"
BUILDER_OWNER_ID = "25807"
RUN_INVOCATION = re.compile(
    re.escape(BUILDER_REPOSITORY_URL) + r"/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)")

AR_MAGIC = b"!<arch>\n"
FAT_MAGICS = (0xCAFEBABE, 0xCAFEBABF)
PENDING_MARKERS = ("pending", "unknown")
LOCK_STATUS_BLOCKED = "incubation-byte-locked-not-release-attested"
LOCK_STATUS_ELIGIBLE = "byte-locked-renderer-release-attested"
SUBJECT_LABELS = ("archive", "legal manifest", "toolchain receipt")


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


def is_static_archive(data: bytes) -> bool:
    return data.startswith(AR_MAGIC) or int.from_bytes(data[:4], "big") in FAT_MAGICS


def release_asset_url(artifact: dict[str, object], name: str) -> str:
    return f"{artifact['repository']}/releases/download/{artifact['release_tag']}/{name}"


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
    require(name == str(artifact["name"]).removesuffix(".zip") + match.group(0),
            f"{label} asset does not belong to the locked archive")
    require(record.get("url") == release_asset_url(artifact, name),
            f"{label} URL is not the locked release's asset")
    return record


def zip_member_rows(manifest: dict, label: str) -> dict[str, str]:
    """zip member name -> sha256, refusing a manifest that lists a name twice."""
    rows = manifest.get("zip_members") or []
    names = [row.get("name") for row in rows]
    require(len(names) == len(set(names)), f"the {label} lists a zip member more than once")
    return {row.get("name"): row.get("sha256") for row in rows}


# -- legal manifest -----------------------------------------------------------

def validate_legal_inventory_record(attestation: dict[str, object]) -> dict[str, object]:
    """Check the exhaustive inventory names one content-addressed release asset."""
    inventory = attestation["legal_inventory"]
    record = validate_release_asset_record(attestation["artifact"], inventory.get("manifest"),
                                           CONTENT_ADDRESSED_MANIFEST, "legal manifest")
    require(inventory.get("manifest_sha256") == record["sha256"],
            "legal inventory manifest_sha256 differs from its manifest asset")
    return record


def verify_legal_manifest_file(
    attestation: dict[str, object], manifest_path: Path, archive: Path | None = None,
    locked_archives: set[str] | None = None,
) -> dict[str, object]:
    """Verify the downloaded manifest is the attested one and covers the archive.

    With the archive, every zip member and every member and symbol table of
    every static-archive slice is re-derived from the archive bytes and must
    be listed, with its digest, exactly as the manifest lists it.
    """
    record = validate_legal_inventory_record(attestation)
    data = manifest_path.read_bytes()
    require(sha256_bytes(data) == record["sha256"],
            f"legal manifest SHA-256 {sha256_bytes(data)} differs from the attestation")
    manifest = json.loads(data)
    require(isinstance(manifest, dict), "legal manifest is not a JSON object")
    artifact = attestation["artifact"]
    require(manifest.get("schema") == LEGAL_MANIFEST_SCHEMA, "legal manifest schema is unsupported")
    require(manifest.get("exhaustive") is True and manifest.get("gaps") == [],
            "legal manifest does not claim an exhaustive inventory")
    described = manifest.get("archive")
    require(isinstance(described, dict) and described.get("sha256") == artifact["sha256"]
            and described.get("name") == artifact["name"],
            "legal manifest describes a different archive")
    identities = attestation["source_identities"]
    revisions = manifest.get("source_revisions")
    require(isinstance(revisions, dict), "legal manifest records no source revisions")
    for name in ("skia", "dawn"):
        row = revisions.get(name)
        require(isinstance(row, dict) and row.get("commit") == identities[name]["commit"]
                and row.get("expected") == identities[name]["commit"],
                f"legal manifest {name} revision differs from the lock")
    licenses = manifest.get("licenses")
    require(isinstance(licenses, dict) and licenses, "legal manifest has no license texts")
    for key, row in licenses.items():
        require(isinstance(row, dict) and isinstance(row.get("text"), str) and row["text"].strip()
                and row.get("sha256") == sha256_bytes(row["text"].encode("utf-8")),
                f"legal manifest license {key} has no text matching its digest")
    rows = manifest.get("zip_members")
    require(isinstance(rows, list) and rows, "legal manifest inventories no zip member")
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("name"), str)
                and isinstance(row.get("sha256"), str) and HEX_64.fullmatch(row["sha256"]) is not None
                and row.get("kind") in {"static-archive", "packaged-file", "builder-metadata"},
                "legal manifest zip member row is malformed")
        if row["kind"] != "static-archive":
            require(isinstance(row.get("licenses"), list) and row["licenses"]
                    and all(item in licenses for item in row["licenses"]),
                    f"legal manifest leaves {row.get('name')} without a license")
    listed = zip_member_rows(manifest, "legal manifest")
    static_archives = manifest.get("static_archives")
    require(isinstance(static_archives, dict) and static_archives,
            "legal manifest inventories no static archive")
    if locked_archives is not None:
        require(set(static_archives) == locked_archives,
                "legal manifest static archives differ from the lock's archives")
    listed_archives = {row["name"] for row in rows if row.get("kind") == "static-archive"}
    require(set(static_archives) == listed_archives,
            "legal manifest static-archive inventory differs from its zip members")
    for name, row in static_archives.items():
        slices = row.get("slices") if isinstance(row, dict) else None
        require(isinstance(slices, dict) and slices, f"legal manifest lists no slices for {name}")
        for arch, piece in slices.items():
            require(isinstance(piece, dict) and isinstance(piece.get("members"), list)
                    and piece["members"], f"legal manifest lists no members for {name}[{arch}]")
            for member in piece["members"]:
                require(isinstance(member, dict) and member.get("objects") and member.get("licenses")
                        and all(item in licenses for item in member["licenses"]),
                        f"legal manifest leaves {name}[{arch}] {member.get('name')} unresolved")
    if archive is not None:
        with zipfile.ZipFile(archive) as handle:
            members = safe_members(handle)
            actual = {}
            for name, info in members.items():
                if info.is_dir():
                    continue
                data = handle.read(info)
                actual[name] = sha256_bytes(data)
                if not is_static_archive(data):
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
        require(actual == listed, "legal manifest zip-member inventory differs from the archive")
    return {"legal_manifest_sha256": record["sha256"],
            "legal_manifest_members": len(rows),
            "legal_manifest_licenses": len(licenses)}


# -- toolchain receipt --------------------------------------------------------

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


def target_architecture(target: object) -> str:
    """The architecture a lock target such as ``macos-arm64`` names."""
    require(isinstance(target, str) and target.startswith("macos-"),
            f"renderer target {target!r} is not a macOS target")
    return target.removeprefix("macos-")


def verify_toolchain_receipt_file(
    attestation: dict[str, object], path: Path, renderer: dict[str, object] | None = None,
) -> dict[str, object]:
    """Cross-check every build_identity field against the receipt that measured it.

    With the lock's renderer entry, the receipt must also have built the
    locked target, configuration, SDK and minimum OS.
    """
    identity = attestation["build_identity"]
    record = validate_release_asset_record(attestation["artifact"], identity.get("toolchain_receipt"),
                                           CONTENT_ADDRESSED_RECEIPT, "toolchain receipt")
    data = path.read_bytes()
    require(sha256_bytes(data) == record["sha256"],
            f"toolchain receipt SHA-256 {sha256_bytes(data)} differs from the attestation")
    receipt = json.loads(data)
    require(isinstance(receipt, dict), "toolchain receipt is not a JSON object")
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
    require(isinstance(abi_rows, dict) and abi_rows, "toolchain receipt records no C++ ABI")
    for arch, row in abi_rows.items():
        require(isinstance(row, dict) and row.get("stdlib") == "libc++"
                and row.get("libcpp_abi_version") == identity["cpp_abi"]["abi"]
                and row.get("libcpp_version") == identity["cpp_abi"]["libcpp_version"],
                f"build identity cpp_abi differs from the toolchain receipt for {arch}")
    target = receipt.get("target") or {}
    require(target.get("platform") == "mac"
            and target.get("configuration") == identity.get("configuration"),
            "toolchain receipt built another platform or configuration than the record names")
    architectures = target.get("architectures")
    require(isinstance(architectures, list) and set(architectures) == set(abi_rows),
            "toolchain receipt C++ ABI rows differ from its target architectures")
    require(target_architecture(identity.get("target")) in architectures,
            f"toolchain receipt did not build {identity.get('target')}")
    if renderer is not None:
        locked = renderer["artifact"]
        require(identity.get("target") == locked.get("target")
                and identity.get("configuration") == locked.get("configuration"),
                "renderer build identity target or configuration differs from the lock")
        observations = renderer.get("observed_macho") or {}
        for name in ("skia", "dawn"):
            row = observations.get(name) or {}
            require(row.get("sdk") == sdk_version and row.get("minimum_os") == target.get("minimum_os"),
                    f"the lock's observed {name} Mach-O SDK or minimum OS differs from the receipt")
    return {"toolchain_receipt_sha256": record["sha256"],
            "toolchain_receipt_architectures": sorted(abi_rows)}


# -- build provenance ---------------------------------------------------------

GhRunner = Callable[[list[str]], subprocess.CompletedProcess]


def run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def run_invocation_url(run_id: object, run_attempt: object) -> str:
    return f"{BUILDER_REPOSITORY_URL}/actions/runs/{run_id}/attempts/{run_attempt}"


def validate_build_provenance_record(attestation: dict[str, object]) -> dict[str, object]:
    """The record must name the pinned builder; it cannot choose another one."""
    record = attestation.get("build_provenance")
    require(isinstance(record, dict), "a builder-attested record must name its build provenance")
    require(set(record) == {"repository", "signer_workflow", "source_ref", "predicate_type", "run",
                            "bundle"},
            "build provenance names unexpected or missing fields")
    require(record.get("repository") == BUILDER_REPOSITORY
            and record.get("signer_workflow") == BUILDER_SIGNER_WORKFLOW
            and record.get("source_ref") == BUILDER_SOURCE_REF
            and record.get("predicate_type") == PREDICATE_TYPE,
            "build provenance must name the pinned builder repository, workflow, ref and predicate")
    run = record.get("run")
    require(isinstance(run, dict) and set(run) == {"run_id", "run_attempt"}
            and all(isinstance(run.get(key), int) and not isinstance(run.get(key), bool)
                    and run[key] > 0 for key in ("run_id", "run_attempt")),
            "build provenance must name the run that attested it (run.run_id, run.run_attempt)")
    validate_release_asset_record(attestation["artifact"], record.get("bundle"),
                                  CONTENT_ADDRESSED_BUNDLE, "build provenance bundle")
    return record


def bundle_statement(bundle: bytes) -> dict[str, object]:
    """The in-toto statement a Sigstore bundle's DSSE envelope carries."""
    try:
        parsed = json.loads(bundle)
        envelope = parsed["dsseEnvelope"]
        require(envelope.get("payloadType") == DSSE_PAYLOAD_TYPE,
                "build provenance bundle does not carry an in-toto statement")
        statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise VerificationError(f"build provenance bundle is not a DSSE bundle: {error}") from error
    require(isinstance(statement, dict), "build provenance statement is not a JSON object")
    return statement


def verify_statement(statement: dict[str, object], subjects: dict[str, tuple[str, str]],
                     builder_commit: str, invocation: str) -> None:
    """The SLSA statement must name each subject and the pinned build exactly."""
    require(statement.get("_type") == STATEMENT_TYPE,
            "build provenance statement is not an in-toto v1 statement")
    require(statement.get("predicateType") == PREDICATE_TYPE,
            "build provenance statement is not SLSA v1 provenance")
    listed = statement.get("subject")
    require(isinstance(listed, list), "build provenance statement names no subjects")
    named: dict[str, set[str]] = {}
    for row in listed:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            named.setdefault(row["name"], set()).add(str((row.get("digest") or {}).get("sha256")))
    for label, (name, digest) in subjects.items():
        require(named.get(name) == {digest},
                f"build provenance statement does not name the {label} {name} with digest {digest}")
    predicate = statement.get("predicate")
    require(isinstance(predicate, dict), "build provenance statement has no predicate")
    definition = predicate.get("buildDefinition") or {}
    require(definition.get("buildType") == BUILD_TYPE,
            "build provenance was not produced by a GitHub Actions workflow build")
    workflow = (definition.get("externalParameters") or {}).get("workflow")
    require(workflow == {"ref": BUILDER_SOURCE_REF, "repository": BUILDER_REPOSITORY_URL,
                         "path": BUILDER_WORKFLOW_PATH},
            f"build provenance names workflow {workflow}, not {BUILDER_SIGNER_URI}")
    github = (definition.get("internalParameters") or {}).get("github") or {}
    require(github.get("runner_environment") == RUNNER_ENVIRONMENT,
            "build provenance was not produced on a GitHub-hosted runner")
    require(github.get("repository_id") == BUILDER_REPOSITORY_ID
            and github.get("repository_owner_id") == BUILDER_OWNER_ID,
            "build provenance names another repository or owner id")
    source = f"git+{BUILDER_REPOSITORY_URL}@{BUILDER_SOURCE_REF}"
    commits = [((row or {}).get("digest") or {}).get("gitCommit")
               for row in definition.get("resolvedDependencies") or [] if (row or {}).get("uri") == source]
    require(commits == [builder_commit],
            f"build provenance was built from {commits}, not builder commit {builder_commit}")
    details = predicate.get("runDetails") or {}
    require((details.get("builder") or {}).get("id") == BUILDER_SIGNER_URI,
            "build provenance names another builder")
    require((details.get("metadata") or {}).get("invocationId") == invocation,
            f"build provenance names run {(details.get('metadata') or {}).get('invocationId')}, "
            f"not the record's {invocation}")


CERTIFICATE_FIELDS = {
    "issuer": OIDC_ISSUER,
    "buildSignerURI": BUILDER_SIGNER_URI,
    "sourceRepositoryURI": BUILDER_REPOSITORY_URL,
    "sourceRepositoryRef": BUILDER_SOURCE_REF,
    "githubWorkflowRepository": BUILDER_REPOSITORY,
    "githubWorkflowRef": BUILDER_SOURCE_REF,
    "runnerEnvironment": RUNNER_ENVIRONMENT,
    "sourceRepositoryIdentifier": BUILDER_REPOSITORY_ID,
    "sourceRepositoryOwnerIdentifier": BUILDER_OWNER_ID,
}


def verify_gh_result(stdout: str, label: str, statement: dict[str, object],
                     builder_commit: str, invocation: str) -> None:
    """Check what Sigstore verified: the certificate identity and the statement."""
    try:
        results = json.loads(stdout or "")
    except ValueError as error:
        raise VerificationError(f"gh attestation verify printed no result for the {label}") from error
    require(isinstance(results, list) and len(results) == 1,
            f"gh attestation verify must verify exactly one attestation for the {label}")
    result = (results[0] or {}).get("verificationResult") or {}
    certificate = (result.get("signature") or {}).get("certificate") or {}
    expected = dict(CERTIFICATE_FIELDS, sourceRepositoryDigest=builder_commit,
                    runInvocationURI=invocation)
    for field, value in expected.items():
        require(certificate.get(field) == value,
                f"the {label}'s certificate {field} is {certificate.get(field)!r}, not {value!r}")
    require(result.get("statement") == statement,
            f"the statement Sigstore verified for the {label} is not the bundle's statement")


def verify_build_provenance(
    attestation: dict[str, object], subjects: dict[str, Path], bundle: Path,
    runner: GhRunner | None = None,
) -> dict[str, object]:
    """Verify the builder's Sigstore attestation covers the archive and both records.

    `gh attestation verify` does the cryptography (certificate chain, Rekor
    inclusion, signature over the statement) offline from the bundle, pinned
    to the builder repository, signer workflow and ref, source commit, SLSA
    predicate, OIDC issuer and a GitHub-hosted runner. Its result is then
    re-checked here field by field, and the statement it verified must be the
    bundle's own, naming each subject by name and digest.
    """
    record = validate_build_provenance_record(attestation)
    require(set(subjects) == set(SUBJECT_LABELS),
            "build provenance must cover the archive, legal manifest and toolchain receipt")
    builder_commit = ((attestation.get("source_identities") or {}).get("builder") or {}).get("commit")
    require(isinstance(builder_commit, str) and HEX_40.fullmatch(builder_commit) is not None,
            "build provenance needs the builder commit the lock names")
    data = bundle.read_bytes()
    require(sha256_bytes(data) == record["bundle"]["sha256"],
            "build provenance bundle SHA-256 differs from the attestation")
    if runner is None:
        require(shutil.which("gh") is not None,
                "gh is required to verify the build provenance attestation")
        runner = run_gh
    invocation = run_invocation_url(record["run"]["run_id"], record["run"]["run_attempt"])
    expected = {
        "archive": (attestation["artifact"]["name"], attestation["artifact"]["sha256"]),
        "legal manifest": (attestation["legal_inventory"]["manifest"]["name"],
                           attestation["legal_inventory"]["manifest"]["sha256"]),
        "toolchain receipt": (attestation["build_identity"]["toolchain_receipt"]["name"],
                              attestation["build_identity"]["toolchain_receipt"]["sha256"]),
    }
    for label, path in subjects.items():
        require(sha256_file(path) == expected[label][1],
                f"the {label} given is not the one the record names")
    statement = bundle_statement(data)
    verify_statement(statement, expected, builder_commit, invocation)
    for label in SUBJECT_LABELS:
        verify_subject_with_sigstore(label, subjects[label], bundle, statement, builder_commit,
                                     invocation, runner)
    return {"build_provenance_verified": list(SUBJECT_LABELS), "build_provenance_run": invocation,
            "gh": shutil.which("gh")}


def verify_subject_with_sigstore(label: str, subject: Path, bundle: Path, statement: dict[str, object],
                                 builder_commit: str, invocation: str, runner: GhRunner) -> None:
    """Real Sigstore verification of one subject, then a check of what it verified."""
    result = runner(["gh", "attestation", "verify", str(subject), "--bundle", str(bundle),
                     "--repo", BUILDER_REPOSITORY,
                     "--signer-workflow", f"{BUILDER_SIGNER_WORKFLOW}@{BUILDER_SOURCE_REF}",
                     "--source-ref", BUILDER_SOURCE_REF, "--source-digest", builder_commit,
                     "--predicate-type", PREDICATE_TYPE, "--cert-oidc-issuer", OIDC_ISSUER,
                     "--deny-self-hosted-runners", "--format", "json"])
    detail = ((result.stdout or "") + (result.stderr or "")).strip()
    require(result.returncode == 0,
            f"build provenance attestation does not cover the {label}: {detail[-400:]}")
    verify_gh_result(result.stdout, label, statement, builder_commit, invocation)


def verify_release_assets(attestation: dict[str, object], release_json: Path) -> dict[str, object]:
    """Check GitHub's own asset digests for the archive and every record."""
    release = json.loads(release_json.read_text(encoding="utf-8"))
    artifact = attestation["artifact"]
    require(isinstance(release, dict) and release.get("tag_name") == artifact["release_tag"],
            "release JSON is not the locked release tag")
    assets: dict[str, list[dict]] = {}
    for row in release.get("assets", []):
        if isinstance(row, dict):
            assets.setdefault(row.get("name"), []).append(row)
    expected = [(artifact["name"], artifact["sha256"])]
    for asset in (attestation["legal_inventory"].get("manifest"),
                  attestation["build_identity"].get("toolchain_receipt"),
                  (attestation.get("build_provenance") or {}).get("bundle")):
        require(isinstance(asset, dict), "the record names a release asset it does not describe")
        expected.append((asset.get("name"), asset.get("sha256")))
    for name, digest in expected:
        rows = assets.get(name) or []
        require(len(rows) == 1, f"release asset is missing or duplicated: {name}")
        require(rows[0].get("digest") == f"sha256:{digest}",
                f"release asset {name} digest {rows[0].get('digest')} differs from sha256:{digest}")
    return {"release_assets_verified": len(expected)}


# -- the record and the lock ----------------------------------------------------

def validate_renderer_attestation(renderer: dict[str, object], attestation: object) -> dict[str, object]:
    """Validate the record's shape and its binding to the lock.

    A pending record describes the current state and cannot make the renderer
    eligible. A builder-attested record must name every builder record in its
    content-addressed form; whether those records verify is checked by
    prove_release_eligibility against the downloaded files.
    """
    require(isinstance(attestation, dict),
            "renderer artifact digest has no provenance record")
    reserved = sorted(underscore_keys(attestation))
    require(not reserved, "renderer provenance record uses reserved keys: " + ", ".join(reserved))
    require(attestation.get("schema") == RECORD_SCHEMA,
            "renderer provenance schema is missing or unsupported")
    unknown = sorted(set(attestation) - RECORD_KEYS)
    require(not unknown, "renderer provenance record has unexpected fields: " + ", ".join(unknown))
    artifact = attestation.get("artifact")
    locked_artifact = renderer.get("artifact")
    require(isinstance(artifact, dict) and isinstance(locked_artifact, dict),
            "renderer provenance artifact identity is missing")
    require(artifact.get("sha256") == locked_artifact.get("sha256"),
            "renderer artifact digest has no matching provenance record")
    for field in ("repository", "release_tag", "name"):
        require(artifact.get(field) == locked_artifact.get(field),
                f"renderer provenance artifact {field} differs from the lock")
    require(artifact.get("repository") == BUILDER_REPOSITORY_URL,
            "renderer artifact is not published by the pinned builder repository")

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
    state = attestation.get("state")
    require(state in {STATE_PENDING, STATE_ATTESTED}, "renderer attestation state is invalid")
    if legal_inventory["state"] == "exhaustive":
        validate_legal_inventory_record(attestation)
    if state == STATE_ATTESTED:
        require(legal_inventory["state"] == "exhaustive",
                "a builder-attested record must carry an exhaustive legal inventory")
        require(locked_artifact.get("builder_commit") == locked_identities["builder"]["commit"],
                "the lock's artifact builder_commit differs from its builder source identity")
        validate_build_identity(build_identity, artifact)
        validate_build_provenance_record(attestation)
    return copy.deepcopy(attestation)


def load_lock(path: Path, attestation_path: Path = ATTESTATION_PATH) -> tuple[dict[str, object], dict[str, object]]:
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
    require(artifact.get("url") == release_asset_url(artifact, str(artifact.get("name"))),
            "renderer artifact URL is not the locked release's asset")
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
    attestation = validate_renderer_attestation(renderer, attestation)
    if eligible:
        require(attestation["state"] == STATE_ATTESTED,
                "renderer eligibility requires a builder-attested provenance record")
        require(completeness.get("legal_manifest_sha256")
                == attestation["legal_inventory"]["manifest_sha256"],
                "the lock's exhaustive claim names a different legal manifest")
    renderer["_attestation"] = attestation
    return lock, renderer


def locked_archive_paths(renderer: dict[str, object]) -> set[str]:
    archive_root = str(renderer["artifact"]["archive_root"]).rstrip("/") + "/"
    return {archive_root + row["name"] for row in renderer["archives"]}


def prove_builder_records(
    renderer: dict[str, object], attestation: dict[str, object], *, archive: Path,
    legal_manifest: Path, toolchain_receipt: Path, provenance_bundle: Path, release_json: Path,
    require_macho: bool = False, runner: GhRunner | None = None,
) -> dict[str, object]:
    """Every proof a builder-attested record rests on, against the downloaded files.

    None of these is optional: the archive bytes against the lock, the legal
    manifest against the archive, the toolchain receipt against the record and
    the lock, the Sigstore build provenance over all three, and GitHub's own
    release-asset digests for all four assets.
    """
    require(attestation.get("state") == STATE_ATTESTED,
            "renderer eligibility requires a builder-attested provenance record")
    result: dict[str, object] = {}
    result.update(verify_archive(archive, renderer, require_macho))
    result.update(verify_legal_manifest_file(attestation, legal_manifest, archive,
                                             locked_archive_paths(renderer)))
    result.update(verify_toolchain_receipt_file(attestation, toolchain_receipt, renderer))
    result.update(verify_build_provenance(
        attestation, {"archive": archive, "legal manifest": legal_manifest,
                      "toolchain receipt": toolchain_receipt}, provenance_bundle, runner))
    result.update(verify_release_assets(attestation, release_json))
    return result


def build_record(renderer: dict[str, object], *, legal_manifest: Path, toolchain_receipt: Path,
                 provenance_bundle: Path) -> dict[str, object]:
    """A builder-attested record derived only from the lock and the builder's records."""
    artifact = renderer["artifact"]
    named = {"repository": artifact["repository"], "release_tag": artifact["release_tag"],
             "name": artifact["name"], "sha256": artifact["sha256"]}

    def asset(path: Path, pattern: re.Pattern[str], label: str) -> dict[str, object]:
        digest = sha256_file(path)
        name = path.name
        record = {"name": name, "sha256": digest, "url": release_asset_url(named, name)}
        return validate_release_asset_record(named, record, pattern, label)

    receipt = json.loads(toolchain_receipt.read_bytes())
    cxx = (receipt.get("compiler") or {}).get("cxx") or {}
    linker = receipt.get("linker") or {}
    abi_rows = receipt.get("cpp_abi") or {}
    arch = target_architecture(artifact.get("target"))
    abi = abi_rows.get(arch) or {}
    statement = bundle_statement(provenance_bundle.read_bytes())
    invocation = str(((statement.get("predicate") or {}).get("runDetails") or {})
                     .get("metadata", {}).get("invocationId"))
    match = RUN_INVOCATION.fullmatch(invocation)
    require(match is not None, f"build provenance names no builder run invocation: {invocation}")
    manifest = asset(legal_manifest, CONTENT_ADDRESSED_MANIFEST, "legal manifest")
    vendor = "Apple" if str(cxx.get("version", "")).startswith("Apple ") else "LLVM"
    return {
        "schema": RECORD_SCHEMA,
        "state": STATE_ATTESTED,
        "artifact": named,
        "source_identities": {name: {"repository": row.get("repository"), "commit": row.get("commit")}
                              for name, row in renderer["source_identities"].items()
                              if name in ("skia", "dawn", "builder")},
        "build_identity": {
            "target": artifact.get("target"),
            "configuration": artifact.get("configuration"),
            "compiler": {"vendor": vendor, "version": cxx.get("version"), "path": cxx.get("path"),
                         "sha256": cxx.get("sha256")},
            "linker": {"vendor": "Apple", "version": linker.get("version"), "path": linker.get("path"),
                       "sha256": linker.get("sha256")},
            "sdk": {"version": (receipt.get("sdk") or {}).get("version")},
            "cpp_abi": {"stdlib": abi.get("stdlib"), "abi": abi.get("libcpp_abi_version"),
                        "libcpp_version": abi.get("libcpp_version")},
            "toolchain_receipt": asset(toolchain_receipt, CONTENT_ADDRESSED_RECEIPT, "toolchain receipt"),
        },
        "legal_inventory": {"state": "exhaustive", "manifest_sha256": manifest["sha256"],
                            "manifest": manifest},
        "build_provenance": {
            "repository": BUILDER_REPOSITORY, "signer_workflow": BUILDER_SIGNER_WORKFLOW,
            "source_ref": BUILDER_SOURCE_REF, "predicate_type": PREDICATE_TYPE,
            "run": {"run_id": int(match.group(1)), "run_attempt": int(match.group(2))},
            "bundle": asset(provenance_bundle, CONTENT_ADDRESSED_BUNDLE, "build provenance bundle"),
        },
    }


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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION_PATH)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--require-macho", action="store_true")
    parser.add_argument("--legal-manifest", type=Path,
                        help="the downloaded content-addressed legal manifest asset")
    parser.add_argument("--toolchain-receipt", type=Path,
                        help="the downloaded content-addressed Apple toolchain receipt asset")
    parser.add_argument("--provenance-bundle", type=Path,
                        help="the downloaded Sigstore bundle of the builder's provenance attestation")
    parser.add_argument("--release-json", type=Path,
                        help="GitHub's release JSON for the locked tag (assets[].digest)")
    parser.add_argument("--require-release-eligible", action="store_true",
                        help="fail unless the lock declares the renderer eligible and --archive, "
                             "--legal-manifest, --toolchain-receipt, --provenance-bundle and "
                             "--release-json prove every builder record")
    parser.add_argument("--emit-attestation", type=Path, metavar="PATH",
                        help="derive the builder-attested record for the locked archive from the "
                             "builder's records, prove it, and write it to PATH")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.emit_attestation and args.require_release_eligible:
        parser.error("--emit-attestation writes a record; it cannot also prove release eligibility")
    proofs = {"--archive": args.archive, "--legal-manifest": args.legal_manifest,
              "--toolchain-receipt": args.toolchain_receipt,
              "--provenance-bundle": args.provenance_bundle, "--release-json": args.release_json}
    try:
        if args.emit_attestation:
            missing = [flag for flag, value in proofs.items() if value is None]
            require(not missing, "--emit-attestation requires " + ", ".join(missing))
            raw = json.loads(args.lock.read_text(encoding="utf-8"))
            renderer = ((raw.get("runtime_toolchain_dependencies") or {}).get("renderer") or {})
            record = build_record(renderer, legal_manifest=args.legal_manifest,
                                  toolchain_receipt=args.toolchain_receipt,
                                  provenance_bundle=args.provenance_bundle)
            with tempfile.TemporaryDirectory(prefix="vellum-renderer-record-") as temporary:
                candidate = Path(temporary) / "renderer-attestation.json"
                candidate.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
                _, renderer = load_lock(args.lock, candidate)
                result = prove_builder_records(
                    renderer, renderer["_attestation"], archive=args.archive,
                    legal_manifest=args.legal_manifest, toolchain_receipt=args.toolchain_receipt,
                    provenance_bundle=args.provenance_bundle, release_json=args.release_json,
                    require_macho=args.require_macho)
                args.emit_attestation.write_text(candidate.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
            result.update({"ok": True, "emitted": str(args.emit_attestation)})
            print(json.dumps(result, sort_keys=True, separators=(",", ":")) if args.json
                  else f"Wrote the proven builder-attested record to {args.emit_attestation}.")
            return 0

        _, renderer = load_lock(args.lock, args.attestation)
        attestation = renderer["_attestation"]
        result: dict[str, object] = {
            "ok": True,
            "lock": str(args.lock),
            "release_eligibility": renderer["release_eligibility"],
            "release_blockers": [row["id"] for row in renderer["release_blockers"]],
            "attestation_state": attestation["state"],
            "builder_records_proven": False,
        }
        if args.require_release_eligible:
            require(renderer["release_eligibility"] == "eligible",
                    "GPU release is blocked: " + ", ".join(result["release_blockers"]))
            missing = [flag for flag, value in proofs.items() if value is None]
            require(not missing, "release eligibility requires " + ", ".join(missing))
            result.update(prove_builder_records(
                renderer, attestation, archive=args.archive, legal_manifest=args.legal_manifest,
                toolchain_receipt=args.toolchain_receipt, provenance_bundle=args.provenance_bundle,
                release_json=args.release_json, require_macho=args.require_macho))
            result["builder_records_proven"] = True
        else:
            if args.archive:
                result.update(verify_archive(args.archive, renderer, args.require_macho))
            if args.legal_manifest:
                result.update(verify_legal_manifest_file(
                    attestation, args.legal_manifest, args.archive, locked_archive_paths(renderer)))
            if args.toolchain_receipt:
                result.update(verify_toolchain_receipt_file(attestation, args.toolchain_receipt,
                                                            renderer))
            if args.provenance_bundle:
                subjects = {"archive": args.archive, "legal manifest": args.legal_manifest,
                            "toolchain receipt": args.toolchain_receipt}
                absent = [label for label, path in subjects.items() if path is None]
                require(not absent, "--provenance-bundle needs the "
                        + ", ".join(absent) + " it must cover")
                result.update(verify_build_provenance(attestation, subjects, args.provenance_bundle))
            if args.release_json:
                result.update(verify_release_assets(attestation, args.release_json))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile,
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
        if result["builder_records_proven"]:
            print("GPU release is eligible: the builder's build provenance, legal manifest and "
                  "toolchain receipt verified for the locked archive.")
        elif renderer["release_eligibility"] == "eligible":
            print("The lock declares the renderer eligible; --require-release-eligible with the "
                  "builder's records proves it.")
        else:
            print("GPU release eligibility remains blocked by declared provenance gaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
