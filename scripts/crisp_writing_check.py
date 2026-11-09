#!/usr/bin/env python3
"""Flag em dashes, workflow breadcrumbs and padding phrases in prose and comments.

Usage: check.py [paths...]          scan files (default: tracked files in cwd)
       check.py --diff <base>       scan only lines added since <base>
       --comments-only               in code files, check only comment text
Exit 1 when anything is found. Add "crisp-writing: ok" on a line to skip it.
"""
import re
import subprocess
import sys

TEXT_EXT = (".md", ".txt", ".rst", ".py", ".sh", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm",
            ".swift", ".rs", ".js", ".ts", ".tsx", ".yml", ".yaml", ".toml", ".cmake", ".json")
CHECKS = [
    ("em dash", re.compile("—")),
    ("breadcrumb", re.compile(r"\b(?:phase|wave|milestone|slice|stage|tranche)[\s-]*\d+[a-z]?\b", re.I)),
    ("breadcrumb", re.compile(r"\b[PDM]\d+ (?:work|units?|items?|follow-?up)\b")),
    ("breadcrumb", re.compile(r"\b(?:this|the previous|next) session\b|\bper the reviewer\b|\bcodex (?:p\d|review)\b", re.I)),
    ("breadcrumb", re.compile(r"\b(?:added|introduced|fixed|follow-?up)\b[^.\n]{0,20}#\d{2,}|\b[\w.-]+#\d{2,}\b", re.I)),
    ("breadcrumb", re.compile(r"\b(?:owner|integrator) (?:decision|ruling)\b[^.\n]{0,10}\b20\d\d-\d\d-\d\d", re.I)),
    ("padding", re.compile(r"\b(?:it'?s worth noting|importantly,|in order to|this ensures that|seamless(?:ly)?|leverag(?:e|es|ing)|robust|comprehensive|cutting-edge|delve|game-changer|utiliz(?:e|es|ing))\b", re.I)),
]
SKIP = "crisp-writing: ok"
PROSE_EXT = (".md", ".txt", ".rst")
COMMENT_RX = re.compile(r"(?://|#|/\*|^\s*\*)(.*)$")
COMMENTS_ONLY = False


def prose_of(path, text):
    """Return the part of a line that is prose: whole line for docs, comment text for code."""
    if not COMMENTS_ONLY or path.endswith(PROSE_EXT):
        return text
    m = COMMENT_RX.search(text)
    return m.group(1) if m else ""


def scan_line(path, lineno, text, out):
    if SKIP in text:
        return
    text = prose_of(path, text)
    for label, rx in CHECKS:
        m = rx.search(text)
        if m:
            out.append(f"{path}:{lineno}: {label}: {m.group(0)!r}: {text.strip()[:120]}")


def tracked_files():
    res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [p for p in res.stdout.splitlines() if p.endswith(TEXT_EXT)]


def scan_files(paths):
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    scan_line(path, i, line, out)
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
    return out


def scan_diff(base):
    res = subprocess.run(["git", "diff", "-U0", f"{base}...HEAD"], capture_output=True, text=True, check=True)
    out, path, lineno = [], None, 0
    for line in res.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            lineno = int(re.search(r"\+(\d+)", line).group(1)) - 1
        elif line.startswith("+") and path and path.endswith(TEXT_EXT):
            lineno += 1
            scan_line(path, lineno, line[1:], out)
    return out


def main(argv):
    global COMMENTS_ONLY
    if "--comments-only" in argv:
        COMMENTS_ONLY = True
        argv = [a for a in argv if a != "--comments-only"]
    if argv[:1] == ["--diff"]:
        if len(argv) < 2:
            print("usage: check.py --diff <base>", file=sys.stderr)
            return 2
        findings = scan_diff(argv[1])
    else:
        findings = scan_files(argv or tracked_files())
    print("\n".join(findings))
    print(f"{len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
