#!/usr/bin/env python3
"""Respell Vellum's mirrored Pulp units under a new root (`pulp::` -> `vellum::`).

The spellings come from the mirror-edge table's `roots`
(`tools/provenance/mirror_edge_patterns.json`, vendored from Pulp's
`tools/scripts/vellum_mirror_edge_patterns.json`). The old spelling is the
first `namespace` / `include` root, and the new one is `--to`. So does the set
of file suffixes it reads and the binary suffixes it skips. Nothing here
hard-codes `pulp` or `vellum`.

Deterministic and idempotent. It is a pure function of the tree, the table and
`--to`: files are visited in sorted order, rules apply in a fixed order, and no
rule's output matches any rule, so a second run changes nothing.

What it renames, in each scanned text file under the root (third-party
`external/` excluded), with OLD and NEW the two namespace spellings:

  namespace     `namespace OLD` -> `namespace NEW` (and `namespace OLD::x`)
  qualified     `OLD::` -> `NEW::`, in code and in string literals
  include-path  `OLD/<unit>` -> `NEW/<unit>` (include roots) wherever it starts
                a path, for each unit with an `include/OLD/<unit>` directory
  include-dir   a bare `include/OLD`
  objc-class    each Objective-C class the tree declares as `<Old>X` ->
                `<New>MirrorX`, because Objective-C classes share one flat
                namespace per process and `<New>X` may already be native

and it moves every `include/OLD/` directory to `include/NEW/`.

What it keeps, and only counts: upper-case `OLD_*` macros, `OLD-*` / `OLD_*`
CMake target and function names (the `target` root), and `<Old>*`
identifiers that are not Objective-C classes. None is a linker-visible C++ or
Objective-C name.

The roots step: the edge table's checks find edges only under the spellings
its roots list. `--apply` therefore refuses unless the table's `namespace`,
`include` and `target` roots already list both spellings (`--allow-unlisted-roots`
overrides it for a scratch copy). The report's `roots_step` says what to set.

Modes:
  default / --dry-run  report what would change; touch nothing
  --apply              rewrite and move in place, then re-scan and verify

Exit codes: 0 success, 1 a post-condition failed or the control count was not
the expected one, 2 bad usage or table, 3 the instrument cannot see its target
(no `namespace OLD` and no `namespace NEW`, so a zero count proves nothing).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mirror_collision_census as collisions  # noqa: E402
import verify_mirror_rename_identity as identity  # noqa: E402

SCHEMA = "vellum.mirror-namespace-rename.v2"
EXCLUDED_TOP_DIRS = ("external",)
OBJC_SOURCE_SUFFIXES = frozenset({".h", ".hpp", ".m", ".mm"})
DEFAULT_TABLE = Path("tools/provenance/mirror_edge_patterns.json")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")
# Escape-aware word start: not after an identifier character, or right after a
# C escape inside a string literal that holds generated code
# (`"...\\nnamespace pulp::x {"`, `"\\tpulp::y"`).
BOUNDARY = r"(?:(?<![\w$])|(?<=\\[abfnrtv0]))"
NS_START = BOUNDARY
DEFAULT_EXCLUSIONS = Path(__file__).resolve().parent / "rename_literal_exclusions.json"
# A string literal in C, C++, Objective-C, CMake, Python or JavaScript source:
# raw C++ strings, then ordinary double-quoted ones (with an optional `@`).
LITERAL = re.compile(r'R"([^(\s\\]{0,16})\((?:.|\n)*?\)\1"|@?"(?:\\.|[^"\\\n])*"')


class UsageError(Exception):
    pass


@dataclass(frozen=True)
class Spelling:
    old: str
    new: str
    roots: dict
    suffixes: frozenset
    binary_suffixes: frozenset

    @property
    def old_class(self) -> str:
        return self.old[:1].upper() + self.old[1:]

    @property
    def new_class_prefix(self) -> str:
        return self.new[:1].upper() + self.new[1:] + "Mirror"


def load_spelling(table_path: Path, new: str) -> Spelling:
    try:
        table = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UsageError(f"cannot read edge table {table_path}: {error}") from error
    return spelling_from_table(table, table_path, new)


def spelling_from_table(table: dict, table_path: Path | str, new: str) -> Spelling:
    roots = table.get("roots")
    if not isinstance(roots, dict) or not all(
            isinstance(roots.get(k), list) and roots[k] for k in ("namespace", "include")):
        raise UsageError(f"{table_path}: roots.namespace and roots.include must be non-empty lists")
    old = roots["namespace"][0]
    if roots["include"][0] != old:
        raise UsageError(f"{table_path}: the first namespace and include roots differ "
                         f"({old!r} vs {roots['include'][0]!r})")
    for name in (old, new):
        if not IDENTIFIER.match(name):
            raise UsageError(f"{name!r} is not a lower-case identifier")
    if new == old:
        raise UsageError(f"--to {new!r} is already the first root")
    suffixes = table.get("suffixes")
    binary = table.get("binary_suffixes", [])
    if not isinstance(suffixes, list) or not isinstance(binary, list):
        raise UsageError(f"{table_path}: suffixes and binary_suffixes must be lists")
    return Spelling(old, new, {k: list(v) for k, v in roots.items()},
                    frozenset(s.lower() for s in suffixes), frozenset(s.lower() for s in binary))


def probes(sp: Spelling) -> dict[str, re.Pattern[str]]:
    o, n = re.escape(sp.old), re.escape(sp.new)
    header_path = r"(?=[a-z_]+/[\w./-]*\.(?:h|hpp|hh|inl|in)\b)"
    return {
        f"namespace {sp.old}": re.compile(NS_START + rf"namespace\s+{o}\b"),
        f"namespace {sp.new}": re.compile(NS_START + rf"namespace\s+{n}\b"),
        f"{sp.old}::": re.compile(BOUNDARY + rf"{o}::"),
        f"{sp.new}::": re.compile(BOUNDARY + rf"{n}::"),
        f"include <{sp.old}/": re.compile(rf"#\s*(?:include|import)\s*[<\"]{o}/"),
        f"include <{sp.new}/": re.compile(rf"#\s*(?:include|import)\s*[<\"]{n}/"),
        f"path segment {sp.old}/<unit>": re.compile(rf"(?:(?<=[<\"'/(\s])|^){o}/{header_path}", re.M),
        f"dir include/{sp.old}": re.compile(rf"(?<=include/){o}(?![\w.-])"),
    }


def retained(sp: Spelling) -> dict[str, re.Pattern[str]]:
    return {
        f"macro {sp.old.upper()}_*": re.compile(rf"\b{re.escape(sp.old.upper())}_[A-Z0-9_]+"),
        f"cmake {sp.old}-* / {sp.old}_*": re.compile(rf"\b{re.escape(sp.old)}[-_][a-z0-9][a-z0-9_-]*"),
    }


def is_excluded(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0] in EXCLUDED_TOP_DIRS


def walk(root: Path) -> list[Path]:
    """Every regular file under root, sorted, symlinks not followed."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def classify(sp: Spelling, path: Path) -> str:
    """'text', 'binary' or 'unclassified', by the table's suffix lists."""
    suffix = path.suffix.lower()
    if suffix in sp.binary_suffixes:
        return "binary"
    if suffix in sp.suffixes:
        return "text"
    return "unclassified"


def read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def discover(sp: Spelling, root: Path, files: list[Path]) -> tuple[list[str], list[str]]:
    """The include units (`include/OLD/<unit>`) and Objective-C classes."""
    units: set[str] = set()
    for path in files:
        parts = path.relative_to(root).parts
        for i in range(len(parts) - 3):
            if parts[i] == "include" and parts[i + 1] == sp.old:
                units.add(parts[i + 2])
    declaration = re.compile(rf"@(?:interface|implementation)\s+({re.escape(sp.old_class)}[A-Z]\w*)")
    classes: set[str] = set()
    for path in files:
        if is_excluded(path.relative_to(root)) or path.suffix.lower() not in OBJC_SOURCE_SUFFIXES:
            continue
        text = read_text(path)
        if text is not None:
            classes.update(declaration.findall(text))
    return sorted(units), sorted(classes)


def build_rules(sp: Spelling, units: list[str], classes: list[str]) -> list[tuple[str, re.Pattern[str], str]]:
    o = re.escape(sp.old)
    rules: list[tuple[str, re.Pattern[str], str]] = [
        ("namespace", re.compile(NS_START + rf"namespace(\s+){o}\b"), rf"namespace\1{sp.new}"),
        ("qualified", re.compile(BOUNDARY + rf"{o}::"), f"{sp.new}::"),
    ]
    if units:
        alternation = "|".join(re.escape(u) for u in units)
        # An `OLD/<unit>` segment wherever it starts a path or follows a
        # separator: `<OLD/x/..>`, `"OLD/x/.."`, `include/OLD/x/..`,
        # `generated/OLD/runtime/..`, `DESTINATION include/OLD/runtime)`.
        rules.append((
            "include-path",
            re.compile(rf"(?:(?<=[<\"'/(\s])|(?<=\\[nt])|^){o}/(?=(?:{alternation})(?:/|\.h|(?![\w.-])))", re.M),
            f"{sp.new}/",
        ))
    rules.append(("include-dir", re.compile(rf"(?<=include/){o}(?![\w.-])(?!/)"), sp.new))
    if classes:
        alternation = "|".join(re.escape(c[len(sp.old_class):]) for c in classes)
        rules.append((
            "objc-class",
            re.compile(BOUNDARY + rf"{re.escape(sp.old_class)}({alternation})\b"),
            rf"{sp.new_class_prefix}\1",
        ))
    # ASCII classes, so `\w`, `\s` and `\b` mean exactly what the tool-free
    # verifier's byte patterns mean.
    return [(name, re.compile(pattern.pattern, (pattern.flags & ~re.UNICODE) | re.ASCII), replacement)
            for name, pattern, replacement in rules]


def rule_digest(sp: Spelling, rules, units: list[str], classes: list[str]) -> str:
    table = {
        "schema": SCHEMA,
        "from": sp.old,
        "to": sp.new,
        "rules": [[name, pattern.pattern, pattern.flags, replacement] for name, pattern, replacement in rules],
        "units": units,
        "objc_classes": classes,
        "excluded": list(EXCLUDED_TOP_DIRS),
        "suffixes": sorted(sp.suffixes),
        "binary_suffixes": sorted(sp.binary_suffixes),
    }
    return hashlib.sha256(json.dumps(table, sort_keys=True).encode()).hexdigest()


def renamed_path(sp: Spelling, relative: Path) -> Path:
    parts = list(relative.parts)
    for i in range(len(parts) - 1):
        if parts[i] == "include" and parts[i + 1] == sp.old:
            parts[i + 1] = sp.new
    return Path(*parts)


def count(sp: Spelling, root: Path, files: list[Path], writes: dict[Path, str] | None = None) -> dict[str, int]:
    patterns = {**probes(sp), **retained(sp)}
    counts = {name: 0 for name in patterns}
    for path in files:
        if is_excluded(path.relative_to(root)) or classify(sp, path) != "text":
            continue
        text = (writes or {}).get(path)
        if text is None:
            text = read_text(path)
        if text is None:
            continue
        for name, pattern in patterns.items():
            counts[name] += len(pattern.findall(text))
    return counts


def roots_step(sp: Spelling) -> dict:
    """The edge-table roots both spellings need, and whether they are set."""
    wanted = {kind: list(sp.roots.get(kind, [])) for kind in ("namespace", "include", "target")}
    # All three move together: the table's controls render `@namespace@`,
    # `@include@` and `@target@` with one spelling at a time.
    for kind in ("namespace", "include", "target"):
        if sp.new not in wanted[kind]:
            wanted[kind].append(sp.new)
    ready = all(sp.new in sp.roots.get(kind, []) for kind in ("namespace", "include", "target"))
    return {
        "ready": ready,
        "current": {kind: list(sp.roots.get(kind, [])) for kind in ("namespace", "include", "target")},
        "required": wanted,
        "note": "target gains the new spelling too, although mirrored CMake target names are kept",
    }


def load_exclusions(path: Path | None) -> list[dict]:
    """Literals the rename must leave as they are, each with its reason.

    Each row is {"path": raw mirror path, "literal": exact source text of the
    literal including its quotes, "reason": why}. A row that matches nothing
    is an error, so the list cannot go stale silently.
    """
    if path is None or not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if not all(isinstance(row.get(k), str) and row[k] for k in ("path", "literal", "reason")):
            raise UsageError(f"{path}: every exclusion needs path, literal and reason")
        if not identity.QUOTED_LITERAL.match(row["literal"]):
            raise UsageError(f"{path}: exclusion {row['literal']!r} is not a quoted string literal")
    return sorted(rows, key=lambda r: (r["path"], r["literal"]))


def byte_patterns(sp: Spelling, classes: list[str]) -> dict[str, re.Pattern[bytes]]:
    """Boundary-free counts over every byte of every file, binary and excluded ones included."""
    o, n = re.escape(sp.old.encode()), re.escape(sp.new.encode())
    patterns = {
        f"substring {sp.old}::": re.compile(o + rb"::"),
        f"substring namespace {sp.old}": re.compile(rb"namespace\s+" + o + rb"(?![A-Za-z0-9_])"),
        f"substring <{sp.old}/ or \"{sp.old}/": re.compile(rb"[<\"]" + o + rb"/"),
        f"substring include/{sp.old}": re.compile(rb"include/" + o + rb"(?![A-Za-z0-9_.-])"),
    }
    for cls in classes:
        patterns[f"word {cls}"] = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(cls.encode()) + rb"(?![A-Za-z0-9_])")
    for name, pattern in identity.target_spellings(sp.new, sp.new_class_prefix).items():
        patterns[f"target {name}"] = pattern
    return patterns


def byte_counts(patterns: dict[str, re.Pattern[bytes]], files: list[Path],
                writes: dict[Path, str] | None = None) -> dict[str, int]:
    counts = {name: 0 for name in patterns}
    for path in files:
        text = (writes or {}).get(path)
        data = text.encode("utf-8") if text is not None else path.read_bytes()
        for name, pattern in patterns.items():
            counts[name] += len(pattern.findall(data))
    return counts


def literal_changes(text: str, relative: str, rules, protected: set[str]) -> list[dict]:
    """Every string literal the rules change, with its line and length change."""
    changes = []
    for match in LITERAL.finditer(text):
        literal = match.group(0)
        line_start = text.rfind("\n", 0, match.start()) + 1
        if re.match(r"\s*#\s*(?:include|import)\b", text[line_start:match.start()]):
            continue  # an include directive's path, not a string
        if literal in protected:
            continue
        new = literal
        for _, pattern, replacement in rules:
            new = pattern.sub(replacement, new)
        if new != literal:
            # Length-coupled: an explicit byte count after the literal that equals
            # its length, as in `seed(..., "pulp::license::v2", 17)`.
            after = re.match(r"[)\s]*,\s*(\d+)\b", text[match.end():match.end() + 80])
            body = literal[1:-1] if literal.startswith('"') else literal
            coupled = bool(after and int(after.group(1)) == len(body.encode().decode("unicode_escape")))
            changes.append({"path": relative, "line": text.count("\n", 0, match.start()) + 1,
                            "before": literal, "after": new, "length_coupled": coupled})
    return changes


def plan(sp: Spelling, root: Path, exclusions: list[dict] | None = None) -> dict:
    files = walk(root)
    units, classes = discover(sp, root, files)
    rules = build_rules(sp, units, classes)
    per_rule = {name: 0 for name, _, _ in rules}
    writes: dict[Path, str] = {}
    changed: list[str] = []
    unclassified: list[str] = []
    excluded_hits = 0
    excluded_probes = (probes(sp)[f"namespace {sp.old}"], probes(sp)[f"{sp.old}::"])
    exclusions = exclusions or []
    exclusion_hits = {(row["path"], row["literal"]): 0 for row in exclusions}
    literals: list[dict] = []
    for path in files:
        relative = path.relative_to(root)
        kind = classify(sp, path)
        if kind == "unclassified":
            unclassified.append(relative.as_posix())
            continue
        if kind != "text":
            continue
        text = read_text(path)
        if text is None:
            continue
        if is_excluded(relative):
            excluded_hits += sum(len(p.findall(text)) for p in excluded_probes)
            continue
        # Excluded literals are swapped for NUL-delimited placeholders, which no
        # rule can match (a text file has no NUL), and restored afterwards.
        protected = {row["literal"]: f"\0{i}\0" for i, row in enumerate(exclusions)
                     if row["path"] == relative.as_posix()}
        new = text
        for literal, placeholder in protected.items():
            exclusion_hits[(relative.as_posix(), literal)] += new.count(literal)
            new = new.replace(literal, placeholder)
        literals.extend(literal_changes(text, relative.as_posix(), rules, set(protected)))
        for name, pattern, replacement in rules:
            new, n = pattern.subn(replacement, new)
            per_rule[name] += n
        for literal, placeholder in protected.items():
            new = new.replace(placeholder, literal)
        if new != text:
            changed.append(relative.as_posix())
            writes[path] = new
    moves = []
    for path in files:
        relative = path.relative_to(root)
        target = renamed_path(sp, relative)
        if target != relative:
            moves.append((relative.as_posix(), target.as_posix()))
    collisions = sorted(t for _, t in moves if (root / t).exists())
    patterns = byte_patterns(sp, classes)
    excluded_old = sum(hits * row["literal"].count(f"{sp.old}::") for row in exclusions
                       for key, hits in exclusion_hits.items() if key == (row["path"], row["literal"]))
    receipt = {
        "from": sp.old,
        "to": sp.new,
        "include_units": units,
        "objc_classes": classes,
        "objc_class_prefix": sp.new_class_prefix,
        "literal_exclusions": [{"path": r["path"], "literal": r["literal"]} for r in exclusions],
    }
    receipt["edge_table"] = {"roots": sp.roots, "suffixes": sorted(sp.suffixes),
                             "binary_suffixes": sorted(sp.binary_suffixes)}
    receipt["table_sha256"] = identity.table_sha256(receipt)
    receipt["tool_rule_digest"] = rule_digest(sp, rules, units, classes)
    receipt["python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    return {
        "files": files,
        "writes": writes,
        "moves": moves,
        "report": {
            "schema": SCHEMA,
            "from": sp.old,
            "to": sp.new,
            "rule_digest": rule_digest(sp, rules, units, classes),
            "include_units": units,
            "objc_classes": classes,
            "objc_class_prefix": f"{sp.old_class} -> {sp.new_class_prefix}",
            "replacements": per_rule,
            "files_scanned": len(files),
            "files_changed": len(changed),
            "paths_moved": len(moves),
            "move_collisions": collisions,
            "unclassified_suffix_files": unclassified,
            "excluded_dirs": list(EXCLUDED_TOP_DIRS),
            "excluded_references": excluded_hits,
            "roots_step": roots_step(sp),
            "literal_changes": literals,
            "literal_changes_count": len(literals),
            "length_coupled_literals": [c for c in literals if c["length_coupled"]],
            "literal_exclusions": [{**row, "occurrences": exclusion_hits[(row["path"], row["literal"])]}
                                   for row in exclusions],
            "excluded_literal_old_references": excluded_old,
            "receipt": receipt,
            "before": count(sp, root, files),
            "bytes_before": byte_counts(patterns, files),
        },
        "byte_patterns": patterns,
    }


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


EMPTY_BLOB = git_blob(b"")


def vellum_exemptions(native_root: Path) -> tuple[set[str], tuple[str, ...]]:
    """The renamed-mirror rule's exemptions from Vellum's own verifier, when it has them (vellum#204).

    One list, owned by Vellum: the policy implementations that must spell the
    names they refuse. Before #204 there are none.
    """
    import importlib.util
    path = native_root / "tools/provenance/verify_extraction.py"
    if not path.is_file():
        return set(), ()
    spec = importlib.util.spec_from_file_location("vellum_verify_extraction_for_rename", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # an unloadable verifier exempts nothing
        return set(), ()
    return (set(getattr(module, "RENAMED_MIRROR_EXEMPT_PATHS", set())),
            tuple(getattr(module, "RENAMED_MIRROR_EXEMPT_PREFIXES", ())))


def native_refusals(sp: Spelling, root: Path, native_root: Path, native, writes: dict[Path, str]) -> dict:
    """Native files may not use the renamed mirror's own namespaces or headers, or copy its blobs.

    The refused units are the mirrored include units native Vellum does not
    also own (it owns `runtime`, so `vellum::runtime` stays legal natively).
    """
    mirror_units = sorted({p.relative_to(root).parts[i + 2]
                           for p in walk(root) for i in range(len(p.relative_to(root).parts) - 3)
                           if p.relative_to(root).parts[i:i + 2] == ("include", sp.old)})
    # Also the mirror's top-level `<old>::<unit>` namespaces (pulp::android has
    # no include unit), matching Vellum's renamed-mirror-namespace census.
    census = re.compile(rf"namespace\s+{re.escape(sp.old)}\s*(?:::\s*|\{{\s*namespace\s+)(\w+)")
    for p in walk(root):
        if not is_excluded(p.relative_to(root)) and p.suffix.lower() in collisions.SOURCE_SUFFIXES | {".py", ".cmake"}:
            mirror_units.extend(census.findall(p.read_text("utf-8", errors="replace")))
    mirror_units = sorted(set(mirror_units))
    native_owned = {ns.split("::")[1] for ns in native.namespaces if ns.startswith(sp.new + "::")}
    native_owned |= {h.split("/")[1] for h in native.headers if h.startswith(sp.new + "/") and h.count("/") >= 2}
    refused = [u for u in mirror_units if u not in native_owned]
    findings = []
    native_blobs: dict[str, str] = {}
    native_classes: dict[str, str] = {}
    declaration = re.compile(r"@(?:interface|implementation)\s+([A-Za-z_]\w*)")
    if refused:
        alt = "|".join(re.escape(u) for u in refused)
        pattern = re.compile(rf"{BOUNDARY}{re.escape(sp.new)}::(?:{alt})\b|[<\"]{re.escape(sp.new)}/(?:{alt})/")
    exempt_paths, exempt_prefixes = vellum_exemptions(native_root)
    for path in walk(native_root):
        relative = path.relative_to(native_root)
        if path.is_relative_to(root) or any(part in collisions.SKIP_DIRS or part.startswith("build")
                                            for part in relative.parts[:-1]):
            continue
        if relative.as_posix() in exempt_paths or relative.as_posix().startswith(exempt_prefixes or ("\0",)):
            continue
        data = path.read_bytes()
        native_blobs.setdefault(git_blob(data), relative.as_posix())
        if path.suffix.lower() in OBJC_SOURCE_SUFFIXES and b"\0" not in data:
            for name in declaration.findall(data.decode("utf-8", errors="replace")):
                native_classes.setdefault(name, relative.as_posix())
        if refused and b"\0" not in data:
            text = data.decode("utf-8", errors="replace")
            for match in pattern.finditer(text):
                findings.append({"path": relative.as_posix(), "line": text.count("\n", 0, match.start()) + 1,
                                 "match": match.group(0)})
    quarantine, third_party = [], []
    for path in walk(root):
        raw = path.read_bytes()
        if is_excluded(path.relative_to(root)):
            # Third-party files (fonts, vendored libraries) may legitimately
            # also ship natively; they are listed, not refused.
            blob = git_blob(raw)
            if blob in native_blobs:
                third_party.append({"mirror": path.relative_to(root).as_posix(), "native": native_blobs[blob]})
            continue
        renamed = writes[path].encode("utf-8") if path in writes else raw
        for data in {raw, renamed}:
            blob = git_blob(data)
            if blob != EMPTY_BLOB and blob in native_blobs:
                quarantine.append({"mirror": path.relative_to(root).as_posix(), "native": native_blobs[blob],
                                   "blob": blob})
    _, classes = discover(sp, root, walk(root))
    renamed_classes = {sp.new_class_prefix + c[len(sp.old_class):]: c for c in classes}
    objc = [{"class": new, "from": old, "native": native_classes[new]}
            for new, old in sorted(renamed_classes.items()) if new in native_classes]
    return {"refused_units": refused, "native_refusals": findings, "exempt_paths": sorted(exempt_paths),
            "blob_quarantine": quarantine,
            "third_party_shared_blobs": third_party, "objc_class_collisions": objc}


def collision_census(sp: Spelling, root: Path, native_root: Path | None,
                     writes: dict[Path, str] | None = None) -> dict:
    """Names the renamed mirror and the native tree would both declare."""
    if native_root is None:
        return {"status": "skipped", "reason": "no native tree"}
    mirror_files = [p for p in collisions.iter_sources(root, EXCLUDED_TOP_DIRS)]
    mirror_name = root.relative_to(native_root).parts[0] if root.is_relative_to(native_root) else None
    native_files = collisions.iter_sources(native_root, (mirror_name,) if mirror_name else ())
    native_files = [p for p in native_files if not p.is_relative_to(root)]
    mirror = collisions.census_tree(root, mirror_files, (sp.old, sp.new), (sp.old, sp.new))
    native = collisions.census_tree(native_root, native_files, None, None)
    result = collisions.compare(mirror, native, sp.new)
    result.update(native_refusals(sp, root, native_root, native, writes or {}))
    result["native_root"] = str(native_root)
    result["status"] = "checked"
    return result


def collision_errors(result: dict) -> list[str]:
    if result.get("status") != "checked":
        return []
    errors = []
    for side in ("mirror", "native"):
        if result["declarations"][side] == 0:
            errors.append(f"collision census is blind: no {side} declarations under the new root")
    for key in ("name_collisions", "macro_collisions", "include_guard_collisions", "header_path_collisions",
                "native_refusals", "blob_quarantine", "objc_class_collisions"):
        if result[key]:
            errors.append(f"{len(result[key])} {key.replace('_', ' ')}")
    return errors


def postconditions(sp: Spelling, before: dict[str, int], after: dict[str, int], report: dict) -> list[str]:
    errors = list(collision_errors(report.get("collisions", {})))
    old_ns, new_ns = f"namespace {sp.old}", f"namespace {sp.new}"
    expected = before[old_ns] + before[new_ns]
    if after[new_ns] != expected:
        errors.append(f"{new_ns} is {after[new_ns]}, expected {expected}")
    allowed = {f"{sp.old}::": report.get("excluded_literal_old_references", 0)}
    for name in (old_ns, f"{sp.old}::", f"include <{sp.old}/", f"path segment {sp.old}/<unit>",
                 f"dir include/{sp.old}"):
        if after[name] != allowed.get(name, 0):
            errors.append(f"{after[name]} '{name}' remain")
    for name in retained(sp):
        if after[name] != before[name]:
            errors.append(f"retained '{name}' changed from {before[name]} to {after[name]}")
    if report["move_collisions"]:
        errors.append(f"{len(report['move_collisions'])} moves would overwrite an existing path")
    if report["excluded_references"]:
        errors.append(f"{report['excluded_references']} {sp.old} references in excluded dirs")
    for row in report.get("literal_exclusions", []):
        if row["occurrences"] == 0:
            errors.append(f"literal exclusion matches nothing: {row['path']} {row['literal']}")
    for change in report.get("length_coupled_literals", []):
        errors.append(f"length-coupled literal would change: {change['path']}:{change['line']} {change['before']} "
                      "(fix it upstream in Pulp, or exclude it)")
    bb, ba = report.get("bytes_before"), report.get("bytes_after")
    if bb is not None and ba is not None:
        if before[old_ns] > 0:
            for name, n in bb.items():
                if name.startswith("target ") and n:
                    errors.append(f"the input already carries {n} '{name[7:]}': not a raw export")
        if ba[f"substring {sp.old}::"] != report["excluded_literal_old_references"]:
            errors.append(f"{ba[f'substring {sp.old}::']} '{sp.old}::' substrings remain; the exclusions allow "
                          f"{report['excluded_literal_old_references']}")
        for name, n in ba.items():
            if (name.startswith("word ") or name.startswith(f"substring namespace")
                    or name.startswith("substring include/") or name.startswith("substring <")) and n:
                errors.append(f"{n} '{name}' remain (boundary-free)")
    for problem in report.get("inverse_errors", []):
        errors.append(problem)
    if report["unclassified_suffix_files"]:
        errors.append(f"{len(report['unclassified_suffix_files'])} files have a suffix the edge table "
                      "neither scans nor declares binary")
    return errors


def move_tree(root: Path, destination: Path) -> list[str]:
    """Move every file under root to the same relative path under destination (Decision 0010, option a).

    Refuses to merge into an existing non-empty destination, so a move can
    never interleave renamed units with native files. Returns the moved paths.
    """
    if destination.exists() and any(destination.iterdir()):
        raise UsageError(f"move destination {destination} already holds files")
    moved = []
    for path in walk(root):
        relative = path.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        moved.append(relative.as_posix())
    for dirpath, _, _ in sorted(os.walk(root), key=lambda e: -len(e[0])):
        directory = Path(dirpath)
        if not any(directory.iterdir()):
            directory.rmdir()
    return moved


def apply(sp: Spelling, root: Path, result: dict) -> None:
    for path, text in result["writes"].items():
        path.write_bytes(text.encode("utf-8"))
    for source, target in result["moves"]:
        destination = root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        (root / source).rename(destination)
    for dirpath, _, _ in sorted(os.walk(root), key=lambda e: -len(e[0])):
        directory = Path(dirpath)
        if directory != root and not any(directory.iterdir()) and sp.old in directory.relative_to(root).parts:
            directory.rmdir()


PULP_TABLE = "tools/scripts/vellum_mirror_edge_patterns.json"
VENDORED_PIN = Path("tools/provenance/mirror_edge_patterns.pin.json")
INERTNESS_CHECKER = Path("tools/mirror/verify_mirror_inertness.py")
MIRROR_ROOTS_LINE = re.compile(r"^MIRROR_ROOTS = \{.*\}$", re.M)


def git(repo: Path, *args: str) -> bytes:
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def plan_roots_flip(vellum_root: Path, pulp: Path, new: str) -> tuple[dict[Path, bytes], dict, list[str]]:
    """The Vellum-side roots step, in the same change as the rename.

    Vendors Pulp's edge table at the checkout's HEAD byte for byte, repins it,
    and sets the inertness checker's MIRROR_ROOTS literal to the table's roots,
    so the edge scan and the rename read one table.
    """
    errors: list[str] = []
    commit = git(pulp, "rev-parse", "HEAD").decode().strip()
    blob = git(pulp, "rev-parse", f"HEAD:{PULP_TABLE}").decode().strip()
    content = git(pulp, "show", f"HEAD:{PULP_TABLE}")
    table = json.loads(content)
    roots = table.get("roots", {})
    for kind in ("namespace", "include", "target"):
        if new not in roots.get(kind, []):
            errors.append(f"Pulp {commit[:12]}'s edge table roots.{kind} {roots.get(kind)} do not list {new!r}: "
                          "land the Pulp roots PR first")
    writes: dict[Path, bytes] = {vellum_root / DEFAULT_TABLE: content}
    pin_path = vellum_root / VENDORED_PIN
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    pin["pulp_commit"], pin["git_blob"] = commit, blob
    writes[pin_path] = (json.dumps(pin, indent=2) + "\n").encode()
    checker = vellum_root / INERTNESS_CHECKER
    source = checker.read_text(encoding="utf-8")
    literal = "MIRROR_ROOTS = {" + ", ".join(
        f'"{kind}": ({", ".join(repr(r).replace(chr(39), chr(34)) for r in roots.get(kind, []))},)'
        if len(roots.get(kind, [])) == 1 else
        f'"{kind}": ({", ".join(repr(r).replace(chr(39), chr(34)) for r in roots.get(kind, []))})'
        for kind in ("namespace", "include")) + "}"
    found = MIRROR_ROOTS_LINE.findall(source)
    if len(found) != 1:
        errors.append(f"{INERTNESS_CHECKER} has {len(found)} MIRROR_ROOTS lines, expected exactly 1")
    else:
        writes[checker] = MIRROR_ROOTS_LINE.sub(literal, source).encode()
    info = {"pulp_commit": commit, "git_blob": blob, "roots": roots, "mirror_roots_literal": literal,
            "files": sorted(p.relative_to(vellum_root).as_posix() for p in writes)}
    return writes, table, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help="the mirror tree (Vellum's mirror/)")
    parser.add_argument("--to", required=True, help="the new namespace and include spelling")
    parser.add_argument("--edge-table", type=Path,
                        help=f"the mirror-edge table (default: <root>/../{DEFAULT_TABLE})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    mode.add_argument("--apply", action="store_true", help="rewrite and move in place")
    parser.add_argument("--allow-unlisted-roots", action="store_true",
                        help="apply even though the edge table's roots do not list --to (scratch copies only)")
    parser.add_argument("--expect-namespace-count", type=int,
                        help="fail unless the input has exactly this many `namespace OLD`")
    parser.add_argument("--native-root", type=Path,
                        help="the native Vellum tree to check for collisions (default: <root>/..)")
    parser.add_argument("--no-collision-census", action="store_true", help="skip the collision census")
    parser.add_argument("--literal-exclusions", type=Path,
                        help=f"literals to leave unchanged (default: {DEFAULT_EXCLUSIONS.name} beside this tool)")
    parser.add_argument("--move-to", type=Path, metavar="NATIVE_ROOT",
                        help="with --apply, move the renamed tree to this native root (relative to <root>/..) in the "
                             "same run, so the mirror root never holds renamed files (Decision 0010, option a)")
    parser.add_argument("--flip-roots-from", type=Path, metavar="PULP_CHECKOUT",
                        help="also vendor Pulp's edge table from this checkout's HEAD, repin it and set "
                             "MIRROR_ROOTS (the Vellum side of the roots step, in the same change)")
    parser.add_argument("--output", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    table = args.edge_table or (root.parent / DEFAULT_TABLE)
    flip_writes: dict[Path, bytes] = {}
    flip_errors: list[str] = []
    flip_info = None
    try:
        if args.flip_roots_from:
            flip_writes, flipped_table, flip_errors = plan_roots_flip(root.parent, args.flip_roots_from, args.to)
            flip_info = {"files": sorted(p.relative_to(root.parent).as_posix() for p in flip_writes)}
            table = f"{args.flip_roots_from}:HEAD:{PULP_TABLE}"
            sp = spelling_from_table(flipped_table, table, args.to)
        else:
            sp = load_spelling(table, args.to)
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        exclusions = load_exclusions(args.literal_exclusions or DEFAULT_EXCLUSIONS)
    except (UsageError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    result = plan(sp, root, exclusions)
    report = result["report"]
    report["edge_table"] = str(table)
    if args.flip_roots_from:
        report["roots_flip"] = {**(flip_info or {}), "errors": flip_errors}
    before = report["before"]

    def finish(status: str, code: int, message: str | None = None) -> int:
        report["status"] = status
        text = json.dumps(report, indent=2, sort_keys=True)
        print(text)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        if message:
            print(f"error: {message}", file=sys.stderr)
        return code

    old_ns, new_ns = f"namespace {sp.old}", f"namespace {sp.new}"
    if before[old_ns] == 0 and before[new_ns] == 0:
        return finish("instrument-blind", 3, f"control count is zero: no `{old_ns}` or `{new_ns}`, "
                      "so a zero change count proves nothing")
    if args.expect_namespace_count is not None and before[old_ns] != args.expect_namespace_count:
        return finish("control-mismatch", 1, f"expected {args.expect_namespace_count} `{old_ns}`, "
                      f"found {before[old_ns]}")
    if flip_errors:
        return finish("roots-not-listed", 1, "; ".join(flip_errors))
    if args.apply and not report["roots_step"]["ready"] and not args.allow_unlisted_roots:
        return finish("roots-not-listed", 1, f"the edge table's roots do not list {sp.new!r}; "
                      "land the roots step first (see roots_step)")

    native_root = None if args.no_collision_census else (args.native_root or root.parent).resolve()
    report["collisions"] = collision_census(sp, root, native_root, result["writes"])
    report["bytes_after"] = byte_counts(result["byte_patterns"], result["files"], result["writes"])
    # Self-check with the tool-free verifier's inverse map: every rewritten file
    # must invert to its input byte for byte.
    report["inverse_errors"] = [
        f"inverse({path.relative_to(root).as_posix()}) differs from its input"
        for path, text in result["writes"].items()
        if identity.inverse_bytes(text.encode("utf-8"), sp.old, sp.new, sp.new_class_prefix) != path.read_bytes()]
    already = before[old_ns] == 0 and not result["writes"] and not result["moves"]
    # Everything --apply would be judged on is known now; refuse before writing.
    predicted = postconditions(sp, before, count(sp, root, result["files"], result["writes"]), report)
    if args.move_to and not args.apply:
        report["move_planned"] = str(args.move_to)
    if args.move_to and args.apply:
        destination = (root.parent / args.move_to).resolve()
        if destination.exists() and any(destination.iterdir()):
            report["errors"] = predicted + [f"move destination {args.move_to} already holds files"]
            return finish("refused", 1, "the move destination is not empty, so nothing was written")
    if args.apply and predicted:
        report["errors"] = predicted
        return finish("refused", 1, "the dry run fails, so nothing was written")
    if args.apply:
        for path, data in flip_writes.items():
            path.write_bytes(data)
        apply(sp, root, result)
        after = count(sp, root, walk(root))
        report["bytes_after"] = byte_counts(result["byte_patterns"], walk(root))
        if args.move_to:
            destination = (root.parent / args.move_to).resolve()
            if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
                return finish("refused", 1, f"--move-to {args.move_to} overlaps the mirror root")
            report["moved_to"] = destination.relative_to(root.parent).as_posix()
            report["moved_files"] = len(move_tree(root, destination))
        report["mode"] = "apply"
    else:
        after = count(sp, root, result["files"], result["writes"])
        report["mode"] = "dry-run"
    report["after"] = after
    errors = postconditions(sp, before, after, report)
    report["errors"] = errors
    if errors:
        return finish("fail", 1)
    return finish("already-renamed" if already else "pass", 0)


if __name__ == "__main__":
    sys.exit(main())
