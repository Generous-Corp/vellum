#!/usr/bin/env python3
"""Convert PDF-compatible Illustrator artwork to SVG with a hash receipt.

Adobe Illustrator files saved with PDF compatibility are PDF documents. This
adapter does not parse them itself: Poppler's ``pdftocairo`` converts one page
(artboard) to SVG, and the adapter records the exact source and output hashes
and the converter version in a JSON receipt written beside the SVG. The SVG
then enters the ordinary SVG import path. The receipt makes no claim about the
rights to the artwork.

The boundary fails closed:
- the converter runs from an argv list, never a shell;
- only ``.ai`` and ``.pdf`` sources are accepted;
- the source, the output and the receipt must not be symlinks, and the output
  must not be the source;
- the converter writes into a fresh 0700 directory and the receipt is written
  through its mkstemp descriptor; both are moved into place only when complete;
- the output and the receipt may not name the source or each other, compared
  by file identity and case-insensitively, since a case-insensitive volume maps
  ART.PDF and art.pdf to one file;
- a missing converter, a non-zero exit, a timeout or an output that does not
  look like SVG is an error.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any

SCHEMA = "vellum.illustrator-svg-provenance.v1"
SUPPORTED_SUFFIXES = frozenset({".ai", ".pdf"})
DEFAULT_TIMEOUT_SECONDS = 60
CONVERTER_ENV = "VELLUM_PDF_TO_SVG"
SVG_PROBE_BYTES = 8192
MAX_VERSION_CHARS = 256
MAX_DETAIL_CHARS = 300


class ConversionError(RuntimeError):
    """A conversion failure that is safe to show to the user."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_symlink(path: Path, label: str) -> None:
    # Checked on the path as given, before any resolve(): a resolved path is
    # never a symlink, so checking afterwards would accept one silently.
    if path.is_symlink():
        raise ConversionError(f"{label} must not be a symlink: {path}")


def _regular_source(path: Path) -> Path:
    _reject_symlink(path, "source")
    try:
        info = path.stat()
    except FileNotFoundError as exc:
        raise ConversionError(f"source does not exist: {path}") from exc
    except OSError as exc:
        raise ConversionError(f"cannot inspect source {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConversionError(f"source is not a regular file: {path}")
    return path.resolve()


def _writable_destination(path: Path, label: str) -> Path:
    _reject_symlink(path, label)
    if path.exists() and not path.is_file():
        raise ConversionError(f"{label} is not a regular file: {path}")
    return path.resolve()


def _folded_name(path: Path) -> str:
    """A file name as a case- and normalization-insensitive volume compares it.

    APFS and HFS+ treat NFC and NFD spellings of a name as one file, and fold
    case, so the name is decomposed, case-folded, and decomposed again (folding
    can produce characters that decompose further).
    """
    decomposed = unicodedata.normalize("NFD", path.name)
    return unicodedata.normalize("NFD", decomposed.casefold())


def _move_into_place(temporary: Path, destination: Path, label: str) -> None:
    """Replace ``destination`` with ``temporary``, as a user-facing error on failure.

    The destination was checked earlier, but it can change before the move (for
    example, become a directory); that is reported, not raised as a traceback.
    """
    try:
        os.replace(temporary, destination)
    except OSError as exc:
        raise ConversionError(f"cannot move the {label} into place at {destination}: {exc}") from exc


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths name one file, including on a case-insensitive volume.

    Resolved strings are not enough: macOS ``resolve()`` keeps the caller's
    case, so ``ART.PDF`` and ``art.pdf`` differ as strings while naming one
    file. Existing files are compared by identity. A destination that does not
    exist yet is treated as the same file when its name matches, ignoring case
    and Unicode normalization, in the same directory, which is conservative on a
    case-sensitive volume.
    """
    if a == b:
        return True
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    if _folded_name(a) != _folded_name(b):
        return False
    if a.parent == b.parent:
        return True
    try:
        return os.path.samefile(a.parent, b.parent)
    except OSError:
        return False


def find_converter() -> Path:
    """Return the converter executable, honouring ``VELLUM_PDF_TO_SVG``."""
    override = os.environ.get(CONVERTER_ENV)
    if override:
        candidate = Path(override).expanduser()
    else:
        found = shutil.which("pdftocairo")
        if not found:
            raise ConversionError(
                "pdftocairo was not found on PATH; install Poppler or set "
                f"{CONVERTER_ENV} to an executable converter"
            )
        candidate = Path(found)
    # A package manager commonly installs the converter as a symlink. Resolving
    # it is safe: the invocation is an argv list and the target is checked below.
    candidate = candidate.resolve()
    try:
        mode = candidate.stat().st_mode
    except OSError as exc:
        raise ConversionError(f"pdftocairo is not an executable file: {candidate}") from exc
    if not stat.S_ISREG(mode) or not os.access(candidate, os.X_OK):
        raise ConversionError(f"pdftocairo is not an executable file: {candidate}")
    return candidate


def converter_version(tool: Path) -> str:
    try:
        result = subprocess.run(
            [str(tool), "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConversionError(f"could not query the pdftocairo version: {exc}") from exc
    text = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0 or not text:
        raise ConversionError(f"pdftocairo version query failed (exit {result.returncode})")
    # Only the first line is kept, bounded, so a receipt never embeds arbitrary
    # converter output.
    return text.splitlines()[0].strip()[:MAX_VERSION_CHARS]


def looks_like_svg(path: Path) -> bool:
    """A light boundary check: an ``<svg`` opening near the start and a closing tag.

    Full parsing belongs to the SVG importer that consumes the file.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return False
    head = data[:SVG_PROBE_BYTES].lower()
    tail = data[-SVG_PROBE_BYTES:].lower()
    return b"<svg" in head and b"</svg" in tail


def _remove_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def convert(
    source: Path,
    output: Path | None = None,
    receipt_path: Path | None = None,
    *,
    page: int = 1,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Convert one page of a PDF-compatible ``.ai``/``.pdf`` file; return the receipt.

    ``output`` defaults to the source path with an ``.svg`` suffix, and
    ``receipt_path`` to ``<output>.provenance.json``.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ConversionError("page must be a positive integer")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ConversionError("timeout must be a positive integer")

    source = _regular_source(Path(source).expanduser())
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ConversionError(
            f"unsupported source extension {source.suffix!r}; expected {allowed} "
            "(an SVG is already converted and is imported directly)"
        )

    output = _writable_destination(
        source.with_suffix(".svg") if output is None else Path(output).expanduser(),
        "output",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(output, source):
        raise ConversionError(f"output must not overwrite the source: {output}")
    receipt_path = _writable_destination(
        Path(str(output) + ".provenance.json")
        if receipt_path is None
        else Path(receipt_path).expanduser(),
        "receipt",
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(receipt_path, source) or _same_file(receipt_path, output):
        raise ConversionError(f"receipt must not overwrite the source or output: {receipt_path}")

    tool = find_converter()
    version = converter_version(tool)
    source_hash = sha256_file(source)
    source_bytes = source.stat().st_size

    # The converter writes into a fresh 0700 directory, so no other user can
    # plant or swap the file it writes before it is moved into place.
    workdir = Path(tempfile.mkdtemp(prefix=f".{output.stem}.", dir=str(output.parent)))
    temporary = workdir / "converted.svg"
    try:
        # One page only. For SVG, pdftocairo writes exactly the named output
        # when the page range is a single page; -singlefile applies only to
        # raster formats, so it is not passed.
        command = [
            str(tool), "-svg", "-f", str(page), "-l", str(page),
            str(source), str(temporary),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"pdftocairo timed out after {timeout}s") from exc
        except OSError as exc:
            raise ConversionError(f"could not run pdftocairo: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = f": {detail[-1][:MAX_DETAIL_CHARS]}" if detail else ""
            raise ConversionError(f"pdftocairo failed with exit {result.returncode}{tail}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ConversionError("pdftocairo produced no SVG output")
        if not looks_like_svg(temporary):
            raise ConversionError("pdftocairo output does not look like an SVG")
        _move_into_place(temporary, output, "SVG")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "source": {
            "path": str(source),
            "format": suffix[1:],
            "sha256": source_hash,
            "bytes": source_bytes,
        },
        "output": {
            "path": str(output),
            "format": "svg",
            "sha256": sha256_file(output),
            "bytes": output.stat().st_size,
        },
        "converter": {
            "name": "pdftocairo",
            "path": str(tool),
            "version": version,
            "page": page,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    # Write through the descriptor mkstemp returns, so the bytes land in the file it
    # created and not in whatever a path lookup finds later.
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{receipt_path.name}.", suffix=".tmp", dir=str(receipt_path.parent))
    temporary_receipt = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        _move_into_place(temporary_receipt, receipt_path, "receipt")
    finally:
        _remove_if_present(temporary_receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert PDF-compatible Illustrator artwork to SVG with a hash receipt"
    )
    parser.add_argument("source", type=Path, help="PDF-compatible .ai or .pdf input")
    parser.add_argument("-o", "--output", type=Path, help="SVG output path (default: beside the input)")
    parser.add_argument(
        "--provenance", type=Path,
        help="JSON receipt path (default: <output>.provenance.json)",
    )
    parser.add_argument("--page", type=int, default=1, help="1-based page/artboard (default: 1)")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help="converter timeout in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = convert(
            args.source, args.output, args.provenance, page=args.page, timeout=args.timeout
        )
    except ConversionError as exc:
        print(f"illustrator-to-svg: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
