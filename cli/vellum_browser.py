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
CHROME_PRODUCT = "Google Chrome"
HEADLESS_SHELL_PRODUCT = "chrome-headless-shell"
PRODUCTS = (CHROME_PRODUCT, HEADLESS_SHELL_PRODUCT)
HEADLESS_SHELL_NAMES = ("chrome-headless-shell", "chrome-headless-shell.exe")
SHELL_PATH_ENV = "VELLUM_CHROME_HEADLESS_SHELL_PATH"
SHELL_PROVENANCE_ENV = "VELLUM_CHROME_HEADLESS_SHELL_PROVENANCE"
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


def validate_record(path: Path, browser: Path, *, product: str | None = None) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BrowserProvenanceError(f"cannot read browser provenance: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise BrowserProvenanceError("unsupported browser provenance schema")
    if record.get("product", CHROME_PRODUCT) not in PRODUCTS:
        raise BrowserProvenanceError("browser provenance names an unknown product")
    if product is not None and record.get("product") != product:
        raise BrowserProvenanceError(f"browser provenance does not describe {product}")
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



def browser_kind(path: Path | str) -> str:
    """``headless-shell`` for chrome-headless-shell, else ``chrome``."""
    return "headless-shell" if Path(path).name in HEADLESS_SHELL_NAMES else "chrome"


def headless_mode_arguments(path: Path | str) -> list[str]:
    """The flag that makes `path` headless.

    chrome-headless-shell is headless by construction and rejects the full
    browser's ``--headless=new``.
    """
    return [] if browser_kind(path) == "headless-shell" else ["--headless=new"]


def sibling_headless_shell(chrome: Path) -> Path | None:
    """The shell a Chrome for Testing cache keeps beside `chrome`, if any.

    The cache layout is ``<root>/chrome/<platform>-<version>/chrome-<arch>/...``
    with the shell of the same release at
    ``<root>/chrome-headless-shell/<platform>-<version>/chrome-headless-shell-<arch>/``.
    """
    parts = chrome.parts
    for index in range(len(parts) - 2, 0, -1):
        if parts[index] != "chrome" or not parts[index + 2].startswith("chrome-"):
            continue
        arch = parts[index + 2][len("chrome-"):]
        root = Path(*parts[:index])
        for name in HEADLESS_SHELL_NAMES:
            candidate = (root / HEADLESS_SHELL_PRODUCT / parts[index + 1]
                         / f"{HEADLESS_SHELL_PRODUCT}-{arch}" / name)
            if candidate.is_file():
                return candidate
        return None
    return None


def prefer_headless_shell(chrome: Path, *, strict: bool,
                          environ: dict[str, str] | None = None) -> Path:
    """Return the pinned headless shell from `chrome`'s release, else `chrome`.

    The full browser connects to the window server even with ``--headless=new``
    and can stall there behind another application's modal UI; the shell does
    not. A shell is used only when it is the same release as the pinned Chrome.
    A shell named through VELLUM_CHROME_HEADLESS_SHELL_PATH that cannot be used
    is refused rather than ignored. With strict provenance the shell must match
    its own receipt (VELLUM_CHROME_HEADLESS_SHELL_PROVENANCE, product
    chrome-headless-shell); a discovered shell without one leaves `chrome`.
    """
    env = os.environ if environ is None else environ
    if browser_kind(chrome) == "headless-shell":
        return chrome
    named = env.get(SHELL_PATH_ENV) or None
    shell = Path(named) if named else sibling_headless_shell(chrome)
    if shell is None:
        return chrome
    if not shell.is_file() or not os.access(shell, os.X_OK) or browser_kind(shell) != "headless-shell":
        if named:
            raise BrowserProvenanceError(
                f"{SHELL_PATH_ENV} does not name an executable chrome-headless-shell: {named}")
        return chrome
    try:
        same_release = browser_version(shell) == browser_version(chrome)
    except BrowserProvenanceError:
        same_release = False
    if not same_release:
        if named:
            raise BrowserProvenanceError(
                f"{SHELL_PATH_ENV} is not the release of the pinned Chrome: {named}")
        return chrome
    if strict:
        receipt = env.get(SHELL_PROVENANCE_ENV)
        if not receipt:
            if named:
                raise BrowserProvenanceError(
                    f"{SHELL_PATH_ENV} needs a {SHELL_PROVENANCE_ENV} receipt under strict provenance")
            return chrome
        validate_record(Path(receipt).expanduser().resolve(), shell,
                        product=HEADLESS_SHELL_PRODUCT)
    return shell
