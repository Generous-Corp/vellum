"""Find names the renamed mirror and native Vellum would both declare.

After the rename, mirrored `pulp::runtime` becomes `vellum::runtime`, which
native Vellum already uses, and every other mirrored namespace lands under the
same `vellum` root. This census lists what the two trees would then declare in
common:

- fully qualified types (class, struct, union, enum), aliases (`using X =`,
  `typedef`), namespace-scope variables, and functions and function templates.
  A function shared by qualified name is an overload set in common; with the
  same normalized signature it is a collision;
- macros `#define`d in headers (a macro defined only inside a source file is
  local to that translation unit);
- include guards;
- installed header paths (`<new>/<unit>/x.hpp` from each tree's
  `include/` directories).

It is lexical, not a compiler: comments, string and character literals are
blanked, preprocessor lines are read separately, and declarations are taken
at namespace scope by brace and parenthesis matching. Blind spots, stated so a
clean result is read for what it is:
- declarations a macro expands to are invisible;
- class members are not compared one by one: a class declared in both trees is
  already a collision;
- a qualified out-of-line definition (`void a::b::f()`) counts as a free
  function only when `a::b` is a namespace the tree opens; otherwise it is a
  member and is skipped;
- signatures are normalized textually (parameter names and default arguments
  dropped, whitespace collapsed), so `int` and `std::int32_t` compare
  different. A same-name overload set is reported for exactly this reason.
The full 8b build is the compiler's check of the same question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx", ".m", ".mm"})
HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp"})
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", "external", "third_party", "_deps"})
CLASS_KEYS = frozenset({"class", "struct", "union", "enum"})
TYPE_WORDS = frozenset({
    "void", "bool", "char", "short", "int", "long", "float", "double", "signed", "unsigned",
    "auto", "const", "volatile", "wchar_t", "char8_t", "char16_t", "char32_t", "size_t",
})
SKIP_STATEMENT_HEADS = frozenset({"using", "static_assert", "friend", "return", "template"})
TOKEN = re.compile(r"[A-Za-z_]\w*|::|->|\.\.\.|\d[\w.']*|\S")


@dataclass
class Declaration:
    kind: str  # type, type-forward, alias, variable, function
    name: str  # fully qualified
    signature: str
    path: str
    line: int


@dataclass
class TreeCensus:
    declarations: list[Declaration] = field(default_factory=list)
    namespaces: set[str] = field(default_factory=set)
    macros: dict[str, str] = field(default_factory=dict)  # name -> path
    guards: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # include path -> path
    files: int = 0


def blank_comments_and_literals(text: str) -> str:
    """Comments and literals become spaces (string literals the token STR); newlines stay."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c == "R" and text.startswith('R"', i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = re.match(r'R"([^(\s]{0,16})\(', text[i:])
            if not m:
                out.append(c)
                i += 1
                continue
            end = text.find(")" + m.group(1) + '"', i + m.end())
            j = n if end < 0 else end + len(m.group(1)) + 2
            out.append(' STR ' + re.sub(r"[^\n]", " ", text[i:j])[5:])
            i = j
        elif c in "\"'":
            if c == "'" and i > 0 and text[i - 1].isalnum():  # digit separator 1'000
                out.append(" ")
                i += 1
                continue
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append((" STR " if c == '"' else " ") + " " * max(0, j - i - 5))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def preprocessor_lines(text: str) -> list[tuple[int, str]]:
    """(line number, joined directive) for each preprocessor line."""
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            start = i
            joined = lines[i]
            while joined.endswith("\\") and i + 1 < len(lines):
                i += 1
                joined = joined[:-1] + " " + lines[i]
            result.append((start + 1, joined.strip()))
        i += 1
    return result


def strip_preprocessor(text: str) -> str:
    lines = text.split("\n")
    out = []
    continuing = False
    for line in lines:
        if continuing or line.lstrip().startswith("#"):
            continuing = line.endswith("\\")
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def tokens(text: str) -> list[tuple[str, int]]:
    result = []
    line = 1
    pos = 0
    for m in TOKEN.finditer(text):
        line += text.count("\n", pos, m.start())
        pos = m.start()
        result.append((m.group(0), line))
    return result


def strip_template_prefix(stmt: list[str]) -> list[str]:
    while stmt and stmt[0] == "template":
        if len(stmt) < 2 or stmt[1] != "<":
            return stmt[1:]
        depth, i = 0, 1
        while i < len(stmt):
            depth += {"<": 1, ">": -1}.get(stmt[i], 0)
            i += 1
            if depth == 0:
                break
        stmt = stmt[i:]
    return stmt


def split_params(inner: list[str]) -> list[list[str]]:
    params, current, depth = [], [], 0
    for tok in inner:
        if tok in "([{<":
            depth += 1
        elif tok in ")]}>":
            depth -= 1
        if tok == "," and depth == 0:
            params.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        params.append(current)
    return params


def normalize_param(param: list[str]) -> str:
    depth = 0
    for i, tok in enumerate(param):
        if tok in "([{<":
            depth += 1
        elif tok in ")]}>":
            depth -= 1
        elif tok == "=" and depth == 0:
            param = param[:i]
            break
    if len(param) >= 2 and re.fullmatch(r"[A-Za-z_]\w*", param[-1]) and param[-1] not in TYPE_WORDS \
            and param[-2] != "::":
        param = param[:-1]
    return " ".join(param)


def function_head(stmt: list[str]) -> tuple[str, str] | None:
    """(name as written, signature) if the statement declares a function."""
    depth = 0
    for i, tok in enumerate(stmt):
        if tok == "<":
            depth += 1
        elif tok == ">":
            depth = max(0, depth - 1)
        elif tok == "=" and depth == 0:
            return None  # an initializer, not a declaration
        elif tok == "(" and depth == 0:
            if i == 0:
                return None
            j = i - 1
            if re.fullmatch(r"[A-Za-z_~]\w*", stmt[j]):
                start = j
                if stmt[j] == "operator":
                    return None
            elif "operator" in stmt[max(0, j - 3):j]:
                start = j - 3 + stmt[max(0, j - 3):j].index("operator") if j >= 3 else stmt.index("operator")
            else:
                return None
            if start >= 1 and stmt[start - 1] == "~":
                start -= 1  # a destructor
            while start >= 2 and stmt[start - 1] == "::" and re.fullmatch(r"[A-Za-z_]\w*", stmt[start - 2]):
                start -= 2
            name = "".join(stmt[start:j + 1])
            if stmt[start] in CLASS_KEYS or name in TYPE_WORDS or stmt[start] in ("if", "while", "for", "switch", "sizeof", "decltype", "alignas", "noexcept", "requires"):
                return None
            # matching ')'
            pdepth, end = 0, i
            for end in range(i, len(stmt)):
                pdepth += {"(": 1, ")": -1}.get(stmt[end], 0)
                if pdepth == 0:
                    break
            if start == 0 and stmt[end + 1:end + 2] == ["->"] and "::" not in name:
                return None  # a deduction guide, `X(T) -> X<T>;`
            params = [normalize_param(p) for p in split_params(stmt[i + 1:end])]
            if params == ["void"]:
                params = []
            quals = [t for t in stmt[end + 1:] if t in ("const", "&", "&&", "volatile")]
            # The return type is not part of the signature: two functions that
            # differ only in it are still the same declaration, and clash.
            return name, f"{name}({', '.join(params)}){' ' + ' '.join(quals) if quals else ''}"
    return None


def class_head(stmt: list[str]) -> tuple[str, str] | None:
    """(kind, name) for `class X {`, `enum class X : T {`, `struct X;`."""
    for i, tok in enumerate(stmt):
        if tok in CLASS_KEYS:
            if i > 0 and stmt[i - 1] in ("enum",):
                continue
            j = i + 1
            if tok == "enum" and j < len(stmt) and stmt[j] in ("class", "struct"):
                j += 1
            names = []
            while j < len(stmt) and stmt[j] not in ("{", ";", ":", "<"):
                if stmt[j] == "[":
                    depth = 0
                    while j < len(stmt):
                        depth += {"[": 1, "]": -1}.get(stmt[j], 0)
                        j += 1
                        if depth == 0:
                            break
                    continue
                if stmt[j] in ("alignas", "__attribute__", "__declspec") and j + 1 < len(stmt) and stmt[j + 1] == "(":
                    depth = 0
                    j += 1
                    while j < len(stmt):
                        depth += {"(": 1, ")": -1}.get(stmt[j], 0)
                        j += 1
                        if depth == 0:
                            break
                    continue
                if re.fullmatch(r"[A-Za-z_]\w*", stmt[j]) and stmt[j] != "final":
                    names.append(stmt[j])
                elif stmt[j] == "(":
                    return None  # a function returning `struct X`, or similar
                j += 1
            if j < len(stmt) and stmt[j] == "<":
                return None  # a specialization
            if names:
                return ("enum" if tok == "enum" else "type"), names[-1]
            return None
    return None


class Scanner:
    def __init__(self, census: TreeCensus, root_rename: tuple[str, str] | None) -> None:
        self.census = census
        self.root_rename = root_rename
        self.pending_qualified: list[Declaration] = []

    def qualify(self, scope: list[str], name: str) -> str:
        parts = [*scope, *name.split("::")]
        if self.root_rename and parts and parts[0] == self.root_rename[0]:
            parts[0] = self.root_rename[1]
        return "::".join(parts)

    def record(self, kind: str, scope: list[str], name: str, signature: str, path: str, line: int) -> None:
        if not scope and "::" not in name:
            return  # global namespace: not under any mirrored or native root
        decl = Declaration(kind, self.qualify(scope, name), signature, path, line)
        if "::" in name and kind == "function":
            self.pending_qualified.append(decl)
        else:
            self.census.declarations.append(decl)

    def statement(self, stmt: list[str], scope: list[str], path: str, line: int, opens_brace: bool) -> None:
        stmt = [t for t in stmt if t != "STR"]
        stmt = strip_template_prefix(stmt)
        if not stmt or stmt[0] in SKIP_STATEMENT_HEADS - {"using"}:
            return
        if stmt[0] == "using":
            if len(stmt) >= 3 and stmt[1] != "namespace" and stmt[2] == "=":
                self.record("alias", scope, stmt[1], "", path, line)
            return
        if stmt[0] == "typedef":
            names = [t for t in stmt if re.fullmatch(r"[A-Za-z_]\w*", t)]
            if names:
                self.record("alias", scope, names[-1], "", path, line)
            return
        head = class_head(stmt)
        if head and (opens_brace or stmt[-1:] == [";"] or len(stmt) <= 4):
            kind, name = head
            self.record("type" if opens_brace else "type-forward", scope, name, "", path, line)
            return
        fn = function_head(stmt)
        if fn:
            self.record("function", scope, fn[0], fn[1], path, line)
            return
        if not opens_brace:
            depth = 0
            for i, tok in enumerate(stmt):
                depth += {"(": 1, ")": -1, "<": 1, ">": -1, "[": 1, "]": -1}.get(tok, 0)
                if depth == 0 and tok in ("=", "[", "{", ";") or i == len(stmt) - 1:
                    cut = stmt[:i] if tok in ("=", "[", "{", ";") else stmt
                    ids = [t for t in cut if re.fullmatch(r"[A-Za-z_]\w*", t)]
                    if len(ids) >= 2 and ids[-1] not in TYPE_WORDS:
                        self.record("variable", scope, ids[-1], "", path, line)
                    return

    def scan(self, text: str, path: str) -> None:
        clean = strip_preprocessor(blank_comments_and_literals(text))
        toks = tokens(clean)
        stack: list[tuple[str, list[str]]] = []  # (kind, names)
        stmt: list[str] = []
        stmt_line = 0
        paren = 0

        def scope() -> list[str] | None:
            names: list[str] = []
            for kind, ns in stack:
                if kind == "ns":
                    names.extend(ns)
                elif kind == "extern":
                    continue
                else:
                    return None  # inside a class, function or anonymous namespace
            return names

        i = 0
        while i < len(toks):
            tok, line = toks[i]
            current = scope()
            if current is None:
                if tok == "{":
                    stack.append(("block", []))
                elif tok == "}" and stack:
                    stack.pop()
                i += 1
                continue
            if not stmt:
                stmt_line = line
            if tok == "namespace" and (not stmt or stmt == ["inline"]):
                j = i + 1
                names = []
                while j < len(toks) and toks[j][0] not in ("{", "=", ";"):
                    if re.fullmatch(r"[A-Za-z_]\w*", toks[j][0]) and toks[j][0] != "inline":
                        names.append(toks[j][0])
                    j += 1
                if j < len(toks) and toks[j][0] == "{":
                    if names:
                        stack.append(("ns", names))
                        self.census.namespaces.add(self.qualify(current, "::".join(names)))
                    else:
                        stack.append(("anon", []))
                    stmt = []
                    i = j + 1
                    continue
                if j < len(toks) and toks[j][0] == "=" and names:
                    self.record("alias", current, names[0], "namespace", path, line)
                while j < len(toks) and toks[j][0] != ";":
                    j += 1
                stmt = []
                i = j + 1
                continue
            if tok == "extern" and i + 2 < len(toks) and toks[i + 1][0] == "STR" and toks[i + 2][0] == "{":
                stack.append(("extern", []))
                stmt = []
                i += 3
                continue
            if tok == "(":
                paren += 1
            elif tok == ")":
                paren = max(0, paren - 1)
            if tok == "{" and paren > 0:
                stack.append(("inner", []))  # a lambda or braced init inside parentheses
                stmt.append(tok)
                i += 1
                continue
            if tok == "{":
                self.statement(stmt, current, path, stmt_line, opens_brace=True)
                stack.append(("block", []))
                stmt = []
                paren = 0
                i += 1
                continue
            if tok == "}":
                if stack:
                    stack.pop()
                stmt = []
                paren = 0
                i += 1
                continue
            if tok == ";" and paren == 0:
                stmt.append(tok)
                self.statement(stmt, current, path, stmt_line, opens_brace=False)
                stmt = []
                i += 1
                continue
            stmt.append(tok)
            i += 1

    def finish(self) -> None:
        for decl in self.pending_qualified:
            owner = decl.name.rsplit("::", 1)[0]
            if owner in self.census.namespaces:
                self.census.declarations.append(decl)
        self.pending_qualified = []


def iter_sources(root: Path, skip_top: tuple[str, ...] = ()) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in skip_top:
            continue
        if any(part in SKIP_DIRS or part.startswith("build") for part in relative.parts[:-1]):
            continue
        files.append(path)
    return files


def census_tree(root: Path, files: list[Path], root_rename: tuple[str, str] | None,
                include_rename: tuple[str, str] | None, texts: dict[Path, str] | None = None) -> TreeCensus:
    census = TreeCensus()
    scanner = Scanner(census, root_rename)
    for path in files:
        relative = path.relative_to(root).as_posix()
        text = (texts or {}).get(path)
        if text is None:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
        census.files += 1
        scanner.scan(text, relative)
        is_header = path.suffix.lower() in HEADER_SUFFIXES
        directives = preprocessor_lines(text)
        for index, (_, directive) in enumerate(directives):
            m = re.match(r"#\s*define\s+([A-Za-z_]\w*)", directive)
            if m and is_header:
                guard = index > 0 and re.match(rf"#\s*(?:ifndef\s+{m.group(1)}|if\s+!\s*defined\s*\(?\s*{m.group(1)})\b",
                                                directives[index - 1][1])
                (census.guards if guard else census.macros).setdefault(m.group(1), relative)
        if is_header and "/include/" in "/" + relative:
            include_path = ("/" + relative).split("/include/", 1)[1]
            if include_rename:
                head, sep, rest = include_path.partition("/")
                if head == include_rename[0] and sep:
                    include_path = include_rename[1] + "/" + rest
            census.headers.setdefault(include_path, relative)
    scanner.finish()
    return census


def compare(mirror: TreeCensus, native: TreeCensus, root: str) -> dict:
    def by_name(census: TreeCensus) -> dict[str, list[Declaration]]:
        out: dict[str, list[Declaration]] = {}
        for decl in census.declarations:
            if decl.name == root or decl.name.startswith(root + "::"):
                out.setdefault(decl.name, []).append(decl)
        return out

    m, n = by_name(mirror), by_name(native)
    errors, overloads = [], []
    for name in sorted(set(m) & set(n)):
        mk = {d.kind for d in m[name]}
        nk = {d.kind for d in n[name]}
        site = {"name": name,
                "mirror": sorted({f"{d.path}:{d.line}" for d in m[name]})[:3],
                "native": sorted({f"{d.path}:{d.line}" for d in n[name]})[:3]}
        if mk == {"function"} and nk == {"function"}:
            same = sorted({d.signature for d in m[name]} & {d.signature for d in n[name]})
            if same:
                errors.append({**site, "kind": "same-signature", "signatures": same})
            else:
                overloads.append({**site, "kind": "shared-overload-set"})
        else:
            errors.append({**site, "kind": "+".join(sorted(mk)) + " vs " + "+".join(sorted(nk))})
    shared_ns = sorted(ns for ns in mirror.namespaces & native.namespaces if ns == root or ns.startswith(root + "::"))
    return {
        "declarations": {"mirror": sum(len(v) for v in m.values()), "native": sum(len(v) for v in n.values())},
        "files": {"mirror": mirror.files, "native": native.files},
        "shared_namespaces": shared_ns,
        "name_collisions": errors,
        "shared_overload_sets": overloads,
        "macro_collisions": [{"name": k, "mirror": mirror.macros[k], "native": native.macros[k]}
                             for k in sorted(set(mirror.macros) & set(native.macros))],
        "include_guard_collisions": [{"name": k, "mirror": mirror.guards[k], "native": native.guards[k]}
                                     for k in sorted(set(mirror.guards) & set(native.guards))],
        "header_path_collisions": [{"include": k, "mirror": mirror.headers[k], "native": native.headers[k]}
                                   for k in sorted(set(mirror.headers) & set(native.headers))],
    }
