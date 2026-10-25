#!/usr/bin/env python3
"""The per-source compile options Pulp's CMake sets on exported sources.

A closure records each mirrored target's sources, include directories and
definitions, but not the options Pulp sets on a single source with
`set_source_files_properties(<files> PROPERTIES COMPILE_OPTIONS <options>)`.
Pulp compiles core/canvas/src/system_ui_font_manager.cpp with -fno-rtti, for
example, because it subclasses SkFontMgr and the prebuilt Skia exports no type
info for it. A reconstruction that drops the option builds a different object
and fails to link.

This reads those calls from the CMake files themselves, so no list of files is
kept by hand. It evaluates each call's enclosing if()/elseif()/else() chain
against the configure the closure recorded (its Pulp options, the Darwin
system, a single-config generator, AppleClang and no extra CMAKE_<LANG>_FLAGS)
with three-valued logic: a branch that cannot be decided is "unknown". An
option under an unknown branch, an option whose file cannot be resolved, a
call inside a function or macro body, and any other per-source property
(COMPILE_DEFINITIONS, COMPILE_FLAGS, INCLUDE_DIRECTORIES) set on an exported
source all fail closed instead of being guessed.

The mirror package plan runs this over the landed mirror's CMake files; a
provenance test runs it over every CMake file of Pulp at the receipt's commit
and requires the same result, so an option set from a Pulp file the export
does not carry is caught too.
"""

from __future__ import annotations

import posixpath
import re
import warnings
from dataclasses import dataclass
from typing import Iterable

SOURCE_ROOT = "@src"
OPTION_PROPERTIES = {"COMPILE_OPTIONS"}
# Per-source properties that would change an exported object if set; none is
# reconstructed, so setting one on an exported source fails closed.
REFUSED_PROPERTIES = {"COMPILE_DEFINITIONS", "COMPILE_FLAGS", "INCLUDE_DIRECTORIES"}
FALSE_CONSTANTS = {"", "0", "OFF", "NO", "FALSE", "N", "IGNORE", "NOTFOUND"}
TRUE_CONSTANTS = {"1", "ON", "YES", "TRUE", "Y"}
BINARY_OPS = {"STREQUAL", "MATCHES", "EQUAL", "LESS", "GREATER", "STRLESS", "STRGREATER",
              "VERSION_LESS", "VERSION_GREATER", "VERSION_EQUAL", "VERSION_LESS_EQUAL",
              "VERSION_GREATER_EQUAL", "LESS_EQUAL", "GREATER_EQUAL", "IN_LIST", "PATH_EQUAL"}
UNARY_OPS = {"DEFINED", "TARGET", "EXISTS", "COMMAND", "POLICY", "IS_DIRECTORY",
             "IS_ABSOLUTE", "IS_SYMLINK", "TEST"}
UNKNOWN = object()


class SourceOptionsError(RuntimeError):
    pass


@dataclass
class Command:
    name: str
    args: list[tuple[str, bool]]  # (text, quoted)
    path: str
    line: int


def commands(text: str, path: str = "<cmake>") -> list[Command]:
    """CMake commands with their raw arguments, comments removed."""
    out: list[Command] = []
    i, n, line = 0, len(text), 1
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch.isspace():
            i += 1
        elif ch == "#":
            bracket = re.match(r"#\[(=*)\[", text[i:])
            if bracket:
                end = text.find("]" + bracket.group(1) + "]", i)
                end = n if end < 0 else end + len(bracket.group(1)) + 2
            else:
                end = text.find("\n", i)
                end = n if end < 0 else end
            line += text.count("\n", i, end)
            i = end
        else:
            match = ident.match(text, i)
            if not match:
                i += 1
                continue
            name, start_line = match.group(0), line
            i = match.end()
            while i < n and text[i] in " \t":
                i += 1
            if i >= n or text[i] != "(":
                continue
            i += 1
            args: list[tuple[str, bool]] = []
            depth, token = 0, None
            while i < n:
                ch = text[i]
                if ch == '"':
                    j, value = i + 1, []
                    while j < n and text[j] != '"':
                        if text[j] == "\\" and j + 1 < n:
                            value.append(text[j:j + 2])
                            j += 2
                            continue
                        value.append(text[j])
                        j += 1
                    literal = "".join(value)
                    line += literal.count("\n")
                    args.append((literal.replace('\\"', '"'), True))
                    i = j + 1
                elif ch == "#":
                    end = text.find("\n", i)
                    i = n if end < 0 else end
                elif ch == "(":
                    depth += 1
                    args.append(("(", False))
                    i += 1
                elif ch == ")":
                    if depth == 0:
                        i += 1
                        break
                    depth -= 1
                    args.append((")", False))
                    i += 1
                elif ch.isspace():
                    if ch == "\n":
                        line += 1
                    i += 1
                else:
                    j = i
                    while j < n and not text[j].isspace() and text[j] not in '()#"':
                        j += 1
                    args.append((text[i:j], False))
                    i = j
            out.append(Command(name.lower(), args, path, start_line))
    return out


def expand(text: str, env: dict[str, object]) -> object:
    """Replace ${VAR} references; UNKNOWN when one is not known."""
    unknown = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal unknown
        value = env.get(match.group(1), UNKNOWN)
        if value is UNKNOWN:
            unknown = True
            return ""
        return str(value)

    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\$\{([A-Za-z0-9_.+-]+)\}", substitute, text)
    return UNKNOWN if unknown else text


def truthy(value: object) -> object:
    if value is UNKNOWN:
        return UNKNOWN
    text = str(value)
    upper = text.upper()
    if upper in TRUE_CONSTANTS:
        return True
    if upper in FALSE_CONSTANTS or upper.endswith("-NOTFOUND"):
        return False
    return True


def kleene_and(a: object, b: object) -> object:
    if a is False or b is False:
        return False
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return True


def kleene_or(a: object, b: object) -> object:
    if a is True or b is True:
        return True
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return False


def kleene_not(a: object) -> object:
    return UNKNOWN if a is UNKNOWN else not a


class Condition:
    """Recursive-descent evaluation of one if() argument list."""

    def __init__(self, args: list[tuple[str, bool]], env: dict[str, object]) -> None:
        self.args = [(expand(text, env), quoted) for text, quoted in args]
        self.env = env
        self.pos = 0

    def evaluate(self) -> object:
        if any(text is UNKNOWN for text, _ in self.args):
            return UNKNOWN
        try:
            value = self.or_expr()
        except IndexError:
            return UNKNOWN
        return value if self.pos == len(self.args) else UNKNOWN

    def peek(self) -> tuple[str, bool] | None:
        return self.args[self.pos] if self.pos < len(self.args) else None

    def take(self) -> tuple[str, bool]:
        token = self.args[self.pos]
        self.pos += 1
        return token

    def keyword(self, word: str) -> bool:
        token = self.peek()
        return token is not None and not token[1] and token[0] == word

    def or_expr(self) -> object:
        value = self.and_expr()
        while self.keyword("OR"):
            self.take()
            value = kleene_or(value, self.and_expr())
        return value

    def and_expr(self) -> object:
        value = self.not_expr()
        while self.keyword("AND"):
            self.take()
            value = kleene_and(value, self.not_expr())
        return value

    def not_expr(self) -> object:
        if self.keyword("NOT"):
            self.take()
            return kleene_not(self.not_expr())
        return self.primary()

    def operand(self, token: tuple[str, bool]) -> object:
        text, quoted = token
        if quoted:
            return text
        return self.env.get(text, text) if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else text

    def primary(self) -> object:
        if self.keyword("("):
            self.take()
            value = self.or_expr()
            if not self.keyword(")"):
                return UNKNOWN
            self.take()
            return value
        token = self.take()
        if not token[1] and token[0] in UNARY_OPS:
            subject = self.take()
            if token[0] == "DEFINED":
                name = subject[0]
                if name.startswith("ENV{") or name.startswith("CACHE{"):
                    return UNKNOWN
                value = self.env.get(name, UNKNOWN)
                return UNKNOWN if value is UNKNOWN else True
            return UNKNOWN
        following = self.peek()
        if following is not None and not following[1] and following[0] in BINARY_OPS:
            op = self.take()[0]
            right = self.take()
            left_value, right_value = self.operand(token), self.operand(right)
            if left_value is UNKNOWN or right_value is UNKNOWN:
                return UNKNOWN
            if op == "STREQUAL":
                return str(left_value) == str(right_value)
            if op == "MATCHES":
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FutureWarning)
                        return re.search(str(right_value), str(left_value)) is not None
                except re.error:
                    return UNKNOWN
            return UNKNOWN
        text, quoted = token
        if quoted:
            return truthy(text)
        if text.upper() in TRUE_CONSTANTS or text.upper() in FALSE_CONSTANTS:
            return truthy(text)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
            return truthy(self.env.get(text, UNKNOWN))
        return truthy(text)


def configure_environment(options: dict[str, str]) -> dict[str, object]:
    """The variables a closure's configure fixes, plus the Darwin toolchain facts."""
    if options.get("CMAKE_SYSTEM_NAME") != "Darwin":
        raise SourceOptionsError(f"only a Darwin configure is evaluated, not {options.get('CMAKE_SYSTEM_NAME')!r}")
    env: dict[str, object] = {name: value for name, value in options.items()}
    env.update({
        "APPLE": "1", "UNIX": "1", "WIN32": "", "MSVC": "", "ANDROID": "", "IOS": "",
        "EMSCRIPTEN": "", "LINUX": "", "CMAKE_HOST_APPLE": "1",
        "CMAKE_CXX_COMPILER_ID": "AppleClang", "CMAKE_C_COMPILER_ID": "AppleClang",
        "CMAKE_OBJCXX_COMPILER_ID": "AppleClang",
        "CMAKE_C_FLAGS": "", "CMAKE_CXX_FLAGS": "",
        "CMAKE_SOURCE_DIR": SOURCE_ROOT, "PROJECT_SOURCE_DIR": SOURCE_ROOT,
        "CMAKE_HOME_DIRECTORY": SOURCE_ROOT, "PULP_ROOT_DIR": SOURCE_ROOT,
    })
    return env


def source_directory(path: str, cmake_files: set[str]) -> str:
    """The CMAKE_CURRENT_SOURCE_DIR a file's top-level code runs with: its own
    directory for a CMakeLists.txt, else the nearest enclosing directory that
    has one (an included module runs in its includer's directory)."""
    directory = posixpath.dirname(path)
    if posixpath.basename(path) == "CMakeLists.txt":
        return directory
    while True:
        candidate = posixpath.join(directory, "CMakeLists.txt") if directory else "CMakeLists.txt"
        if candidate in cmake_files or not directory:
            return directory
        directory = posixpath.dirname(directory)


def resolve(file: str, base: str, env: dict[str, object]) -> object:
    expanded = expand(file, env)
    if expanded is UNKNOWN:
        return UNKNOWN
    text = str(expanded)
    if text.startswith(SOURCE_ROOT + "/"):
        relative = text[len(SOURCE_ROOT) + 1:]
    elif text.startswith("/"):
        return UNKNOWN
    else:
        relative = posixpath.join(base, text) if base else text
    normalized = posixpath.normpath(relative)
    return UNKNOWN if normalized.startswith("..") else normalized


def per_source_properties(cmd: Command) -> tuple[list[tuple[str, bool]], list[tuple[str, list[str]]]] | None:
    """(files, [(property, values)]) of a per-source property call, or None."""
    words = [text for text, _ in cmd.args]
    if cmd.name == "set_source_files_properties":
        if "PROPERTIES" not in words:
            return None
        split = words.index("PROPERTIES")
        files = [arg for arg in cmd.args[:split] if arg[0] not in {"DIRECTORY", "TARGET_DIRECTORY"}]
        pairs = cmd.args[split + 1:]
        props = [(pairs[k][0], [pairs[k + 1][0]]) for k in range(0, len(pairs) - 1, 2)]
        return files, props
    if cmd.name == "set_property" and words[:1] == ["SOURCE"]:
        if "PROPERTY" not in words:
            return None
        split = words.index("PROPERTY")
        files = [arg for arg in cmd.args[1:split] if arg[0] not in {"DIRECTORY", "TARGET_DIRECTORY", "APPEND", "APPEND_STRING"}]
        rest = [text for text, _ in cmd.args[split + 1:]]
        return files, [(rest[0], rest[1:])] if rest else []
    return None


def evaluate(files: dict[str, str], options: dict[str, str], exported: Iterable[str]) -> dict[str, list[str]]:
    """Pulp-relative exported source -> the COMPILE_OPTIONS Pulp's CMake applies to it."""
    exported = set(exported)
    cmake_files = set(files)
    result: dict[str, list[str]] = {}
    errors: list[str] = []
    for path in sorted(files):
        env = configure_environment(options)
        base = source_directory(path, cmake_files)
        env["CMAKE_CURRENT_SOURCE_DIR"] = f"{SOURCE_ROOT}/{base}" if base else SOURCE_ROOT
        env["CMAKE_CURRENT_LIST_DIR"] = f"{SOURCE_ROOT}/{posixpath.dirname(path)}" if posixpath.dirname(path) else SOURCE_ROOT
        # Each frame: [active, decided]; active is True/False/UNKNOWN,
        # decided says an earlier branch of the chain was (or may have been) taken.
        frames: list[list[object]] = []
        definition_depth = 0

        def effective() -> object:
            value: object = True
            for active, _ in frames:
                value = kleene_and(value, active)
            return UNKNOWN if definition_depth and value is not False else value

        for cmd in commands(files[path], path):
            if cmd.name in {"function", "macro"}:
                definition_depth += 1
                continue
            if cmd.name in {"endfunction", "endmacro"}:
                definition_depth = max(0, definition_depth - 1)
                continue
            if cmd.name == "if":
                frames.append([Condition(cmd.args, env).evaluate(), None])
                frames[-1][1] = frames[-1][0]
                continue
            if cmd.name in {"elseif", "else"} and frames:
                decided = frames[-1][1]
                value = True if cmd.name == "else" else Condition(cmd.args, env).evaluate()
                frames[-1][0] = kleene_and(kleene_not(decided), value)
                frames[-1][1] = kleene_or(decided, value)
                continue
            if cmd.name == "endif":
                if frames:
                    frames.pop()
                continue
            state = effective()
            if state is False:
                continue
            words = [text for text, _ in cmd.args]
            if cmd.name == "set" and words:
                name = expand(words[0], env)
                if name is UNKNOWN:
                    continue
                values = [w for w in words[1:] if w != "PARENT_SCOPE"]
                if "CACHE" in values:
                    values = values[:values.index("CACHE")]
                    if name in env:
                        continue
                joined = expand(";".join(values), env)
                env[str(name)] = joined if state is True else UNKNOWN
                continue
            if cmd.name == "unset" and words:
                env.pop(words[0], None) if state is True else env.__setitem__(words[0], UNKNOWN)
                continue
            if cmd.name == "option" and words:
                if words[0] not in env:
                    default = words[2] if len(words) > 2 else "OFF"
                    env[words[0]] = default if state is True else UNKNOWN
                continue
            if cmd.name == "get_property" and words:
                generator = words[1:] == ["GLOBAL", "PROPERTY", "GENERATOR_IS_MULTI_CONFIG"]
                env[words[0]] = "0" if generator and state is True else UNKNOWN
                continue
            parsed = per_source_properties(cmd)
            if parsed is None:
                continue
            sources, props = parsed
            where = f"{path}:{cmd.line}"
            relevant = [(prop, values) for prop, values in props
                        if prop in OPTION_PROPERTIES or prop in REFUSED_PROPERTIES]
            if not relevant:
                continue
            for raw, _ in sources:
                resolved = resolve(raw, base, env)
                if resolved is not UNKNOWN and resolved not in exported:
                    continue
                for prop, values in relevant:
                    if resolved is UNKNOWN:
                        if prop in OPTION_PROPERTIES:
                            errors.append(f"{where}: {prop} on a file that cannot be resolved: {raw}")
                        continue
                    if prop in REFUSED_PROPERTIES:
                        errors.append(f"{where}: {prop} on exported source {resolved} is not reconstructed")
                        continue
                    if state is UNKNOWN:
                        errors.append(f"{where}: cannot decide whether {prop} applies to {resolved}")
                        continue
                    expanded = [expand(v, env) for v in values]
                    if any(v is UNKNOWN for v in expanded):
                        errors.append(f"{where}: {prop} for {resolved} names an unknown variable")
                        continue
                    flat = [part for v in expanded for part in str(v).split(";") if part]
                    if resolved in result and result[resolved] != flat:
                        errors.append(f"{where}: {resolved} is given {prop} twice")
                        continue
                    result[resolved] = flat
    if errors:
        raise SourceOptionsError("; ".join(errors))
    return result
