"""Which files the contamination scans read as text, and what native text refuses.

Two scans enforce the same boundary on different inputs: verify_extraction.py
reads the native source roots of a checkout, and scripts/install_core.py reads
the payload of an SDK archive. Both decide "is this file text I must scan?" and
both refuse the same Pulp names, so both take that rule from here.

The installer ships as one checksummed file that cannot import this module, so
it carries these values as literals; cli/tests/test_sdk_mirror_scope.py holds
them equal to this module.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re


# Files whose suffix marks them as text. Build inputs are deliberately here:
# CMakeLists.txt (.txt), configure templates (.in) and file templates
# (.template) can link a Pulp target or include a Pulp header as easily as a
# .cmake or .hpp file can.
CONTENT_SCAN_SUFFIXES = frozenset({
    ".c",
    ".cc",
    ".cmake",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".entitlements",
    ".frag",
    ".glsl",
    ".go",
    ".gradle",
    ".h",
    ".hh",
    ".hpp",
    ".html",
    ".hxx",
    ".in",
    ".inc",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lock",
    ".m",
    ".manifest",
    ".map",
    ".md",
    ".metal",
    ".mjs",
    ".mm",
    ".pc",
    ".plist",
    ".properties",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".sksl",
    ".svg",
    ".swift",
    ".template",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vert",
    ".wgsl",
    ".xml",
    ".yaml",
    ".yml",
})
# Text files whose name has no telling suffix.
CONTENT_SCAN_BASENAMES = frozenset({
    "LICENSE",
    "Makefile",
    "NOTICE",
    "vellum",
    "vellum-backend",
    "vellum-import-backend",
    "vellum-native-backend",
    "vellum-web-backend",
})
# The native source scan does not read Python. The Python under the native
# roots is the contamination tooling itself (the installer, the README and
# installed-SDK checks, their tests), which has to spell every forbidden name in
# order to refuse it; reading it would report the policy as its own violation.
# Python is not a build or authoring input of the native SDK, and the shipped
# Python launchers are still scanned where they ship, by the installer.
NATIVE_SOURCE_EXCLUDED_SUFFIXES = frozenset({".py"})

# Pulp names that native Vellum source and non-mirror SDK payload refuse, as
# (pattern, flags) so a str scan and a bytes scan compile the same rule.
FORBIDDEN_NATIVE_CONTENT_PATTERNS: dict[str, tuple[str, int]] = {
    "pulp-public-namespace": (
        r"(?:\bnamespace\s+pulp\b|\bpulp::|#\s*include\s*[<\"]pulp/)",
        re.IGNORECASE,
    ),
    "pulp-package-or-target": (
        r"(?:@pulp/|\bPULP_[A-Z0-9_]+|\bpulp[-_]"
        r"(?:audio|canvas|format|gpu|graph|host|midi|plugin|render|runtime|signal|view)\b)",
        re.IGNORECASE,
    ),
    "audio-plugin-sdk": (
        r"\b(?:AudioUnit|VST3|CLAP|LV2|Oboe)\b",
        re.IGNORECASE,
    ),
}


def should_scan_content(name: str, *, extensionless: bool = False) -> bool:
    """Whether the file at `name` (a POSIX path) is text the scans must read.

    `extensionless` also admits a file with no suffix. A source checkout holds
    no compiled binaries, so its suffixless files are text (and a file that is
    not UTF-8 is skipped by the reader); an SDK payload ships native
    executables without suffixes, so the installer leaves it off.
    """
    path = PurePosixPath(name)
    suffix = path.suffix.lower()
    return (
        path.name in CONTENT_SCAN_BASENAMES
        or suffix in CONTENT_SCAN_SUFFIXES
        or (extensionless and suffix == "")
    )


def compiled_text_patterns() -> dict[str, re.Pattern[str]]:
    return {name: re.compile(pattern, flags) for name, (pattern, flags) in FORBIDDEN_NATIVE_CONTENT_PATTERNS.items()}


def compiled_bytes_patterns() -> dict[str, re.Pattern[bytes]]:
    return {
        name: re.compile(pattern.encode("utf-8"), flags)
        for name, (pattern, flags) in FORBIDDEN_NATIVE_CONTENT_PATTERNS.items()
    }
