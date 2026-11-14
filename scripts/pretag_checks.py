#!/usr/bin/env python3
"""Run a release tag's content checks before the tag exists.

A pushed release tag is frozen, so any check that runs only on the tag and
depends on the tagged commit's content must refuse the tag before it is made:
v0.1.8 and v0.1.9 were tagged and frozen, and then their own provenance runs
failed on content no re-run could change. release-tag.yml's secret-free
select-release-commit job runs this before it releases a selection; the
signing job never starts when it fails.

The checks are the tag pipeline's own steps, read from the workflow files at
the commit being tagged and run with the environment a push of the tag gives
them (GITHUB_REF=refs/tags/<tag>, GITHUB_SHA=<commit>):

  gpu-macos.yml    the release identity: the tag names CMakeLists.txt's version;
  provenance.yml   the signed-tag promotion evidence, the authority protocol
                   and release readiness (including the live open-pull-request
                   audit), the authority phase and documentation, and the
                   reconciled observatory check at the commit's time;
  sdk-release.yml  the release source's view-audio pin and archive, and the
                   renderer's release eligibility at the release source.

Steps that only verify the tag's signature or bind its object id are left
to the signing job, which verifies the signed tag before pushing it. The
promotion evidence step reads the tag message, so a local tag carrying the
attestation release_request.py builds is signed with a throwaway key in this
checkout and deleted afterwards; it is never pushed.

The workflows are read with a small reader for the one shape these steps use
(a `- name:` step whose `run` is a `|` block or a plain one-line command,
with optional `id`, `shell`, `if` and a flat `env` map). An expression it cannot resolve, a step it cannot find or a
`run` that is not a block refuses rather than guesses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Generous-Corp/vellum"
ZERO = "0" * 40
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA = re.compile(r"[0-9a-f]{40}")


class PretagError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    workflow: str
    job: str
    step: str
    # Run only when this earlier output has this value (the step's own `if:`).
    when: tuple[str, str] | None = None


# In order. Each later check may use the outputs of earlier ones.
CHECKS = (
    Check("gpu-macos.yml", "gpu-macos-arm64", "Derive the release identity from CMakeLists.txt"),
    Check("provenance.yml", "provenance-verify", "Resolve exact-boundary and signed-tag promotion evidence"),
    Check("provenance.yml", "provenance-verify", "Test authority protocol and negative controls"),
    Check("provenance.yml", "provenance-verify", "Resolve the exact authority lifecycle phase"),
    Check("provenance.yml", "provenance-verify", "Verify active repository documentation",
          when=("steps.authority-phase.outputs.phase", "active")),
    Check("provenance.yml", "observatory-verify", "Resolve the committed Pulp observatory cursor"),
    Check("provenance.yml", "observatory-verify", "Verify two-way observatory health and negative controls"),
    Check("sdk-release.yml", "validate-sdk-artifacts", "Materialize the release source's view-audio pin"),
    Check("sdk-release.yml", "validate-sdk-artifacts", "Fetch and verify the pinned Pulp view-audio archive"),
    Check("sdk-release.yml", "finalize-sdk-release", "Require renderer release eligibility at the release source"),
)


@dataclass
class Step:
    name: str
    run: str
    id: str | None = None
    shell: str | None = None
    condition: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def _scalar(text: str) -> str:
    text = text.split(" #", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def read_step(workflow: str, job: str, name: str) -> Step:
    """The step called `name` in `job`, from a workflow's text."""
    lines = workflow.splitlines()
    try:
        jobs = lines.index("jobs:")
        start = lines.index(f"  {job}:", jobs)
    except ValueError as error:
        raise PretagError(f"job {job} not found") from error
    end = next((index for index in range(start + 1, len(lines))
                if re.match(r"  [A-Za-z0-9_-]+:\s*$", lines[index])), len(lines))
    body = lines[start:end]
    heads = [index for index, line in enumerate(body) if re.match(r"      - name:", line)]
    for position, head in enumerate(heads):
        if _scalar(body[head].split(":", 1)[1]) != name:
            continue
        block = body[head:heads[position + 1] if position + 1 < len(heads) else len(body)]
        step = Step(name=name, run="")
        index = 1
        found_run = False
        while index < len(block):
            line = block[index]
            match = re.match(r"        ([a-z-]+):(.*)$", line)
            if not match:
                index += 1
                continue
            key, value = match.group(1), match.group(2)
            if key == "run":
                if re.fullmatch(r" [A-Za-z0-9_][A-Za-z0-9_./ =-]*", value.rstrip()):
                    # A one-line plain scalar such as `run: python3 tools/x.py`.
                    step.run = value.strip()
                    found_run = True
                    index += 1
                    continue
                if value.strip() != "|":
                    raise PretagError(f"{name}: `run` is neither a `|` block nor a plain one-line command")
                index += 1
                text = []
                while index < len(block) and (not block[index].strip() or block[index].startswith("          ")):
                    text.append(block[index][10:])
                    index += 1
                step.run = "\n".join(text).rstrip("\n") + "\n"
                found_run = True
                continue
            if key == "env":
                index += 1
                while index < len(block) and block[index].startswith("          "):
                    pair = re.match(r"          ([A-Za-z_][A-Za-z0-9_]*):(.*)$", block[index])
                    if pair is None:
                        if block[index].strip().startswith("#"):
                            index += 1
                            continue
                        raise PretagError(f"{name}: unreadable env line: {block[index].strip()}")
                    step.env[pair.group(1)] = _scalar(pair.group(2))
                    index += 1
                continue
            if key == "id":
                step.id = _scalar(value)
            elif key == "shell":
                step.shell = _scalar(value)
            elif key == "if":
                step.condition = _scalar(value)
            index += 1
        if not found_run:
            raise PretagError(f"{name}: has no `run` block")
        return step
    raise PretagError(f"{job} has no step {name!r}")


def resolve(text: str, expressions: dict[str, str], where: str) -> str:
    def one(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in expressions:
            raise PretagError(f"{where}: cannot resolve ${{{{ {key} }}}}")
        return expressions[key]
    return re.sub(r"\$\{\{(.*?)\}\}", one, text)


def use_check_runs_commit(script: str, commit: str | None) -> str:
    """`script` reading the check runs of `commit` instead of the tagged commit, when one is given."""
    if commit is None:
        return script
    if not SHA.fullmatch(commit):
        raise PretagError("--check-runs-commit must be a full SHA")
    return script.replace("commits/$GITHUB_SHA/check-runs", f"commits/{commit}/check-runs")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Runner:
    def __init__(self, root: Path, tag: str, commit: str, temp: Path, check_runs_commit: str | None = None) -> None:
        if not TAG.fullmatch(tag) or not SHA.fullmatch(commit):
            raise PretagError("the tag must be vMAJOR.MINOR.PATCH and the commit a full SHA")
        self.root, self.tag, self.commit, self.temp = root, tag, commit, temp
        # A commit that exists only locally has no check runs on GitHub; a caller that
        # rehearses one reads the runs of a real commit instead (release_canary.py).
        self.check_runs_commit = check_runs_commit
        self.outputs: dict[str, str] = {}
        token = os.environ.get("GH_TOKEN", "")
        self.expressions = {
            "github.ref": f"refs/tags/{tag}", "github.ref_name": tag, "github.sha": commit,
            "github.repository": REPOSITORY, "github.event_name": "push", "github.token": token,
            "runner.temp": str(temp), "github.workspace": str(root),
            # provenance.yml resolves this only on refs/tags/authority/*.
            "steps.observatory-authority-start.outputs.authority-start": "",
        }
        self.environment = {
            **os.environ, "GITHUB_REF": f"refs/tags/{tag}", "GITHUB_REF_NAME": tag, "GITHUB_REF_TYPE": "tag",
            "GITHUB_SHA": commit, "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_EVENT_NAME": "push",
            "GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(temp),
            # Job-level env of the jobs these steps belong to, as a tag push sets it.
            "VELLUM_TARGET_SHA": commit, "VELLUM_GIT_BASE": ZERO,
            "RELEASE_TAG": tag, "SOURCE_COMMIT": commit,
        }

    def run(self, check: Check, workflow: str) -> bool:
        if check.when and self.expressions.get(check.when[0]) != check.when[1]:
            print(f"skip  {check.workflow} / {check.step} ({check.when[0]} is not {check.when[1]})", flush=True)
            return True
        step = read_step(workflow, check.job, check.step)
        script = resolve(step.run, self.expressions, check.step)
        script = use_check_runs_commit(script, self.check_runs_commit)
        env = {**self.environment, **{key: resolve(value, self.expressions, check.step)
                                      for key, value in step.env.items()}}
        output = self.temp / "github-output"
        output.write_text("")
        env.update(GITHUB_OUTPUT=str(output), GITHUB_ENV=str(self.temp / "github-env"),
                   GITHUB_STEP_SUMMARY=str(self.temp / "step-summary.md"))
        script_path = self.temp / "step.sh"
        script_path.write_text(script if script.endswith("\n") else script + "\n")
        flags = ["--noprofile", "--norc", "-eo", "pipefail"] if step.shell == "bash" else ["-e"]
        print(f"run   {check.workflow} / {check.step}", flush=True)
        result = subprocess.run(["bash", *flags, str(script_path)], cwd=self.root, env=env, check=False)
        if result.returncode != 0:
            print(f"FAIL  {check.workflow} / {check.step} (exit {result.returncode})", flush=True)
            return False
        for line in output.read_text().splitlines():
            if "=" in line and step.id:
                key, value = line.split("=", 1)
                self.expressions[f"steps.{step.id}.outputs.{key}"] = value
        print(f"pass  {check.workflow} / {check.step}", flush=True)
        return True

    def sign_local_tag(self) -> None:
        """The promotion evidence step reads the tag message: a local tag, signed with a throwaway key."""
        sys.path.insert(0, str(self.root / "scripts"))
        import release_request  # noqa: PLC0415

        attested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = release_request.attestation(self.root, self.commit, attested_at, self.tag)
        except release_request.ReleaseRequestError as error:
            raise PretagError(f"the promotion attestation would not verify: {error}") from error
        message = self.temp / "promotion-attestation.json"
        message.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        key = self.temp / "throwaway-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pretag", "-f", str(key)], check=True)
        if git("tag", "--list", self.tag, cwd=self.root):
            raise PretagError(f"{self.tag} already exists in this checkout")
        git("-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "-c", "user.name=Vellum pretag",
            "-c", "user.email=pretag@invalid", "tag", "-s", "-F", str(message), self.tag, self.commit, cwd=self.root)

    def main(self) -> int:
        if git("rev-parse", "HEAD", cwd=self.root) != self.commit:
            raise PretagError(f"the checkout is not at {self.commit}")
        workflows = {name: (self.root / ".github/workflows" / name).read_text(encoding="utf-8")
                     for name in {check.workflow for check in CHECKS}}
        self.sign_local_tag()
        try:
            for check in CHECKS:
                if check.step == "Verify two-way observatory health and negative controls":
                    # provenance.yml checks out this Pulp commit before the step; so must the caller.
                    wanted = self.expressions.get("steps.observatory-cursor.outputs.pulp-sha", "")
                    have = git("rev-parse", "HEAD", cwd=self.root / ".observatory-pulp")
                    if have != wanted:
                        raise PretagError(f".observatory-pulp is at {have}, not the cursor's {wanted}")
                if not self.run(check, workflows[check.workflow]):
                    print(f"{self.tag} would fail {check.workflow} / {check.step} on {self.commit}; "
                          "refusing it before the tag exists", file=sys.stderr)
                    return 1
        finally:
            git("tag", "-d", self.tag, cwd=self.root)
        print(f"every content check the {self.tag} pipeline runs on {self.commit} passes")
        return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check-runs-commit", help="read this commit's check runs, not the tagged commit's "
                        "(for a rehearsal on a commit that exists only locally)")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="pretag-") as temporary:
            return Runner(args.root.resolve(), args.tag, args.commit, Path(temporary), args.check_runs_commit).main()
    except (OSError, subprocess.CalledProcessError, PretagError) as error:
        print(f"pretag checks refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
