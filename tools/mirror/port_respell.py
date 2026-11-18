#!/usr/bin/env python3
"""The port respell: Pulp C-linkage names a framework/ file carries under a Vellum spelling.

A file ported into framework/ joins as Pulp's bytes put through the transfer's
rename. Some Pulp names survive that rename, and a `pulp_*` function or object
defined with C linkage is the one that matters: Pulp's copy and Vellum's copy
would share a linker-visible name in one process. The respell table,
`provenance/framework/respell.json`, lists each such name and its Vellum
spelling. Every entry is of the one kind the rule recognises, `extern-c`: a
`pulp_<x>` its evidence file defines with C linkage, recognised from bytes and
never chosen by hand. It is respelled in every framework/ file, so callers and
the definition agree.

`PULP_*` macro names clash with nothing; they stay as counted name debt, and a
table entry of any other kind is refused.

The respell replaces identifier tokens in code and comments and never touches
string or character literals or `#include <...>` header names, so a string's
bytes are never changed. A file that builds a table name by token pasting
(`pulp_ ## x`) is refused, since no token respell can follow it.

This module is pure: it reads bytes it is given and runs no git.
tools/provenance/verify_extraction.py proves the table against Pulp and the
transfer record.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

TABLE = "provenance/framework/respell.json"
SCHEMA = "vellum.framework-respell.v1"
ENTRY_KEYS = {"old", "new", "kind", "evidence"}
KINDS = ("extern-c",)
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

ENCODING_PREFIXES = frozenset({"u8", "u", "U", "L"})
RAW_PREFIXES = frozenset({"R"} | {p + "R" for p in ENCODING_PREFIXES})
# A header name in angle brackets: `#include <...>`, `#include_next <...>`,
# `#import <...>` and `__has_include(<...>)`. The `<` that opens it.
HEADER_NAME = re.compile(r"(?:^[ \t]*#[ \t]*(?:include|include_next|import)[ \t]*|"
                         r"\b__has_include(?:_next)?[ \t]*\([ \t]*)<\Z", re.M)


def _ident_run_before(text: str, i: int) -> str:
    """The identifier characters immediately before offset i."""
    j = i
    while j > 0 and (text[j - 1].isalnum() or text[j - 1] == "_"):
        j -= 1
    return text[j:i]


def _opens_char_literal(text: str, i: int) -> bool:
    """Whether the quote at i opens a character literal rather than being a digit separator.

    A quote glued to a numeric literal or identifier is a separator, except
    after a whole-token encoding prefix (u8, u, U, L).
    """
    run = _ident_run_before(text, i)
    return not run or run in ENCODING_PREFIXES


def _opens_header_name(text: str, i: int) -> bool:
    line_start = text.rfind("\n", 0, i) + 1
    return HEADER_NAME.search(text[line_start:i + 1]) is not None


def segments(text: str) -> list[tuple[str, int, int]]:
    """Split C-family text into ("code" | "comment" | "literal", start, end) spans.

    Literals are string and character literals, raw strings included, and
    header names in angle brackets. A quote that follows a digit is a digit
    separator, not a character literal.
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
        elif c == "<" and _opens_header_name(text, i):
            flush("code", start, i)
            j = i + 1
            while j < n and text[j] not in ">\n":
                j += 1
            end = j + 1 if j < n and text[j] == ">" else j
            flush("literal", i, end)
            i = start = end
        elif c == '"' or (c == "'" and _opens_char_literal(text, i)):
            raw = c == '"' and _ident_run_before(text, i) in RAW_PREFIXES
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


PASTE = re.compile(r"(?<![A-Za-z0-9_])((?:PULP_|pulp_)[A-Za-z0-9_]*)[ \t]*##|##[ \t]*((?:PULP_|pulp_)[A-Za-z0-9_]*)")


def pasted_pulp_fragments(text: str) -> set[str]:
    """PULP_/pulp_ fragments the text glues to another token with `##`, in code.

    A name built by pasting never appears as one token, so the respell could
    not follow it. A fragment passed to a pasting macro as an argument
    (`CAT(pulp_, x)`) is not seen.
    """
    return {left or right for left, right in PASTE.findall(blanked(text))}


# -- recognising C-linkage definitions --------------------------------------------

EXTERN_C = re.compile(r"\bextern\s*\Z")
# Attribute groups a declaration may carry anywhere: GNU, MSVC and standard.
ATTRIBUTE_HEADS = re.compile(r"\b(?:__attribute__|__declspec|alignas|__asm__|__asm|asm)\s*\(|\[\[")
TRAILING_WORDS = re.compile(r"\b(?:const|volatile|noexcept|override|final|throw)\s*\Z")
NOT_A_DEFINITION = re.compile(r"^\s*(?:extern|typedef|using|static|friend|template)\b")


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


def _match_back(code: str, i: int, opener: str, closer: str) -> int:
    """The index of the opener matching the closer at i, scanning backwards; -1 when unbalanced."""
    depth = 0
    while i >= 0:
        if code[i] == closer:
            depth += 1
        elif code[i] == opener:
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return -1


def _without_preprocessor_lines(code: str) -> str:
    """Code with each preprocessor directive (and its continuation lines) blanked, offsets kept."""
    out, continued = [], False
    for line in code.split("\n"):
        directive = continued or line.lstrip().startswith("#")
        continued = directive and line.rstrip().endswith("\\")
        out.append(" " * len(line) if directive else line)
    return "\n".join(out)


def _without_attributes(declaration: str) -> str:
    """The declaration with attribute groups (`__attribute__((...))`, `__declspec(...)`, `[[...]]`) blanked."""
    text = declaration
    while True:
        found = ATTRIBUTE_HEADS.search(text)
        if found is None:
            return text
        if found.group(0) == "[[":
            close = text.find("]]", found.end())
            end = len(text) if close < 0 else close + 2
        else:
            end = _match(text, found.end() - 1, "(", ")") + 1
        text = text[:found.start()] + " " * (end - found.start()) + text[end:]


def _declarations(code: str, start: int, end: int) -> list[tuple[str, bool]]:
    """Top-level declarations in code[start:end]: (text before its body or ;, has a body)."""
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
            out.append((code[head:i], False))
            i += 1
            head = i
            continue
        i += 1
    return out


def declarator(declaration: str) -> tuple[str | None, bool, bool]:
    """(declared name, declares a function, has an initializer) for one declaration's text.

    Attribute groups are skipped, then trailing parameter lists, array bounds
    and qualifiers are peeled off and a parenthesized declarator such as
    `(*pulp_fp)` is unwrapped; the name is the last identifier left. A name
    followed by a parameter list declares a function; a pointer to one is an
    object.
    """
    text = _without_attributes(declaration)
    initializer = False
    depth = 0
    for index, c in enumerate(text):
        if c in "(<[":
            depth += 1
        elif c in ")>]" and depth:
            depth -= 1
        elif depth == 0 and (c == "=" or text.startswith("->", index)):
            initializer = c == "="
            text = text[:index]
            break
    function: bool | None = None
    while True:
        text = text.rstrip()
        trailing = TRAILING_WORDS.search(text)
        if trailing:
            text = text[:trailing.start()]
            continue
        if text.endswith("]"):
            opener = _match_back(text, len(text) - 1, "[", "]")
            if opener < 0:
                return None, False, initializer
            text = text[:opener]
            function = False if function is None else function
            continue
        if text.endswith(")"):
            opener = _match_back(text, len(text) - 1, "(", ")")
            if opener < 0:
                return None, False, initializer
            before = text[:opener].rstrip()
            inner = text[opener + 1:-1]
            names_before = IDENT.findall(before)
            if names_before and names_before[-1] in ("noexcept", "throw") and before.endswith(names_before[-1]):
                text = before[:-len(names_before[-1])]
                continue
            pointer = inner.lstrip().startswith(("*", "&", "^"))
            if not pointer and before and (before[-1].isalnum() or before[-1] in "_)>"):
                # A parameter list after the declarator.
                function = True if function is None else function
                text = before
                continue
            # A parenthesized declarator: `(*name)`, `(&name)`, `(name)`. A
            # pointer or reference to a function or array is an object.
            if pointer:
                function = False
            text = inner
            continue
        break
    names = IDENT.findall(text)
    return (names[-1] if names else None), bool(function), initializer


def extern_c_definitions(text: str) -> set[str]:
    """pulp_ functions and objects the text defines with C linkage.

    Both the single-declaration form (`extern "C" T pulp_f(...) {...}`) and
    the block form (`extern "C" { ... }`) count. In the single form a
    declaration is a definition only with a body or an initializer. In the
    block form an object declared without `extern` is a definition too
    (`extern "C" { int pulp_obj; }`), while a function without a body, a
    typedef and a `static` (internal linkage) name are not.
    """
    code = _without_preprocessor_lines(blanked(text))
    names: set[str] = set()
    for start in _extern_c_starts(text):
        i = start
        while i < len(code) and code[i].isspace():
            i += 1
        block = i < len(code) and code[i] == "{"
        if block:
            declarations = _declarations(code, i + 1, _match(code, i, "{", "}"))
        else:
            declarations = _declarations(code, i, len(code))[:1]
        for declaration, body in declarations:
            if NOT_A_DEFINITION.match(_without_attributes(declaration)):
                continue
            name, function, initializer = declarator(declaration)
            if not name or not C_NAME.match(name):
                continue
            if body or initializer or (block and not function):
                names.add(name)
    return names


# -- applying -------------------------------------------------------------------

PREFIX_TOKEN = re.compile(r"(?<![A-Za-z0-9_])((?:PULP_|pulp_)[A-Za-z0-9_]*_)(?![A-Za-z0-9_])")


def comment_prefixes(text: str, mapping: dict[str, str]) -> dict[str, str]:
    """Shared prefixes a comment may name for several respelled names.

    A comment token ending in `_` (`pulp_motion_` in "the `pulp_motion_*`
    functions") is respelled when it is a strict prefix of at least two names
    this file respells, with the same prefix change. Any other PULP_ or pulp_
    token stays as it is, and counts as name debt.
    """
    present = [old for old in mapping if old in pulp_tokens(text)]
    out = {}
    # Candidates are read outside literals; apply() uses them in comments only.
    for token in set(PREFIX_TOKEN.findall(blanked(text, keep_comments=True))):
        if token not in mapping and sum(1 for old in present if old.startswith(token) and old != token) >= 2:
            out[token] = respelled(token)
    return out


def apply(text: str, mapping: dict[str, str]) -> str:
    """Respell identifier tokens in code and comments; literals are never changed.

    In comments, a shared prefix of two or more respelled names is respelled
    too (comment_prefixes). Apply it to Pulp-spelled bytes: the prefix rule
    counts the names the text still spells the Pulp way, so on a file already
    respelled it finds none. To bring a file up to a changed table, use
    reapply(). A forward respell of text that already spells one of its
    Vellum names is refused, so the mistake fails instead of leaving a stale
    prefix; undoing (a mapping back to Pulp names) is never refused.
    """
    if not mapping:
        return text
    forward = [new for new in mapping.values() if new.startswith(("VELLUM_", "vellum_"))]
    already = sorted(set(forward) & tokens(text, keep_comments=True))
    if already:
        raise RespellError(f"the text already spells {already[:3]}; it is respelled, so use reapply() "
                           "to bring it up to the table")
    prefixes = comment_prefixes(text, mapping)

    def compile(names: dict[str, str]) -> re.Pattern[str]:
        return re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True))
                          + r")(?![A-Za-z0-9_])")

    in_code = compile(mapping)
    in_comment = compile({**mapping, **prefixes})
    parts = []
    for kind, a, b in segments(text):
        piece = text[a:b]
        if kind == "code":
            piece = in_code.sub(lambda m: mapping[m.group(1)], piece)
        elif kind == "comment":
            piece = in_comment.sub(lambda m: {**mapping, **prefixes}[m.group(1)], piece)
        parts.append(piece)
    return "".join(parts)


def reapply(text: str, mapping: dict[str, str]) -> str:
    """apply() for a file that may already be respelled: undo the mapping, then apply it."""
    return apply(apply(text, {new: old for old, new in mapping.items()}), mapping)


def mapping_for(entries: list[dict[str, Any]], path: str) -> dict[str, str]:
    """The old -> new names respelled in one framework/ file: every entry applies to every file."""
    if not respells(path):
        return {}
    return {entry["old"]: entry["new"] for entry in entries}


def apply_bytes(data: bytes, entries: list[dict[str, Any]], path: str) -> bytes:
    mapping = mapping_for(entries, path)
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
        return [f"{where}: kind must be extern-c, not {kind!r}; macro names are name debt, not respelled"]
    if not isinstance(old, str) or not C_NAME.match(old):
        errors.append(f"{where}: an extern-c entry respells a pulp_ name, not {old!r}")
    elif new != respelled(old):
        errors.append(f"{where}: {old} respells to {respelled(old)}, not {new!r}")
    if not (isinstance(evidence, str) and evidence.startswith("framework/") and respells(evidence)):
        errors.append(f"{where}: an extern-c entry names the framework/ source whose bytes prove it")
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
        keys = [(entry["old"], entry["kind"], entry["evidence"]) for entry in entries]
        if keys != sorted(keys):
            errors.append(f"{TABLE}: entries are sorted by old name, kind and evidence")
        seen: set[str] = set()
        for entry in entries:
            if entry["old"] in seen:
                errors.append(f"{TABLE}: {entry['old']} is listed twice")
            seen.add(entry["old"])
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
    rows = sorted(entries, key=lambda e: (e["old"], e["kind"], e["evidence"]))
    return json.dumps({"schema": SCHEMA, "entries": rows}, indent=2) + "\n"


# -- derivation -----------------------------------------------------------------

def derive(evidence: dict[str, str]) -> list[dict[str, Any]]:
    """The table the evidence implies: one entry per pulp_ name some file defines with C linkage.

    `evidence` holds each framework/ source's origin bytes by path: a
    transferred file as the transfer recorded it, a ported file as Pulp has
    it after the rename. A name defined in several files keeps the first path.
    """
    chosen: dict[str, dict[str, Any]] = {}
    for path, text in sorted(evidence.items()):
        for name in sorted(extern_c_definitions(text)):
            chosen.setdefault(name, {"old": name, "new": respelled(name), "kind": "extern-c", "evidence": path})
    return sorted(chosen.values(), key=lambda e: (e["old"], e["kind"], e["evidence"]))


def consistency_errors(entries: list[dict[str, Any]], current: dict[str, str]) -> list[str]:
    """No framework/ file still spells a respelled name the old way, so a caller and its definition agree."""
    errors = []
    for entry in entries:
        old = entry["old"]
        for path in sorted(path for path, text in current.items() if old in pulp_tokens(text)):
            errors.append(f"{TABLE}: {path} still spells {old}; an extern-c name is respelled in every "
                          "framework/ file")
    return errors


def paste_errors(entries: list[dict[str, Any]], current: dict[str, str]) -> list[str]:
    """A table in force refuses a framework/ file that builds a PULP_/pulp_ name by token pasting."""
    if not entries:
        return []
    return [f"{TABLE}: {path} pastes {sorted(fragments)} with ##; a pasted Pulp name cannot be respelled"
            for path, text in sorted(current.items()) if (fragments := pasted_pulp_fragments(text))]


def hand_respells(origin: str, current: str, entries: list[dict[str, Any]]) -> list[str]:
    """Names the current bytes spell the Vellum way that the respell of the origin bytes does not.

    `origin` is the file as it joined framework/. A Pulp name the origin
    spells, that the current bytes spell the Vellum way, must be one the
    table's respell of the origin gives (a comment prefix included).
    Raises RespellError when the origin cannot be respelled.
    """
    expected = tokens(apply(origin, {entry["old"]: entry["new"] for entry in entries}), keep_comments=True)
    before, after = tokens(origin, keep_comments=True), tokens(current, keep_comments=True)
    return sorted(name for name in pulp_tokens(origin)
                  if (new := respelled(name)) in after and new not in before and new not in expected)
