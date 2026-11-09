#!/usr/bin/env python3
"""Judge a pull request's framework/ change with the default branch's rules.

Run by .github/workflows/framework-trusted-verify.yml on `pull_request_target`
and `merge_group`, from a checkout of the base (the default branch), never of
the pull request. The pull request's tree is data: its commit objects are
fetched into the trusted checkout, and this script materializes them into a
separate directory from git objects alone (no checkout, so no filters,
attributes, LFS or hooks), then judges that directory with the trusted copy of
verify_extraction.py. Nothing from the pull request is imported or executed.

- A change that touches neither framework/ data nor a framework rule file is
  a no-op pass.
- A change that introduces the transfer record is judged by
  framework_introduction_errors: no rule file may change, the landed rename
  tool must reproduce framework/ from the base's mirror/, the record must be
  the derived one, and the debt baseline must be the live counts.
- A later change to framework/ data is held to the full framework regime.
- A change to rule files alone is a rules change; its review judges it.

Exit: 0 pass (or no-op), 1 refused, 2 the inputs could not be read.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMEWORK_DATA_PREFIXES = ("framework/", "mirror/", "provenance/framework/", "provenance/framework-name-debt.json")
# Git, with nothing a repository's config or content can redirect.
SAFE_GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false"]


class InputError(RuntimeError):
    pass


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_extraction", HERE / "verify_extraction.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("verify_extraction", module)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run([*SAFE_GIT, "-C", str(repo), *args], input=data, capture_output=True, env=env, check=False)
    if result.returncode != 0:
        raise InputError(f"git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def materialize(source: Path, head: str, dest: Path) -> None:
    """Write `head`'s tree into `dest`, a repository that borrows `source`'s objects.

    dest's HEAD is `head` (detached) and its index is `head`'s tree, so git
    reads of dest see the pull request's commit; the files are written from
    blobs, byte for byte, with the tree's modes.
    """
    dest.mkdir(parents=True)
    git(dest, "init", "-q")
    alternates = dest / ".git/objects/info/alternates"
    objects = Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()) / "objects"
    alternates.write_text(str(objects.resolve()) + "\n", encoding="utf-8")
    git(dest, "update-ref", "--no-deref", "HEAD", head)
    git(dest, "read-tree", head)
    listing = git(dest, "ls-tree", "-r", "-z", "--full-tree", head)
    entries = []
    for item in listing.split(b"\0"):
        if not item:
            continue
        meta, raw_path = item.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts or ".git" in parts:
            raise InputError(f"the tree holds an unsafe path {path!r}")
        entries.append((mode, kind, sha, path))
    blobs = [e for e in entries if e[1] == "blob"]
    batch = git(dest, "cat-file", "--batch", data="".join(f"{sha}\n" for _, _, sha, _ in blobs).encode())
    offset = 0
    top = dest.resolve()
    for mode, _, sha, path in blobs:
        header_end = batch.index(b"\n", offset)
        _, kind, size = batch[offset:header_end].decode().split()
        start = header_end + 1
        content = batch[start:start + int(size)]
        offset = start + int(size) + 1
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(top):
            raise InputError(f"{path} would be written outside the tree")
        if mode == "120000":
            os.symlink(content, target)
        else:
            target.write_bytes(content)
            if mode == "100755":
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def judge(root: Path, base: str) -> dict:
    """The trusted verdict on `base..HEAD` of `root`."""
    verifier = load_verifier()
    os.environ["VELLUM_GIT_BASE"] = base
    # --no-renames: a file moved out of (or into) a judged path counts on both sides.
    changed = git(root, "diff", "--no-renames", "--name-only", "-z", base, "HEAD").decode("utf-8", errors="surrogateescape")
    names = [name for name in changed.split("\0") if name]
    data = sorted(n for n in names if n.startswith(FRAMEWORK_DATA_PREFIXES))
    rules = sorted(n for n in names if n.startswith(verifier.FRAMEWORK_RULE_PATHS))
    if not data and not rules:
        return {"status": "pass", "mode": "no-op", "errors": []}
    if not data:
        return {"status": "pass", "mode": "rules-only", "errors": [], "rule_files": rules}
    # The one transfer-state reading decides introduction against change.
    errors, checks = verifier.verify_framework(root, None, "origin/main", defer_identity=True)
    state = checks.get("framework_transfer_state") or {}
    mode = "introduction" if state.get("introduction") else (
        "framework-change" if state.get("head_framework") else "no-framework")
    if state.get("introduction") and checks.get("framework_transfer_introduced") is not True:
        errors = errors + ["framework/ is new but was not judged as an introduction"]
    return {"status": "fail" if errors else "pass", "mode": mode, "errors": errors, "rule_files": rules}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, required=True, help="the trusted checkout holding the PR's objects")
    parser.add_argument("--head", required=True, help="the pull request's head commit (40 hex)")
    parser.add_argument("--base", required=True, help="the base commit (40 hex)")
    parser.add_argument("--dest", type=Path, required=True, help="an absent directory to materialize the PR into")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    for name, value in (("--head", args.head), ("--base", args.base)):
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            print(f"framework-trusted-verify: {name} must be a 40-hex commit", file=sys.stderr)
            return 2
    try:
        materialize(args.repo, args.head, args.dest)
        result = judge(args.dest, args.base)
    except (InputError, ValueError, OSError) as exc:
        print(f"framework-trusted-verify: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    for error in result["errors"]:
        print(f"framework-trusted-verify: {error}", file=sys.stderr)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
