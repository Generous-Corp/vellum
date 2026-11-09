#!/usr/bin/env python3
"""Prove a renamed mirror is a pure rename of the raw Pulp export, without the rename tool.

This is the primary identity guard (DECISIONS 2026-11-05). It does not import
or run rename_mirror_namespace.py, so it holds whatever that tool does:

1. The receipt's rename table (from, to, include units, Objective-C classes,
   class prefix, literal exclusions) hashes to its recorded `table_sha256`.
2. The raw export contains none of the target spellings: `<to>::`,
   `namespace <to>`, `<to>/`, `include/<to>`, `<To>Mirror`.
3. Discovery over the raw export yields the receipt's units and classes: a unit
   is a directory `include/<from>/<unit>/` holding a file; a class is
   `@interface` / `@implementation <From><Upper>...` in a header or
   Objective-C source outside `external/`.
4. The mirror holds exactly `renamed_path(p)` for every raw path `p`
   (`include/<from>/` -> `include/<to>/`), and nothing else.
5. For every file, inverse(mirror bytes) == raw bytes. The inverse map is
   context-free (`<To>Mirror` -> `<From>`, `namespace\\s+<to>` -> `namespace <from>`,
   `<to>::` -> `<from>::`, `<to>/` -> `<from>/`), which is exact because of check 2.
6. Every excluded literal occurs as often in the mirror file as in its raw
   file (an excluded literal that was renamed would otherwise invert cleanly).
7. Completeness, boundary-free over all bytes of the mirror:
   - `<from>::` occurs only inside the receipt's excluded literals;
   - there is no `namespace <from>`, no `[<"]<from>/<unit>/` and no `include/<from>/`;
   - no declared class name remains as a whole word.

With `--secondary`, it also runs the receipt's recorded tool blob (`git cat-file`
from `--vellum-repo`, never the checkout's HEAD) under the recorded Python
minor version, on a scratch copy of the raw export, and requires the result to
equal the mirror byte for byte with the recorded rule digest.

Exit: 0 pass, 1 fail, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TABLE_KEYS = ("from", "to", "include_units", "objc_classes", "objc_class_prefix", "literal_exclusions", "edge_table")
OBJC_SOURCE_SUFFIXES = (".h", ".hpp", ".m", ".mm")
# Pinned here, not taken from the receipt: a receipt cannot choose its own spellings.
PINNED = {"from": "pulp", "to": "vellum", "objc_class_prefix": "VellumMirror"}
REGULAR_MODES = ("100644", "100755")
EXCLUDED_TOP_DIRS = ("external",)
HELPER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.py\Z")
# The escape-aware word start the forward rules use (ASCII, as bytes).
BOUND = rb"(?:(?<![A-Za-z0-9_$])|(?<=\\[abfnrtv0]))"


def table_sha256(rename: dict) -> str:
    table = {key: rename[key] for key in TABLE_KEYS}
    return hashlib.sha256(json.dumps(table, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capitalized(name: str) -> str:
    return name[:1].upper() + name[1:]


class DiskFile:
    """A file in a directory tree, with a git-style mode so symlinks and executables are visible."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if path.is_symlink():
            self.mode = "120000"
        elif path.is_file():
            self.mode = "100755" if os.access(path, os.X_OK) else "100644"
        else:
            self.mode = "other"

    def read_bytes(self) -> bytes:
        return os.readlink(self.path).encode() if self.mode == "120000" else self.path.read_bytes()


def files_under(root: Path) -> dict[str, DiskFile]:
    """Every entry under root, symlinks included (never followed) so they can be refused."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames) + sorted(d for d in dirnames if (Path(dirpath) / d).is_symlink()):
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = DiskFile(path)
    return out


class GitBlob:
    """A file read from a git object, so a checkout's autocrlf or smudge filters never touch the bytes."""

    def __init__(self, repo: Path, sha: str, mode: str = "100644") -> None:
        self.repo, self.sha, self.mode = repo, sha, mode

    def read_bytes(self) -> bytes:
        return subprocess.run(["git", "-C", str(self.repo), "cat-file", "blob", self.sha],
                              check=True, capture_output=True).stdout


def git_tree(repo: Path, treeish: str) -> dict[str, GitBlob]:
    """Every entry under `treeish` (a commit, `commit:subdir` or a tree id), with its mode.

    Gitlinks and symlinks are kept, so the caller can refuse them.
    """
    out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", treeish],
                         check=True, capture_output=True).stdout
    files = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, sha = meta.split()
        files[path.decode()] = GitBlob(repo, sha.decode(), mode.decode())
    return files


SAFE_PATH = re.compile(r"(?:[A-Za-z0-9_.+@-]+/)*[A-Za-z0-9_.+@-]+\Z")
QUOTED_LITERAL = re.compile(r'(?:@|u8|u|U|L)?(?:"(?:\\.|[^"\\\n])*"|R"([^(\s\\]{0,16})\((?:.|\n)*?\)\1")\Z')


def safe_relative(path: str) -> bool:
    return bool(SAFE_PATH.match(path)) and not any(part in ("", ".", "..") for part in path.split("/"))


def raw_from_records(pulp_repo: Path, records: list[dict], pulp_sha: str,
                     main_ref: str = "origin/main") -> tuple[dict[str, GitBlob], list[str]]:
    """The raw export, rebuilt from authoritative Pulp, the only source CI may use.

    Every record must name the export receipt's `pulp_sha`, which must be an
    ancestor of Pulp's main; carry its `pulp_git_blob_sha`; and name a safe
    relative path that resolves, with pathspec magic off, to exactly one blob
    at that commit, the recorded one. Never a tree a pull request supplies.
    """
    files, errors = {}, []
    if not re.fullmatch(r"[0-9a-f]{40}", pulp_sha or ""):
        return {}, [f"export receipt pulp_sha {pulp_sha!r} is not a full commit id"]
    ancestor = subprocess.run(["git", "-C", str(pulp_repo), "merge-base", "--is-ancestor", pulp_sha, main_ref],
                              capture_output=True)
    if ancestor.returncode != 0:
        errors.append(f"pulp_sha {pulp_sha} is not an ancestor of Pulp {main_ref}")
    for record in records:
        path, commit, want = record.get("pulp_path", ""), record.get("pulp_commit", ""), record.get("pulp_git_blob_sha")
        if commit != pulp_sha:
            errors.append(f"record {path}: pulp_commit {commit} is not the receipt's pulp_sha {pulp_sha}")
            continue
        if not want:
            errors.append(f"record {path}: pulp_git_blob_sha is missing")
            continue
        if not safe_relative(path):
            errors.append(f"record path {path!r} is not a safe relative path")
            continue
        out = subprocess.run(["git", "-C", str(pulp_repo), "--literal-pathspecs", "ls-tree", "-z", commit, "--", path],
                             capture_output=True).stdout
        entries = [e for e in out.split(b"\0") if e]
        if len(entries) != 1:
            errors.append(f"{commit}:{path} resolves to {len(entries)} entries, not one blob")
            continue
        meta, found = entries[0].split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        if kind != "blob" or found.decode() != path:
            errors.append(f"{commit}:{path} is a {kind} at {found.decode()!r}, not the blob named")
            continue
        if want != sha:
            errors.append(f"{commit}:{path} is blob {sha}, the record says {want}")
        if path in files:
            errors.append(f"two records name {path}")
        files[path] = GitBlob(pulp_repo, sha, mode)
    return files, errors


def as_files(tree) -> dict:
    return tree if isinstance(tree, dict) else files_under(tree)


def renamed_path(relative: str, frm: str, to: str) -> str:
    parts = relative.split("/")
    for i in range(len(parts) - 1):
        if parts[i] == "include" and parts[i + 1] == frm:
            parts[i + 1] = to
    return "/".join(parts)


def target_spellings(to: str, class_prefix: str) -> dict[str, re.Pattern[bytes]]:
    t = re.escape(to.encode())
    return {
        f"{to}::": re.compile(t + rb"::"),
        f"namespace {to}": re.compile(rb"namespace\s+" + t + rb"(?![A-Za-z0-9_])"),
        f"{to}/": re.compile(t + rb"/"),
        f"include/{to}": re.compile(rb"include/" + t),
        class_prefix: re.compile(re.escape(class_prefix.encode())),
    }


def inverse_bytes(data: bytes, frm: str, to: str, class_prefix: str) -> bytes:
    f, t = frm.encode(), to.encode()
    data = data.replace(class_prefix.encode(), capitalized(frm).encode())
    data = re.sub(rb"namespace(\s+)" + re.escape(t) + rb"(?![A-Za-z0-9_])", rb"namespace\1" + f, data)
    data = data.replace(t + b"::", f + b"::")
    data = data.replace(t + b"/", f + b"/")
    data = data.replace(b"include/" + t, b"include/" + f)
    return data


def is_text(relative: str, data: bytes, suffixes: set[str], binary: set[str]) -> bool:
    """Whether the forward rename rewrites this file: the tool's own classification, restated."""
    if relative.split("/")[0] in EXCLUDED_TOP_DIRS or b"\0" in data:
        return False
    suffix = os.path.splitext(relative.rsplit("/", 1)[-1])[1].lower()
    if suffix in binary or suffix not in suffixes:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def forward_bytes(data: bytes, rename: dict, protected: list[bytes]) -> tuple[bytes, dict[str, int]]:
    """An independent implementation of the forward rename, as bytes.

    It shares no code with rename_mirror_namespace.py. The mirror must equal it
    byte for byte, so a mirror carrying any swap the rules do not make
    (`@pulp/react` -> `@vellum/react`, `PulpChart` -> `VellumMirrorChart`, a URL
    path) fails even when it inverts cleanly.
    """
    f, t = rename["from"].encode(), rename["to"].encode()
    placeholders = []
    for i, literal in enumerate(protected):
        token = b"\0" + str(i).encode() + b"\0"
        placeholders.append((token, literal))
        data = data.replace(literal, token)
    counts = {}
    rules = [
        ("namespace", re.compile(BOUND + rb"namespace(\s+)" + re.escape(f) + rb"\b"), rb"namespace\1" + t),
        ("qualified", re.compile(BOUND + re.escape(f) + rb"::"), t + b"::"),
    ]
    units = rename["include_units"]
    if units:
        alt = b"|".join(re.escape(u.encode()) for u in units)
        rules.append(("include-path", re.compile(rb"(?:(?<=[<\"'/(\s])|(?<=\\[nt])|^)" + re.escape(f)
                                                 + rb"/(?=(?:" + alt + rb")(?:/|\.h|(?![A-Za-z0-9_.-])))", re.M),
                      t + b"/"))
    rules.append(("include-dir", re.compile(rb"(?<=include/)" + re.escape(f) + rb"(?![A-Za-z0-9_.-])(?!/)"), t))
    classes = rename["objc_classes"]
    old_class = capitalized(rename["from"]).encode()
    if classes:
        alt = b"|".join(re.escape(c.encode()[len(old_class):]) for c in classes)
        rules.append(("objc-class", re.compile(BOUND + re.escape(old_class) + rb"(" + alt + rb")\b"),
                      rename["objc_class_prefix"].encode() + rb"\1"))
    for name, pattern, replacement in rules:
        data, n = pattern.subn(replacement, data)
        counts[name] = n
    for token, literal in placeholders:
        data = data.replace(token, literal)
    return data, counts


def discover(raw: dict, frm: str) -> tuple[list[str], list[str]]:
    units = set()
    for relative in raw:
        parts = relative.split("/")
        for i in range(len(parts) - 3):
            if parts[i] == "include" and parts[i + 1] == frm:
                units.add(parts[i + 2])
    declaration = re.compile(rb"@(?:interface|implementation)\s+(" + re.escape(capitalized(frm).encode())
                             + rb"[A-Z][A-Za-z0-9_]*)")
    classes = set()
    for relative, path in raw.items():
        if relative.split("/")[0] == "external" or not relative.lower().endswith(OBJC_SOURCE_SUFFIXES):
            continue
        classes.update(m.decode() for m in declaration.findall(path.read_bytes()))
    return sorted(units), sorted(classes)


def verify(raw_root, mirror_root, rename: dict) -> list[str]:
    """Each root is a directory or a {path: blob} map from git_tree()."""
    errors: list[str] = []
    missing = [k for k in (*TABLE_KEYS, "table_sha256") if k not in rename]
    if missing:
        return [f"receipt rename block lacks {missing}"]
    if table_sha256(rename) != rename["table_sha256"]:
        errors.append("the rename table does not hash to its recorded table_sha256")
    for key, value in PINNED.items():
        if rename[key] != value:
            errors.append(f"receipt {key} is {rename[key]!r}; this verifier is pinned to {value!r}")
    frm, to, prefix = rename["from"], rename["to"], rename["objc_class_prefix"]
    raw = as_files(raw_root)
    mirror = as_files(mirror_root)

    # Only regular files may cross: no symlink, gitlink or other entry on either side.
    for side, tree in (("raw export", raw), ("mirror", mirror)):
        for relative, entry in tree.items():
            if entry.mode not in REGULAR_MODES:
                errors.append(f"{side} {relative} is not a regular file (mode {entry.mode})")

    spellings = target_spellings(to, prefix)
    for relative, path in raw.items():
        data = path.read_bytes()
        for name, pattern in spellings.items():
            if pattern.search(data):
                errors.append(f"raw export {relative} already contains the target spelling {name!r}")
            if pattern.search(relative.encode()) or relative.split("/")[-1] == to:
                errors.append(f"raw export path {relative} already contains the target spelling {name!r}")

    units, classes = discover(raw, frm)
    if units != rename["include_units"]:
        errors.append(f"discovered units {units} differ from the receipt's {rename['include_units']}")
    if classes != rename["objc_classes"]:
        errors.append(f"discovered classes {classes} differ from the receipt's {rename['objc_classes']}")

    expected: dict[str, str] = {}
    for r in raw:
        target = renamed_path(r, frm, to)
        if target in expected:
            errors.append(f"raw {expected[target]} and {r} both rename to {target}")
        expected[target] = r
    for extra in sorted(set(mirror) - set(expected)):
        errors.append(f"mirror has {extra}, which no raw path renames to")
    for absent in sorted(set(expected) - set(mirror)):
        errors.append(f"mirror lacks {absent} (from raw {expected[absent]})")

    exclusions = rename["literal_exclusions"]
    seen_rows = set()
    for row in exclusions:
        if not QUOTED_LITERAL.match(row["literal"]):
            errors.append(f"literal exclusion {row['path']} {row['literal']!r} is not a quoted string literal")
        key = (row["path"], row["literal"])
        if key in seen_rows:
            errors.append(f"duplicate literal exclusion {row['path']} {row['literal']}")
        seen_rows.add(key)
    protected_by_path: dict[str, list[bytes]] = {}
    for path_, literal in sorted(seen_rows):
        protected_by_path.setdefault(path_, []).append(literal.encode())
    edge = rename.get("edge_table", {})
    suffixes = {x.lower() for x in edge.get("suffixes", [])}
    binary = {x.lower() for x in edge.get("binary_suffixes", [])}

    for target in sorted(set(expected) & set(mirror)):
        source = expected[target]
        raw_bytes, mirror_bytes = raw[source].read_bytes(), mirror[target].read_bytes()
        if raw[source].mode != mirror[target].mode:
            errors.append(f"{target} has mode {mirror[target].mode}; raw {source} has {raw[source].mode}")
        if inverse_bytes(mirror_bytes, frm, to, prefix) != raw_bytes:
            errors.append(f"inverse({target}) != raw {source}: not a pure rename")
        if is_text(source, raw_bytes, suffixes, binary):
            want, _ = forward_bytes(raw_bytes, rename, protected_by_path.get(source, []))
        else:
            want = raw_bytes
        if mirror_bytes != want:
            have_counts = {n: len(p.findall(mirror_bytes)) for n, p in spellings.items()}
            want_counts = {n: len(p.findall(want)) for n, p in spellings.items()}
            errors.append(f"{target} differs from the forward rename of raw {source}: target spellings "
                          f"{have_counts} where the rules make {want_counts}")

    # An excluded literal must survive unchanged: as many occurrences in the
    # mirror file as in its raw file. Without this, renaming an excluded
    # literal would still invert to the raw bytes and pass.
    allowed_by_file: dict[str, int] = {}
    for row_path, row_literal in sorted(seen_rows):
        row = {"path": row_path, "literal": row_literal}
        literal = row["literal"].encode()
        raw_path, mirror_path = raw.get(row["path"]), mirror.get(renamed_path(row["path"], frm, to))
        in_raw = raw_path.read_bytes().count(literal) if raw_path else 0
        in_mirror = mirror_path.read_bytes().count(literal) if mirror_path else 0
        if in_raw == 0:
            errors.append(f"literal exclusion {row['path']} {row['literal']} matches nothing in the raw export")
        if in_mirror != in_raw:
            errors.append(f"excluded literal {row['literal']} occurs {in_mirror}x in the mirror's "
                          f"{row['path']}, {in_raw}x in the raw export: it was changed")
        target = renamed_path(row["path"], frm, to)
        allowed_by_file[target] = allowed_by_file.get(target, 0) + in_raw * literal.count(frm.encode() + b"::")
    f = re.escape(frm.encode())
    unit_alt = b"|".join(re.escape(u.encode()) for u in rename["include_units"]) or b"(?!)"
    completeness = {
        f"{frm}::": re.compile(f + rb"::"),
        f"namespace {frm}": re.compile(rb"namespace\s+" + f + rb"(?![A-Za-z0-9_])"),
        f"{frm}/<unit>/": re.compile(rb"[<\"]" + f + rb"/(?:" + unit_alt + rb")/"),
        f"include/{frm}/": re.compile(rb"include/" + f + rb"/"),
    }
    for cls in rename["objc_classes"]:
        completeness[f"class {cls}"] = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(cls.encode()) + rb"(?![A-Za-z0-9_])")
    # Counted per file, so one file's allowance cannot cover another's leftover.
    totals = {name: 0 for name in completeness}
    for relative, path in mirror.items():
        data = path.read_bytes()
        for name, pattern in completeness.items():
            n = len(pattern.findall(data))
            totals[name] += n
            if name == f"{frm}::" and n != allowed_by_file.get(relative, 0):
                errors.append(f"{relative}: {n} `{frm}::`; its recorded exclusions allow "
                              f"{allowed_by_file.get(relative, 0)}")
    for name, n in totals.items():
        if name != f"{frm}::" and n:
            errors.append(f"{n} `{name}` remain in the mirror")
    return errors


def materialize(tree: dict, dest: Path) -> None:
    """Write a {path: blob} map's regular files under dest (the primary check refuses any other entry).

    Refuses an absolute path, a `..` or `.` segment or an empty segment before
    writing anything, so no entry can land outside dest.
    """
    for relative in tree:
        if relative.startswith("/") or any(part in ("", ".", "..") for part in relative.split("/")):
            raise ValueError(f"refusing to materialize unsafe path {relative!r}")
    for relative, entry in tree.items():
        if entry.mode in REGULAR_MODES:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.read_bytes())
            if entry.mode == "100755":
                target.chmod(0o755)


def secondary(raw_root, mirror_root, rename: dict, vellum_repo: Path) -> list[str]:
    """Run the recorded tool blob, not HEAD, under the recorded Python minor version.

    Each root is a directory or a git_tree() map; a map is materialized into a
    scratch directory, so the authoritative git input serves both checks.
    """
    if isinstance(raw_root, dict) or isinstance(mirror_root, dict):
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            if isinstance(raw_root, dict):
                try:
                    materialize(raw_root, base / "raw")
                except ValueError as error:
                    return [str(error)]
                raw_root = base / "raw"
            if isinstance(mirror_root, dict):
                try:
                    materialize(mirror_root, base / "mirror")
                except ValueError as error:
                    return [str(error)]
                mirror_root = base / "mirror"
            return secondary(raw_root, mirror_root, rename, vellum_repo)
    want = rename.get("python", "")
    have = f"{sys.version_info.major}.{sys.version_info.minor}"
    if want != have:
        return [f"receipt pins Python {want or '(none)'}; this interpreter is {have}"]
    try:
        source = subprocess.run(["git", "-C", str(vellum_repo), "cat-file", "blob", rename["tool_git_blob"]],
                                check=True, capture_output=True).stdout
    except (subprocess.CalledProcessError, KeyError) as error:
        return [f"cannot read the recorded tool blob: {error}"]
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        tool = work / "tool" / "rename_mirror_namespace.py"
        tool.parent.mkdir()
        tool.write_bytes(source)
        for helper, blob in rename.get("helper_git_blobs", {}).items():
            if not HELPER_NAME.match(helper):
                return [f"helper name {helper!r} is not a bare *.py basename"]
            data = subprocess.run(["git", "-C", str(vellum_repo), "cat-file", "blob", blob],
                                  check=True, capture_output=True).stdout
            (tool.parent / helper).write_bytes(data)
        copy = work / "repo" / "mirror"
        shutil.copytree(raw_root, copy)
        table = work / "repo" / "edge.json"
        table.write_text(json.dumps(rename["edge_table"]))
        exclusions = work / "repo" / "exclusions.json"
        exclusions.write_text(json.dumps(rename["literal_exclusions"]))
        result = subprocess.run([sys.executable, "-I", "-B", str(tool), str(copy), "--to", rename["to"],
                                 "--edge-table", str(table), "--literal-exclusions", str(exclusions),
                                 "--no-collision-census", "--apply"], capture_output=True, text=True)
        if result.returncode != 0:
            return [f"the recorded tool failed: {result.stderr.strip()[-400:]}"]
        report = json.loads(result.stdout)
        if report.get("rule_digest") != rename.get("tool_rule_digest"):
            errors.append("the recorded tool's rule digest differs from the receipt's")
        produced, mirror = files_under(copy), files_under(mirror_root)
        if set(produced) != set(mirror):
            errors.append("the recorded tool produces a different path set")
        for relative in sorted(set(produced) & set(mirror)):
            if produced[relative].read_bytes() != mirror[relative].read_bytes():
                errors.append(f"the recorded tool produces different bytes for {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, help="the raw Pulp export tree, as a directory")
    parser.add_argument("--mirror", type=Path, help="the renamed mirror tree, as a directory")
    parser.add_argument("--raw-git", nargs=2, metavar=("REPO", "TREEISH"),
                        help="the raw export read from git objects (preferred: no checkout filters)")
    parser.add_argument("--mirror-git", nargs=2, metavar=("REPO", "TREEISH"),
                        help="the mirror read from git objects, e.g. <vellum> HEAD:mirror")
    parser.add_argument("--records", type=Path,
                        help="CI mode: blob-origin records (v2); --raw is rebuilt from Pulp at the export receipt's "
                             "pulp_sha, with --pulp-repo and --export-receipt. The other raw inputs are for scratch use.")
    parser.add_argument("--scratch", action="store_true",
                        help="allow --raw/--raw-git, which are not authoritative; never set in CI")
    parser.add_argument("--pulp-repo", type=Path)
    parser.add_argument("--export-receipt", type=Path, help="the mirror export receipt (its pulp_sha)")
    parser.add_argument("--pulp-main", default="origin/main", help="the Pulp ref pulp_sha must descend into")
    parser.add_argument("--receipt", type=Path, required=True, help="JSON holding the `rename` block")
    parser.add_argument("--secondary", action="store_true")
    parser.add_argument("--vellum-repo", type=Path)
    args = parser.parse_args()
    document = json.loads(args.receipt.read_text())
    rename = document.get("rename", document)
    if sum(map(bool, (args.raw, args.raw_git, args.records))) != 1 or bool(args.mirror) == bool(args.mirror_git):
        print("error: give exactly one of --records/--raw/--raw-git and one of --mirror/--mirror-git",
              file=sys.stderr)
        return 2
    if (args.raw or args.raw_git) and not args.scratch:
        print("error: --raw and --raw-git are scratch inputs (pass --scratch); CI uses --records", file=sys.stderr)
        return 2
    source_errors: list[str] = []
    if args.records:
        if not (args.pulp_repo and args.export_receipt):
            print("error: --records needs --pulp-repo and --export-receipt", file=sys.stderr)
            return 2
        records = json.loads(args.records.read_text())
        records = records.get("records", records) if isinstance(records, dict) else records
        pulp_sha = json.loads(args.export_receipt.read_text()).get("pulp_sha", "")
        raw, source_errors = raw_from_records(args.pulp_repo, records, pulp_sha, args.pulp_main)
    else:
        raw = git_tree(Path(args.raw_git[0]), args.raw_git[1]) if args.raw_git else args.raw
    mirror = git_tree(Path(args.mirror_git[0]), args.mirror_git[1]) if args.mirror_git else args.mirror
    errors = source_errors + verify(raw, mirror, rename)
    if args.secondary:
        if not args.vellum_repo:
            print("error: --secondary needs --vellum-repo", file=sys.stderr)
            return 2
        errors += secondary(raw, mirror, rename, args.vellum_repo)
    print(json.dumps({"status": "fail" if errors else "pass", "authoritative_raw": bool(args.records),
                      "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
