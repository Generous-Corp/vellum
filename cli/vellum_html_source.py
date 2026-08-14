"""Fail-closed preflight and contained staging for HTML design sources.

This module does not execute JavaScript or fetch the network. It is the
admission boundary before an HTML/Claude source enters isolated Chromium.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
from urllib.parse import urlsplit

SCHEMA = "vellum.html-source-preflight.v1"
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_DEPENDENCIES = 2048
MAX_DEPENDENCY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ATTRIBUTE_BYTES = 64 * 1024
CLAUDE_MARKER_RE = re.compile(r"(?:^|[-_:])claude(?:$|[-_:])", re.IGNORECASE)
CLAUDE_GENERATOR_RE = re.compile(r"\bclaude\b|anthropic", re.IGNORECASE)
LOCAL_ATTRIBUTES = {"audio", "cite", "data", "href", "poster", "src", "srcset"}
HTML_EXTENSIONS = {".html", ".htm"}
CSS_URL_RE = re.compile(r"url\(\s*[\"']?([^\)\"'\s]+)", re.IGNORECASE)


class HTMLSourceError(ValueError):
    """Raised when an HTML source cannot be admitted safely."""


@dataclass(frozen=True)
class Dependency:
    path: str
    kind: str
    bytes: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "kind": self.kind, "bytes": self.bytes,
                "sha256": f"sha256:{self.sha256}"}


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.references: list[tuple[str, str]] = []
        self.markers: set[str] = set()
        self.generators: list[str] = []
        self.inline_styles: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "style":
            self._in_style = True
        values = {key.casefold(): value for key, value in attrs if value is not None}
        for key, value in values.items():
            if len(value.encode("utf-8")) > MAX_ATTRIBUTE_BYTES:
                raise HTMLSourceError(f"HTML attribute '{key}' exceeds the bounded size")
            if key.startswith("data-claude") or CLAUDE_MARKER_RE.search(key):
                self.markers.add(f"attribute:{key}")
            if key in {"name", "content"} and CLAUDE_GENERATOR_RE.search(value):
                self.generators.append(value)
            if key == "style":
                self.inline_styles.append(value)
            if key in LOCAL_ATTRIBUTES:
                for reference in _split_references(value, key):
                    self.references.append((tag, reference))
        if tag == "meta" and values.get("name", "").casefold() == "generator":
            content = values.get("content", "")
            if content:
                self.generators.append(content)
        for value in values.values():
            if value and CLAUDE_GENERATOR_RE.search(value):
                self.markers.add("value:claude")

    def handle_comment(self, data: str) -> None:
        if CLAUDE_GENERATOR_RE.search(data):
            self.markers.add("comment:claude")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.inline_styles.append(data)


def _split_references(value: str, attribute: str) -> list[str]:
    if attribute == "srcset":
        # A data URL contains a comma of its own. Join its payload before
        # treating the next comma as a srcset candidate separator.
        candidates: list[str] = []
        current = ""
        for part in value.split(","):
            if current and current.lstrip().casefold().startswith("data:") and " " not in current:
                current += "," + part
                continue
            if current.strip():
                candidates.append(current.strip().split(None, 1)[0])
            current = part
        if current.strip():
            candidates.append(current.strip().split(None, 1)[0])
        return candidates
    return [value.strip()] if value.strip() else []


def _relative_reference(reference: str, base: str = "") -> str | None:
    """Return a canonical local path, or None for non-file references."""
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith(("/", "\\")):
        return None
    path = parsed.path
    if not path or path.startswith("#"):
        return None
    if "\\" in path:
        raise HTMLSourceError(f"HTML dependency uses a backslash: {reference!r}")
    normalized = posixpath.normpath(posixpath.join(base, path))
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise HTMLSourceError(f"HTML dependency escapes the source: {reference!r}")
    candidate = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise HTMLSourceError(f"HTML dependency is not canonical: {reference!r}")
    return candidate.as_posix()


def _dependency_kind(tag: str, path: str) -> str:
    suffix = Path(path).suffix.casefold()
    if tag == "script" or suffix in {".js", ".mjs", ".cjs"}:
        return "script"
    if tag == "link" or suffix == ".css":
        return "stylesheet"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}:
        return "image"
    if suffix in {".woff", ".woff2", ".ttf", ".otf"}:
        return "font"
    return "resource"


def _regular_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise HTMLSourceError(f"HTML dependency escapes the source: {relative!r}") from error
    current = root.resolve()
    for segment in PurePosixPath(relative).parts:
        current /= segment
        if current.is_symlink():
            raise HTMLSourceError(f"HTML dependency contains a symbolic link: {relative!r}")
    if not candidate.is_file() or candidate.is_symlink():
        raise HTMLSourceError(f"HTML dependency is missing or not a regular file: {relative!r}")
    return candidate


def fingerprint_html(source: Path) -> dict[str, object]:
    """Inspect a local HTML entrypoint without executing it."""
    source = Path(source).expanduser()
    if source.is_symlink():
        raise HTMLSourceError("HTML source must not be a symbolic link")
    source = source.resolve()
    if source.suffix.casefold() not in HTML_EXTENSIONS:
        raise HTMLSourceError("HTML source must end in .html or .htm")
    if not source.is_file() or source.is_symlink():
        raise HTMLSourceError("HTML source must be a regular file")
    if source.stat().st_size > MAX_HTML_BYTES:
        raise HTMLSourceError(f"HTML source exceeds {MAX_HTML_BYTES} bytes")
    try:
        parser = _ReferenceParser()
        parser.feed(source.read_bytes().decode("utf-8"))
        parser.close()
    except UnicodeDecodeError as error:
        raise HTMLSourceError("HTML source must be valid UTF-8") from error
    claude = bool(parser.markers or any(CLAUDE_GENERATOR_RE.search(value) for value in parser.generators))
    sidecars = {name for name in ("claude.json", "claude-design.json", "design.json")
                if (source.parent / name).is_file()}
    claude = claude or bool({"claude.json", "claude-design.json"} & sidecars) or "claude" in source.stem.casefold()
    return {
        "schema": SCHEMA,
        "source": source.name,
        "producer": "claude-design" if claude else "generic-html",
        "fingerprint": "claude-design-v1" if claude else "generic-html-v1",
        "confidence": "strong" if claude else "baseline",
        "markers": sorted(parser.markers),
        "generators": sorted(set(parser.generators)),
        "sidecars": sorted(sidecars),
        "inlineStyles": parser.inline_styles,
        "references": [{"tag": tag, "reference": reference}
                       for tag, reference in parser.references],
    }


def discover_dependencies(source: Path, fingerprint: dict[str, object]) -> list[Dependency]:
    root = source.resolve().parent
    references = fingerprint.get("references", [])
    if not isinstance(references, list):
        raise HTMLSourceError("HTML fingerprint references are malformed")
    dependencies: dict[str, Dependency] = {}
    total = source.stat().st_size

    def add_dependency(relative: str, kind: str) -> None:
        nonlocal total
        if relative in dependencies:
            return
        path = _regular_file(root, relative)
        size = path.stat().st_size
        if size > MAX_DEPENDENCY_BYTES:
            raise HTMLSourceError(f"HTML dependency exceeds {MAX_DEPENDENCY_BYTES} bytes: {relative!r}")
        data = path.read_bytes()
        if len(data) > MAX_DEPENDENCY_BYTES:
            raise HTMLSourceError(f"HTML dependency exceeds {MAX_DEPENDENCY_BYTES} bytes: {relative!r}")
        dependencies[relative] = Dependency(
            path=relative, kind=kind, bytes=len(data), sha256=sha256(data).hexdigest())
        total += len(data)
        if len(dependencies) > MAX_DEPENDENCIES:
            raise HTMLSourceError(f"HTML source exceeds {MAX_DEPENDENCIES} dependencies")
        if total > MAX_TOTAL_BYTES:
            raise HTMLSourceError(f"HTML source tree exceeds {MAX_TOTAL_BYTES} bytes")

    for item in references:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
            raise HTMLSourceError("HTML fingerprint contains a malformed reference")
        relative = _relative_reference(item["reference"])
        if relative is None:
            continue
        add_dependency(relative, _dependency_kind(str(item.get("tag", "")), relative))
    # CSS is part of the contained source graph. Discover local url() assets
    # without evaluating CSS or resolving remote URLs.
    for dependency in list(dependencies.values()):
        if dependency.kind != "stylesheet":
            continue
        css_path = root / dependency.path
        css = css_path.read_text(encoding="utf-8", errors="strict")
        css_base = PurePosixPath(dependency.path).parent.as_posix()
        if css_base == ".":
            css_base = ""
        for reference in CSS_URL_RE.findall(css):
            relative = _relative_reference(reference, css_base)
            if relative is None:
                continue
            add_dependency(relative, _dependency_kind("", relative))
    inline_styles = fingerprint.get("inlineStyles", [])
    if not isinstance(inline_styles, list) or any(not isinstance(style, str) for style in inline_styles):
        raise HTMLSourceError("HTML fingerprint inline styles are malformed")
    for style in inline_styles:
        for reference in CSS_URL_RE.findall(style):
            relative = _relative_reference(reference)
            if relative is not None:
                add_dependency(relative, _dependency_kind("", relative))
    return [dependencies[key] for key in sorted(dependencies)]


def stage_html_source(source: Path, destination: Path) -> dict[str, object]:
    """Copy an admitted HTML source and local dependencies to a fresh tree."""
    source = Path(source).expanduser()
    if source.is_symlink():
        raise HTMLSourceError("HTML source must not be a symbolic link")
    source = source.resolve()
    fingerprint = fingerprint_html(source)
    dependencies = discover_dependencies(source, fingerprint)
    destination = destination.resolve()
    if destination == source.parent or source.parent in destination.parents:
        raise HTMLSourceError("HTML staging destination cannot be inside the source tree")
    if destination.exists():
        raise HTMLSourceError(f"HTML staging destination already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        shutil.copy2(source, destination / source.name)
        for dependency in dependencies:
            target = destination / dependency.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.parent / dependency.path, target)
    except OSError as error:
        shutil.rmtree(destination, ignore_errors=True)
        raise HTMLSourceError(f"Could not stage HTML source: {error}") from error
    return {
        "schema": SCHEMA, "entry": source.name,
        "producer": fingerprint["producer"], "fingerprint": fingerprint["fingerprint"],
        "dependencies": [item.as_dict() for item in dependencies],
        "stagingRoot": str(destination),
    }
