#!/usr/bin/env python3
"""Fail when a module the web-dist page loads is missing from web-dist.

The browser resolves every relative ES module specifier against the file that
imports it, and a missing file surfaces only as a 404 and a page that never
boots. This walks the real import graph, starting at each module script of each
HTML page in the payload and at each shipped module, and reports every specifier that does not resolve to
a file inside the payload. Nothing is listed by hand, so a module added to the
sources but not to the copy list is caught the first time something imports it.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
import sys


# Dynamic bare specifiers the shipped modules may name. Emscripten's glue loads
# `node:module` behind an ENVIRONMENT_IS_NODE guard, so it never runs in a page.
# Anything else bare would need an import map the payload does not ship.
ALLOWED_DYNAMIC_BARE_SPECIFIERS = frozenset(("node:module",))

_ID_START = re.compile(r"[A-Za-z_$\u0080-\uffff]")
_ID_PART = re.compile(r"[A-Za-z0-9_$\u0080-\uffff]*")
_NUMBER = re.compile(r"\.?[0-9][0-9A-Za-z_.]*")
_REGEX_PREFIX_WORDS = frozenset((
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
))
# A `)` that closes the head of one of these statements is followed by the
# statement body, where a `/` can only begin a regular expression.
_CONTROL_HEAD_WORDS = frozenset(("if", "while", "for", "with"))


class TokenizeError(ValueError):
    """The source ends inside a comment, string, template or regular expression."""


def _tokens(source: str) -> list[tuple[str, str]]:
    """Tokenize ECMAScript just far enough to find module specifiers.

    Yields ("id", name), ("str", value), ("escaped-str", raw), ("num", ""),
    ("tpl", ""), ("tpl", "${") for a substitution opening, ("re", "") and
    ("p", char). Comments are dropped; string, template and regular
    expression bodies never produce identifier tokens, so text inside them is
    never mistaken for an import. Raises TokenizeError when the source ends
    inside an unterminated construct, so malformed input never reads as empty.
    """
    out: list[tuple[str, str]] = []
    n = len(source)
    i = 0
    # Each open `${` records the brace depth to return to the template at.
    templates: list[int] = []
    depth = 0
    # One entry per open `(`: whether it opens an if/while/for/with head.
    parens: list[bool] = []
    # Indices in `out` of each `)` that closed such a head.
    control_closes: set[int] = set()

    def read_template(start: int) -> int:
        j = start
        while j < n:
            c = source[j]
            if c == "\\":
                j += 2
            elif c == "`":
                out.append(("tpl", ""))
                return j + 1
            elif c == "$" and source.startswith("${", j):
                out.append(("tpl", "${"))
                templates.append(depth)
                return j + 2
            else:
                j += 1
        raise TokenizeError("unterminated template literal")

    while i < n:
        c = source[i]
        if c.isspace():
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise TokenizeError("unterminated block comment")
            i = end + 2
        elif c in "'\"":
            j = i + 1
            while j < n and source[j] != c and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            if j >= n or source[j] != c:
                raise TokenizeError("unterminated string literal")
            raw = source[i + 1:j]
            # Import specifier escapes are rejected below. Treating them as a
            # decoded path requires the full ECMAScript escape grammar; keeping
            # a distinct token makes the checker fail closed instead.
            out.append(("escaped-str" if "\\" in raw else "str", raw))
            i = j + 1
        elif c == "`":
            i = read_template(i + 1)
        elif c == "}" and templates and templates[-1] == depth:
            templates.pop()
            i = read_template(i + 1)
        elif c == "/" and _regex_allowed(out, control_closes):
            j = i + 1
            in_class = False
            while j < n and source[j] != "\n":
                d = source[j]
                if d == "\\":
                    j += 1
                elif d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                j += 1
            if j >= n or source[j] != "/":
                raise TokenizeError("unterminated regular expression literal")
            j += 1
            while j < n and _ID_PART.match(source, j).end() > j:
                j = _ID_PART.match(source, j).end()
            out.append(("re", ""))
            i = j
        elif _ID_START.match(c):
            end = _ID_PART.match(source, i).end()
            out.append(("id", source[i:end]))
            i = end
        elif c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            end = _NUMBER.match(source, i).end()
            out.append(("num", ""))
            i = end
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "(":
                parens.append(_opens_control_head(out))
            elif c == ")" and parens and parens.pop():
                control_closes.add(len(out))
            out.append(("p", c))
            i += 1
    if templates:
        raise TokenizeError("unterminated template literal")
    return out


def _after_dot(previous: list[tuple[str, str]]) -> bool:
    """True when the last token is a property name (`a.if`, `a?.return`)."""
    return len(previous) >= 2 and previous[-2] == ("p", ".")


def _opens_control_head(previous: list[tuple[str, str]]) -> bool:
    """True when a `(` here opens an if/while/for/with head (`for await (` too)."""
    if not previous or previous[-1][0] != "id":
        return False
    word = previous[-1][1]
    if word == "await" and len(previous) >= 2 and previous[-2] == ("id", "for"):
        return not (len(previous) >= 3 and previous[-3] == ("p", "."))
    return word in _CONTROL_HEAD_WORDS and not _after_dot(previous)


def _regex_allowed(previous: list[tuple[str, str]], control_closes: set[int]) -> bool:
    """True when a `/` here begins a regular expression rather than a division.

    Where the grammar leaves the choice open (a `}` that may end a block or an
    object literal), division is chosen: reading a regular expression as code
    can only add spurious edges, while reading code as a regular expression
    could hide a real one.
    """
    if not previous:
        return True
    kind, value = previous[-1]
    if kind == "id":
        return value in _REGEX_PREFIX_WORDS and not _after_dot(previous)
    if kind == "tpl":
        return value == "${"
    if kind in ("str", "escaped-str", "num", "re"):
        return False
    if value == ")":
        return len(previous) - 1 in control_closes
    return value not in "]}"


def specifiers(source: str) -> tuple[list[str], list[str], list[str]]:
    """Return static, dynamic, and unsupported import specifiers."""
    tokens = _tokens(source)
    static: list[str] = []
    dynamic: list[str] = []
    unsupported: list[str] = []
    count = len(tokens)
    for index, (kind, value) in enumerate(tokens):
        if kind != "id" or value not in ("import", "export"):
            continue
        if index and tokens[index - 1] == ("p", "."):
            continue  # a property named import/export, not the keyword
        following = tokens[index + 1] if index + 1 < count else ("", "")
        if value == "import" and following == ("p", "("):
            argument = tokens[index + 2] if index + 2 < count else ("", "")
            closing = tokens[index + 3] if index + 3 < count else ("", "")
            if argument[0] == "str" and closing == ("p", ")"):
                dynamic.append(argument[1])
            elif argument[0] == "escaped-str" and closing == ("p", ")"):
                unsupported.append("escaped import specifier is unsupported")
            else:
                unsupported.append("dynamic import requires one string literal")
            continue
        if value == "import" and following == ("p", "."):
            continue  # import.meta
        if value == "import" and following[0] in ("str", "escaped-str"):
            if following[0] == "str":
                static.append(following[1])
            else:
                unsupported.append("escaped import specifier is unsupported")
            continue
        # `import clause from "x"` and `export {..}`/`export * from "x"`: walk the
        # clause up to its `from`, giving up at anything a clause cannot contain.
        # Binding names may be strings (`{ "a-b" as c }`, `* as "ns"`) and may
        # be `from` itself (`{ from }`, `import from from "x"`).
        if value == "export" and following not in (("p", "{"), ("p", "*")):
            continue
        in_braces = False
        j = index + 1
        while j < count:
            k, v = tokens[j]
            following_kind = tokens[j + 1][0] if j + 1 < count else ""
            if k == "id" and v == "from" and not in_braces and following_kind in ("str", "escaped-str"):
                if following_kind == "str":
                    static.append(tokens[j + 1][1])
                else:
                    unsupported.append("escaped import specifier is unsupported")
                break
            if k in ("str", "escaped-str"):
                # A string here can only be a binding name.
                if not (in_braces or tokens[j - 1] == ("id", "as")):
                    break
            elif (k, v) == ("p", "{"):
                if in_braces:
                    break
                in_braces = True
            elif (k, v) == ("p", "}"):
                if not in_braces or tokens[j + 1:j + 2] != [("id", "from")]:
                    break
                in_braces = False
            elif k == "p" and v not in ",*":
                break
            elif k in ("num", "tpl", "re"):
                break
            j += 1
    return static, dynamic, unsupported


class ModuleScripts(HTMLParser):
    """Collects module script sources and inline module script bodies."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []
        self.inline: list[str] = []
        self._inline_module = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        values = dict(attrs)
        if values.get("type") != "module":
            return
        if values.get("src"):
            self.sources.append(values["src"] or "")
        else:
            self._inline_module = True
            self.inline.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inline_module = False

    def handle_data(self, data: str) -> None:
        if self._inline_module:
            self.inline[-1] += data


def resolve(root: Path, importer_dir: Path, specifier: str) -> Path | None:
    """Resolve a browser specifier to a payload path, or None for a bare one."""
    clean = specifier.split("?", 1)[0].split("#", 1)[0]
    if clean.startswith("/"):
        return (root / clean.lstrip("/")).resolve()
    if clean.startswith("./") or clean.startswith("../"):
        return (importer_dir / clean).resolve()
    return None


def missing_modules(root: Path) -> list[str]:
    """Every import in the page graph that does not resolve inside `root`."""
    root = root.resolve()
    problems: list[str] = []
    pending: list[tuple[Path, str, str, bool]] = []

    def queue_imports(importer_dir: Path, label: str, source: str) -> None:
        try:
            static, dynamic, unsupported = specifiers(source)
        except TokenizeError as error:
            problems.append(f"{label}: {error}")
            return
        problems.extend(f"{label}: {reason}" for reason in unsupported)
        pending.extend((importer_dir, f"{label} -> {spec}", spec, False) for spec in static)
        pending.extend((importer_dir, f"{label} (dynamic) -> {spec}", spec, True) for spec in dynamic)

    pages = sorted(root.rglob("*.html"))
    if not pages:
        return [f"{root}: no HTML page to start from"]
    for page in pages:
        parser = ModuleScripts()
        label = page.relative_to(root).as_posix()
        try:
            parser.feed(page.read_text(encoding="utf-8"))
        except UnicodeDecodeError as error:
            problems.append(f"{label}: not valid UTF-8 ({error.reason} at byte {error.start})")
            continue
        for src in parser.sources:
            pending.append((page.parent, f"{label} -> {src}", src, False))
        for body in parser.inline:
            queue_imports(page.parent, f"{label} (inline)", body)
    # Every shipped module is importable by a consumer of the payload, so the
    # graph starts at each of them too, not only at what the demo page reaches.
    for module in sorted(root.rglob("*.js")) + sorted(root.rglob("*.mjs")):
        pending.append((module.parent, f"(payload) -> ./{module.name}", f"./{module.name}", False))
    seen: set[Path] = set()
    while pending:
        importer_dir, edge, specifier, dynamic = pending.pop()
        target = resolve(root, importer_dir, specifier)
        if target is None:
            # A bare specifier only loads through an import map, which this
            # payload does not ship; only the listed host modules are exempt.
            if dynamic and specifier in ALLOWED_DYNAMIC_BARE_SPECIFIERS:
                continue
            problems.append(f"{edge}: bare specifier has no import map")
            continue
        if root not in target.parents:
            problems.append(f"{edge}: resolves outside the payload")
            continue
        if not target.is_file():
            problems.append(f"{edge}: missing {target.relative_to(root).as_posix()}")
            continue
        if target in seen or target.suffix not in (".js", ".mjs"):
            continue
        seen.add(target)
        label = target.relative_to(root).as_posix()
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            problems.append(f"{label}: not valid UTF-8 ({error.reason} at byte {error.start})")
            continue
        queue_imports(target.parent, label, text)
    return sorted(problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="the built web-dist directory")
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"web-dist directory not found: {args.root}")
    problems = missing_modules(args.root)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"web-dist import graph resolves: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
