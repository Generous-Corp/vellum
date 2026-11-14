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

`newest-run` is the guard the workflow runs before it posts anything: a rerun
of an older run for the same head must not overwrite the status the newest run
owns. Exit: 0 this run is the newest and may post, 3 a newer run owns the
status (post nothing), 2 the run list could not be read (post nothing).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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


def materialize(source: Path, head: str, dest_parent: Path) -> Path:
    """Write `head`'s tree into a fresh 0700 directory under `dest_parent`; return it.

    The directory is a repository that borrows `source`'s objects: its HEAD is
    `head` (detached) and its index is `head`'s tree, so git reads of it see
    the pull request's commit. Files are written from blobs, byte for byte,
    each created new (O_EXCL) without following a link (O_NOFOLLOW) in a
    directory made here, never through one. The tree may hold only regular
    files: a symlink, a gitlink, a duplicate path or an unsafe name is
    refused before anything is written. The directory is never inside the
    trusted checkout, nor the checkout inside it.
    """
    top = Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    common = Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
    dest = Path(tempfile.mkdtemp(prefix="pr-", dir=dest_parent)).resolve()
    os.chmod(dest, 0o700)
    for trusted_path in (top, common):
        if dest.is_relative_to(trusted_path) or trusted_path.is_relative_to(dest):
            raise InputError(f"the change would be materialized inside, or around, the trusted checkout {trusted_path}")
    listing = git(source, "ls-tree", "-r", "-z", "--full-tree", head)
    entries = []
    seen: set[str] = set()
    for item in listing.split(b"\0"):
        if not item:
            continue
        meta, raw_path = item.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        parts = path.split("/")
        if path.startswith("/") or any(part in ("", ".", "..", ".git") for part in parts):
            raise InputError(f"the tree holds an unsafe path {path!r}")
        if path in seen:
            raise InputError(f"the tree lists {path!r} twice")
        seen.add(path)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise InputError(f"the tree holds {path!r} as {mode} {kind}; only regular files are judged")
        entries.append((mode, sha, parts))
    # A path cannot be both a file and a directory of another path.
    directories = {"/".join(parts[:i]) for _, _, parts in entries for i in range(1, len(parts))}
    clash = sorted(directories & seen)
    if clash:
        raise InputError(f"the tree lists {clash[0]!r} as both a file and a directory")
    git(dest, "init", "-q")
    alternates = dest / ".git/objects/info/alternates"
    alternates.write_text(str(common / "objects") + "\n", encoding="utf-8")
    git(dest, "update-ref", "--no-deref", "HEAD", head)
    git(dest, "read-tree", head)
    batch = git(source, "cat-file", "--batch", data="".join(f"{sha}\n" for _, sha, _ in entries).encode())
    offset = 0
    for mode, sha, parts in entries:
        header_end = batch.index(b"\n", offset)
        _, kind, size = batch[offset:header_end].decode().split()
        start = header_end + 1
        content = batch[start:start + int(size)]
        offset = start + int(size) + 1
        directory = dest
        for part in parts[:-1]:
            directory = directory / part
            if os.path.lexists(directory):
                if directory.is_symlink() or not directory.is_dir():
                    raise InputError(f"{'/'.join(parts)}: {directory} is not a directory made here")
            else:
                os.mkdir(directory, 0o700)
        target = directory / parts[-1]
        if os.path.lexists(target):
            raise InputError(f"{'/'.join(parts)} already exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(target, flags, 0o755 if mode == "100755" else 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    return dest


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


def newest_run_decision(runs: object, own_run_id: object) -> tuple[bool, int]:
    """Whether `own_run_id` may post the status, and which run owns it.

    `runs` is the workflow's run list for one head commit (the `workflow_runs`
    array of the Actions API). The newest run is the latest `created_at`, ties
    broken by the higher run id. Raises InputError when the list cannot be
    trusted: malformed, or not containing this run, so callers fail closed.
    """
    try:
        own = int(own_run_id)
        if not isinstance(runs, list) or not runs:
            raise InputError("the run list is empty or not a list")
        ranked = [(str(run["created_at"]), int(run["id"])) for run in runs]
    except InputError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise InputError(f"the run list is unreadable: {exc!r}") from exc
    if own not in {run_id for _, run_id in ranked}:
        raise InputError(f"run {own} is not in the run list")
    owner = max(ranked)[1]
    return owner == own, owner


def newest_run_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="framework_trusted_verify.py newest-run")
    parser.add_argument("--runs-file", type=Path, required=True, help="the Actions API run-list JSON for the head")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.runs_file.read_text(encoding="utf-8"))
        runs = document.get("workflow_runs") if isinstance(document, dict) else None
        may_post, owner = newest_run_decision(runs, args.run_id)
    except (InputError, ValueError, OSError) as exc:
        print(f"framework-trusted-verify: cannot tell whether run {args.run_id} is the newest, posting nothing: {exc}",
              file=sys.stderr)
        return 2
    if may_post:
        return 0
    print(f"framework-trusted-verify: run {owner} is newer than run {args.run_id} for this head and owns the "
          f"status; run {args.run_id} posts nothing")
    return 3


def main(argv: list[str] | None = None) -> int:
    if (argv if argv is not None else sys.argv[1:])[:1] == ["newest-run"]:
        return newest_run_main((argv if argv is not None else sys.argv[1:])[1:])
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, required=True, help="the trusted checkout holding the PR's objects")
    parser.add_argument("--head", required=True, help="the pull request's head commit (40 hex)")
    parser.add_argument("--base", required=True, help="the base commit (40 hex)")
    parser.add_argument("--dest-parent", type=Path, required=True,
                        help="where to create the fresh 0700 directory the change is materialized into")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    for name, value in (("--head", args.head), ("--base", args.base)):
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            print(f"framework-trusted-verify: {name} must be a 40-hex commit", file=sys.stderr)
            return 2
    try:
        dest = materialize(args.repo, args.head, args.dest_parent)
        result = judge(dest, args.base)
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
