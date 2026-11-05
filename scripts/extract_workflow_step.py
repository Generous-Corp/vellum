#!/usr/bin/env python3
"""Print one workflow step's `run: |` script exactly as GitHub Actions runs it.

release-publish-rehearsal.yml uses this to run the SDK finalizer's publish
step from sdk-release.yml as written, instead of a copy that could drift from
it. The reader is deliberately narrow and fails closed: it accepts only a
two-space job key under `jobs:`, a step written `- name: <exact name>` in that
job, and a literal `run: |` block in that step. Anything else, including a
`${{ }}` expression in the script (which only Actions could expand) or a
step name that appears more than once in the job, is refused.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

JOB = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):[ \t]*$")
STEP = re.compile(r"^(?P<indent> *)- name: (?P<name>.+?)[ \t]*$")
RUN = re.compile(r"^(?P<indent> *)run: \|[ \t]*$")


class StepError(RuntimeError):
    pass


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def job_lines(lines: list[str], job: str) -> list[str]:
    try:
        start = lines.index("jobs:")
    except ValueError as error:
        raise StepError("the workflow has no top-level `jobs:` key") from error
    found: list[str] | None = None
    current: str | None = None
    for line in lines[start + 1:]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        header = JOB.match(line)
        if header:
            current = header["name"]
            if current == job:
                if found is not None:
                    raise StepError(f"job {job!r} appears more than once")
                found = []
            continue
        if current == job and found is not None:
            found.append(line)
    if found is None:
        raise StepError(f"the workflow has no job {job!r}")
    return found


def step_script(text: str, job: str, step: str) -> str:
    lines = job_lines(text.splitlines(), job)
    starts = [(index, match) for index, line in enumerate(lines)
              if (match := STEP.match(line)) and match["name"] == step]
    if len(starts) != 1:
        raise StepError(f"job {job!r} has {len(starts)} steps named {step!r}; expected 1")
    index, match = starts[0]
    step_indent = len(match["indent"])
    body: list[str] = []
    for line in lines[index + 1:]:
        if line.strip() and indent_of(line) <= step_indent:
            break
        body.append(line)
    runs = [(number, found) for number, line in enumerate(body)
            if (found := RUN.match(line)) and len(found["indent"]) == step_indent + 2]
    if len(runs) != 1:
        raise StepError(f"step {step!r} has no single literal `run: |` block")
    number, found = runs[0]
    key_indent = len(found["indent"])
    block: list[str] = []
    for line in body[number + 1:]:
        if line.strip() and indent_of(line) <= key_indent:
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    content = [line for line in block if line.strip()]
    if not content:
        raise StepError(f"step {step!r} has an empty script")
    # A literal block's indentation is set by its first non-empty line.
    width = indent_of(content[0])
    if any(indent_of(line) < width for line in content):
        raise StepError(f"step {step!r} has a line indented less than its first line")
    script = "\n".join(line[width:] for line in block) + "\n"
    if "${{" in script:
        raise StepError(f"step {step!r} uses a ${{{{ }}}} expression only Actions can expand")
    return script


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        script = step_script(args.workflow.read_text(encoding="utf-8"), args.job, args.step)
    except (OSError, UnicodeError, StepError) as error:
        print(f"extract-workflow-step: {error}", file=sys.stderr)
        return 1
    args.output.write_text(script, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
