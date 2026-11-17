#!/usr/bin/env python3
"""The port respell: Pulp names a framework/ file may carry under a Vellum spelling.

A file ported into framework/ joins as Pulp's bytes put through the transfer's
rename, and it must carry no name debt. Some Pulp names survive that rename:
`PULP_*` macros and `pulp_*` C names. The respell table,
`provenance/framework/respell.json`, lists each such name and its Vellum
spelling, and every entry is one of five kinds, each recognised from bytes,
never chosen by hand:

- `closure-macro` (a): a `PULP_<X>` macro the landed closures define. A ported
  source uses `VELLUM_<X>`, and the build plan gives that source `VELLUM_<X>`
  with the closure's value. Transferred files keep the `PULP_` spelling.
- `link-export` (b): a `pulp_<x>` name a closure lists in `link_exports`.
- `file-macro` (c): a `PULP_<X>` macro its evidence file `#define`s itself. It
  is respelled in that file only.
- `extern-c` (d): a `pulp_<x>` function or object its evidence file defines
  with C linkage. It is respelled in every framework/ file, so callers and the
  definition agree, and so Pulp's and Vellum's copies no longer share a
  linker-visible name.
- `opt-in-switch` (e): a `PULP_<X>` that its evidence file only tests
  (`defined()`, `#ifdef`, `#ifndef`), and that no framework/ file and no
  closure defines. It is respelled in every framework/ file that tests it.

The respell replaces identifier tokens in code and comments and never touches
string or character literals, so a string's bytes are never changed.

This module is pure: it reads bytes it is given and runs no git.
tools/provenance/verify_extraction.py proves the table against Pulp and the
transfer record, and tools/mirror/mirror_package.py plans the aliases.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

TABLE = "provenance/framework/respell.json"
SCHEMA = "vellum.framework-respell.v1"
ENTRY_KEYS = {"old", "new", "kind", "evidence"}
KINDS = ("closure-macro", "link-export", "file-macro", "extern-c", "opt-in-switch")
# Kinds whose name is respelled in every framework/ file, not only in ported ones.
FRAMEWORK_WIDE = ("link-export", "extern-c", "opt-in-switch")
MACRO_KINDS = ("closure-macro", "file-macro", "opt-in-switch")
C_NAME_KINDS = ("link-export", "extern-c")
MACRO = re.compile(r"PULP_[A-Za-z0-9_]+\Z")
C_NAME = re.compile(r"pulp_[A-Za-z0-9_]+\Z")
TEXT_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".h", ".hh", ".hpp", ".hxx", ".inc", ".ipp"}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PULP_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:PULP_|pulp_)[A-Za-z0-9_]+")


class RespellError(ValueError):
    pass


def respelled(old: str) -> str:
    """The Vellum spelling of a Pulp name: the prefix alone changes."""
    if old.startswith("PULP_"):
        return "VELLUM_" + old[len("PULP_"):]
    if old.startswith("pulp_"):
        return "vellum_" + old[len("pulp_"):]
    raise RespellError(f"{old} has no PULP_ or pulp_ prefix")


def respells(path: str) -> bool:
    """Whether the respell reads this file: C, C++ and Objective-C sources and headers."""
    return Path(path).suffix.lower() in TEXT_SUFFIXES


# -- lexing -------------------------------------------------------------------

def segments(text: str) -> list[tuple[str, int, int]]:
    """Split C-family text into ("code" | "comment" | "literal", start, end) spans.

    Literals are string and character literals, raw strings included. A
    quote that follows a digit is a digit separator, not a character literal.
    """
    out: list[tuple[str, int, int]] = []
    i, n, start = 0, len(text), 0

    def flush(kind: str, a: int, b: int) -> None:
        if b > a:
            out.append((kind, a, b))

    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            flush("code", start, i)
            end = text.find("\n", i)
            end = n if end < 0 else end
            flush("comment", i, end)
            i = start = end
        elif c == "/" and text.startswith("/*", i):
            flush("code", start, i)
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            flush("comment", i, end)
            i = start = end
        elif c == '"' or (c == "'" and not (i > 0 and text[i - 1].isalnum())):
            raw = (c == '"' and i > 0 and text[i - 1] == "R"
                   and not (i > 1 and (text[i - 2].isalnum() or text[i - 2] == "_")
                            and text[i - 2] not in "uUL8"))
            flush("code", start, i)
            if raw:
                open_paren = text.find("(", i)
                delimiter = text[i + 1:open_paren]
                close = text.find(")" + delimiter + '"', open_paren)
                end = n if close < 0 else close + len(delimiter) + 2
            else:
                j = i + 1
                while j < n and text[j] != c and text[j] != "\n":
                    j += 2 if text[j] == "\\" else 1
                end = min(j + 1, n)
            flush("literal", i, end)
            i = start = end
        else:
            i += 1
    flush("code", start, n)
    return out


def blanked(text: str, keep_comments: bool = False) -> str:
    """The text with literals (and comments, unless kept) replaced by spaces, newlines kept."""
    parts = []
    for kind, a, b in segments(text):
        piece = text[a:b]
        if kind == "literal" or (kind == "comment" and not keep_comments):
            piece = re.sub(r"[^\n]", " ", piece)
        parts.append(piece)
    return "".join(parts)


def tokens(text: str, keep_comments: bool = True) -> set[str]:
    """Identifier tokens outside literals (and outside comments unless kept)."""
    return set(IDENT.findall(blanked(text, keep_comments)))


def pulp_tokens(text: str) -> set[str]:
    """PULP_/pulp_ identifier tokens in code and comments."""
    return set(PULP_TOKEN.findall(blanked(text, keep_comments=True)))


# -- recognising each kind ------------------------------------------------------

DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(PULP_[A-Za-z0-9_]+)", re.M)


def self_defined_macros(text: str) -> set[str]:
    """PULP_ macros the text #defines itself (kind c)."""
    return set(DEFINE.findall(blanked(text)))


EXTERN_C = re.compile(r"\bextern\s*\Z")


def _extern_c_starts(text: str) -> list[int]:
    """Offsets just after each `extern "C"` in code."""
    starts = []
    spans = segments(text)
    for index, (kind, a, b) in enumerate(spans):
        if kind == "literal" and text[a:b] == '"C"' and index > 0 and spans[index - 1][0] == "code":
            before = text[spans[index - 1][1]:a]
            if EXTERN_C.search(before):
                starts.append(b)
    return starts


def _match(code: str, i: int, opener: str, closer: str) -> int:
    depth = 0
    while i < len(code):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(code)


def _declarations(code: str, start: int, end: int) -> list[tuple[str, bool]]:
    """Top-level declarations in code[start:end]: (text before its body or ;, is_definition)."""
    out: list[tuple[str, bool]] = []
    i, head = start, start
    while i < end:
        c = code[i]
        if c == "(":
            i = _match(code, i, "(", ")") + 1
            continue
        if c == "[":
            i = _match(code, i, "[", "]") + 1
            continue
        if c == "{":
            out.append((code[head:i], True))
            i = _match(code, i, "{", "}") + 1
            if i < end and code[i] == ";":
                i += 1
            head = i
            continue
        if c == ";":
            decl = code[head:i]
            out.append((decl, "=" in decl or re.search(r"\]\s*\Z", decl) is not None))
            i += 1
            head = i
            continue
        i += 1
    return out


def _declared_name(declaration: str) -> str | None:
    """The name a declaration declares: the identifier before its first top-level ( [ or =."""
    depth = 0
    for index, c in enumerate(declaration):
        if c in "([=" and depth == 0:
            names = IDENT.findall(declaration[:index])
            return names[-1] if names else None
        if c in "<":
            depth += 1
        elif c in ">" and depth:
            depth -= 1
    return None


def extern_c_definitions(text: str) -> set[str]:
    """pulp_ functions and objects the text defines with C linkage (kind d).

    Both the single-declaration form (`extern "C" T pulp_f(...) {...}`) and
    the block form (`extern "C" { ... }`) count. A declaration without a
    body or initializer is a use, not a definition.
    """
    code = blanked(text)
    names: set[str] = set()
    for start in _extern_c_starts(text):
        i = start
        while i < len(code) and code[i].isspace():
            i += 1
        if i < len(code) and code[i] == "{":
            close = _match(code, i, "{", "}")
            declarations = _declarations(code, i + 1, close)
        else:
            declarations = _declarations(code, i, len(code))[:1]
        for declaration, is_definition in declarations:
            name = _declared_name(declaration)
            if is_definition and name and C_NAME.match(name):
                names.add(name)
    return names


TEST_CONTEXTS = (
    r"\bdefined\s*\(\s*{name}\s*\)",
    r"\bdefined\s+{name}(?![A-Za-z0-9_])",
    r"^[ \t]*#[ \t]*if(?:n)?def[ \t]+{name}(?![A-Za-z0-9_])",
)
ANY_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def macro_uses(text: str, name: str) -> tuple[int, int]:
    """(tested, other) counts of a macro in code: tested only by defined/#ifdef/#ifndef, or used otherwise."""
    code = blanked(text)
    total = len(re.findall(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", code))
    tested = sum(len(re.findall(pattern.format(name=re.escape(name)), code, re.M)) for pattern in TEST_CONTEXTS)
    return tested, total - tested


def opt_in_switches(text: str) -> set[str]:
    """PULP_ macros the text only tests and never uses for a value or defines (candidates for kind e)."""
    found = set()
    for name in {t for t in tokens(text, keep_comments=False) if MACRO.match(t)}:
        tested, other = macro_uses(text, name)
        if tested and not other:
            found.add(name)
    return found


# -- applying -------------------------------------------------------------------

def apply(text: str, mapping: dict[str, str]) -> str:
    """Respell identifier tokens in code and comments; literals are never changed."""
    if not mapping:
        return text
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
                         + r")(?![A-Za-z0-9_])")
    parts = []
    for kind, a, b in segments(text):
        piece = text[a:b]
        if kind != "literal":
            piece = pattern.sub(lambda m: mapping[m.group(1)], piece)
        parts.append(piece)
    return "".join(parts)


def mapping_for(entries: list[dict[str, Any]], path: str, ported: bool) -> dict[str, str]:
    """The old -> new names respelled in one framework/ file."""
    out = {}
    for entry in entries:
        kind = entry["kind"]
        if kind == "file-macro" and entry["evidence"] != path:
            continue
        if kind == "closure-macro" and not ported:
            continue
        out[entry["old"]] = entry["new"]
    return out


def apply_bytes(data: bytes, entries: list[dict[str, Any]], path: str, ported: bool) -> bytes:
    if not respells(path):
        return data
    mapping = mapping_for(entries, path, ported)
    if not mapping:
        return data
    return apply(data.decode("utf-8"), mapping).encode("utf-8")


# -- the table ----------------------------------------------------------------

def entry_errors(entry: Any, where: str) -> list[str]:
    if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
        return [f"{where}: must have exactly {sorted(ENTRY_KEYS)}"]
    errors: list[str] = []
    old, new, kind, evidence = entry["old"], entry["new"], entry["kind"], entry["evidence"]
    if kind not in KINDS:
        return [f"{where}: kind must be one of {list(KINDS)}"]
    pattern = MACRO if kind in MACRO_KINDS else C_NAME
    if not isinstance(old, str) or not pattern.match(old):
        errors.append(f"{where}: a {kind} entry respells a {'PULP_' if kind in MACRO_KINDS else 'pulp_'} name, "
                      f"not {old!r}")
    elif new != respelled(old):
        errors.append(f"{where}: {old} respells to {respelled(old)}, not {new!r}")
    if kind in ("closure-macro", "link-export"):
        if evidence is not None:
            errors.append(f"{where}: a {kind} entry's evidence is the closures; evidence is null")
    elif not (isinstance(evidence, str) and evidence.startswith("framework/") and respells(evidence)):
        errors.append(f"{where}: a {kind} entry names the framework/ source whose bytes prove it")
    return errors


def parse(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        document = json.loads(text)
    except ValueError as exc:
        return [], [f"{TABLE}: unreadable: {exc}"]
    if not isinstance(document, dict) or set(document) != {"schema", "entries"} \
            or document["schema"] != SCHEMA or not isinstance(document["entries"], list):
        return [], [f"{TABLE}: must be an object with schema {SCHEMA} and an entries list"]
    entries = document["entries"]
    errors = [e for index, entry in enumerate(entries) for e in entry_errors(entry, f"{TABLE} entries[{index}]")]
    if not errors:
        keys = [(entry["old"], entry["kind"], entry["evidence"] or "") for entry in entries]
        if keys != sorted(keys):
            errors.append(f"{TABLE}: entries are sorted by old name, kind and evidence")
        seen: dict[str, str] = {}
        for entry in entries:
            other = seen.get(entry["old"])
            # A file macro may be defined in several files, each its own entry;
            # any other name has one kind.
            if other is not None and not (other == entry["kind"] == "file-macro"):
                errors.append(f"{TABLE}: {entry['old']} is listed twice")
            seen[entry["old"]] = entry["kind"]
    return ([] if errors else entries), errors


def load(repo: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """(entries, errors) for the checkout's table; a missing table respells nothing."""
    path = repo / TABLE
    if path.is_symlink():
        return [], [f"{TABLE}: is a symlink"]
    if not path.exists():
        return [], []
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{TABLE}: unreadable: {exc}"]


def render(entries: Iterable[dict[str, Any]]) -> str:
    rows = sorted(entries, key=lambda e: (e["old"], e["kind"], e["evidence"] or ""))
    return json.dumps({"schema": SCHEMA, "entries": rows}, indent=2) + "\n"


# -- derivation -----------------------------------------------------------------

def derive(ported: dict[str, str], transferred: dict[str, str], closure_macros: set[str],
           link_exports: set[str], current: dict[str, str]) -> list[dict[str, Any]]:
    """The table the evidence implies.

    `ported`: each Pulp-origin ledger-added source's Pulp bytes, after the
    transfer rename and before any respell, by framework path.
    `transferred`: each transferred source's bytes as the transfer recorded
    them, by framework path. `current`: every framework/ source as it is now.
    `closure_macros`: the PULP_ names the closures define. `link_exports`: the
    pulp_ names the closures list as link exports.
    """
    entries: dict[tuple[str, str, str | None], dict[str, Any]] = {}

    def add(old: str, kind: str, evidence: str | None) -> None:
        entries[(old, kind, evidence)] = {"old": old, "new": respelled(old), "kind": kind, "evidence": evidence}

    # (d) from every source: a C-linkage definition anywhere in framework/.
    for path, text in sorted({**transferred, **ported}.items()):
        for name in extern_c_definitions(text):
            add(name, "extern-c", path)
    defined_c = {key[0] for key in entries}
    # Every file that #defines a PULP_ macro, as framework/ holds it now or as Pulp had it.
    defined_anywhere = set(closure_macros)
    for text in [*current.values(), *transferred.values(), *ported.values()]:
        defined_anywhere |= {"PULP_" + name[len("VELLUM_"):] if name.startswith("VELLUM_") else name
                             for name in ANY_DEFINE.findall(blanked(text))}
    for path, text in sorted(ported.items()):
        used = pulp_tokens(text)
        for name in sorted(used):
            if name in defined_c:
                continue
            if name in link_exports:
                add(name, "link-export", None)
            elif name in self_defined_macros(text):
                add(name, "file-macro", path)
            elif name in closure_macros:
                add(name, "closure-macro", None)
            elif MACRO.match(name) and name in opt_in_switches(text) and name not in defined_anywhere:
                add(name, "opt-in-switch", path)
    # A name that several kinds would claim keeps the one entry per name the
    # table allows: the first kind found above. A name with two evidence
    # files of the same framework-wide kind keeps the first path.
    chosen: dict[str, dict[str, Any]] = {}
    for key in sorted(entries):
        entry = entries[key]
        if entry["kind"] == "file-macro":
            chosen[f"{entry['old']}\0{entry['evidence']}"] = entry
        elif entry["old"] not in chosen:
            chosen[entry["old"]] = entry
    return sorted(chosen.values(), key=lambda e: (e["old"], e["kind"], e["evidence"] or ""))


def consistency_errors(entries: list[dict[str, Any]], current: dict[str, str]) -> list[str]:
    """No framework/ file still spells a respelled name the old way.

    A framework-wide name is respelled everywhere, so a caller and its
    definition agree; a file macro is respelled in its own file.
    """
    errors = []
    for entry in entries:
        kind, old = entry["kind"], entry["old"]
        if kind in FRAMEWORK_WIDE:
            stale = sorted(path for path, text in current.items() if old in pulp_tokens(text))
        elif kind == "file-macro":
            text = current.get(entry["evidence"])
            stale = [entry["evidence"]] if text is not None and old in pulp_tokens(text) else []
        else:
            continue
        for path in stale:
            errors.append(f"{TABLE}: {path} still spells {old}; a {kind} name is respelled "
                          f"{'in every framework/ file' if kind in FRAMEWORK_WIDE else 'in its own file'}")
    return errors


def switch_errors(entries: list[dict[str, Any]], current: dict[str, str], closure_macros: set[str],
                  repo_texts: dict[str, str]) -> list[str]:
    """An opt-in switch stays one: nothing defines it under either spelling, and nothing sets the old one."""
    errors = []
    for entry in entries:
        if entry["kind"] != "opt-in-switch":
            continue
        old, new = entry["old"], entry["new"]
        if old in closure_macros:
            errors.append(f"{TABLE}: {old} is defined by a closure, so it is not an opt-in switch")
        for path, text in sorted(current.items()):
            if {old, new} & set(ANY_DEFINE.findall(blanked(text))):
                errors.append(f"{TABLE}: {path} defines {old} or {new}, so it is not an opt-in switch")
            if new in tokens(text, keep_comments=False):
                tested, other = macro_uses(text, new)
                if other:
                    errors.append(f"{TABLE}: {path} uses {new} for its value; an opt-in switch is only tested")
        for path, text in sorted(repo_texts.items()):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", text):
                errors.append(f"{TABLE}: {path} still sets or names {old}; the switch is now {new}")
    return errors
