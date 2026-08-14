"""Exact browser discovery and provenance validation for the web capture lane."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


SCHEMA = "vellum.browser-runtime-provenance.v1"
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+\.\d+)(?!\d)")
ACTION_REF_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


class BrowserProvenanceError(ValueError):
    """Raised when a browser cannot satisfy the exact runtime contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def browser_version(path: Path) -> str:
    try:
        completed = subprocess.run(
            [str(path), "--version"], text=True, capture_output=True, check=False,
        )
    except OSError as error:
        raise BrowserProvenanceError(f"cannot execute browser: {error}") from error
    rendered = (completed.stdout or completed.stderr).strip()
    match = VERSION_RE.search(rendered)
    if completed.returncode or match is None:
        raise BrowserProvenanceError(
            f"browser did not report a four-part version: {rendered or 'no output'}"
        )
    return match.group(1)


def validate_record(path: Path, browser: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserProvenanceError(f"cannot read browser provenance: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise BrowserProvenanceError("unsupported browser provenance schema")
    version = browser_version(browser)
    if record.get("version") != version or record.get("requested_version") != version:
        raise BrowserProvenanceError("browser version differs from its provenance record")
    digest = record.get("executable_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BrowserProvenanceError("browser provenance has an invalid executable digest")
    if sha256(browser) != digest:
        raise BrowserProvenanceError("browser executable differs from its provenance record")
    source = record.get("source_action")
    if not isinstance(source, str) or ACTION_REF_RE.fullmatch(source) is None:
        raise BrowserProvenanceError("browser provenance has an invalid source action ref")
    return record


def configured_provenance(browser: Path) -> tuple[bool, str]:
    provenance = os.environ.get("VELLUM_CHROME_PROVENANCE")
    if not provenance:
        return False, "no VELLUM_CHROME_PROVENANCE record (browser is unverified)"
    try:
        record = validate_record(Path(provenance).expanduser().resolve(), browser)
    except BrowserProvenanceError as error:
        return False, str(error)
    return True, f"Chrome {record['version']} ({record['executable_sha256'][:12]})"

