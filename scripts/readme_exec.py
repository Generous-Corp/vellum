#!/usr/bin/env python3
"""Fail-closed linter/runner for classified README shell blocks."""
from __future__ import annotations
import argparse, json, os, platform, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MARKER = re.compile(r"<!-- readme-exec: ([^>]+) -->\s*\n```(sh|powershell)\n")
FIELDS = re.compile(r"([a-z][a-z0-9-]*)=([A-Za-z0-9._-]+)")

class Error(ValueError): pass

def parse(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    fences = list(re.finditer(r"```(sh|powershell)\n", text))
    markers = list(MARKER.finditer(text))
    classified_fences = [
        marker.end() - len(f"```{marker.group(2)}\n") for marker in markers
    ]
    if [fence.start() for fence in fences] != classified_fences:
        raise Error("every sh/powershell block needs an adjacent readme-exec marker")
    result, seen = [], set()
    for marker in markers:
        fields = dict(FIELDS.findall(marker.group(1)))
        if "id" not in fields or (("profile" in fields) == ("skip" in fields)):
            raise Error("each marker needs id and exactly one of profile or skip")
        if set(fields) - {"id", "profile", "skip"}:
            raise Error("marker has unknown fields")
        if fields["id"] in seen:
            raise Error(f"duplicate id: {fields['id']}")
        seen.add(fields["id"])
        end = text.find("\n```", marker.end())
        if end < 0: raise Error(f"unterminated block: {fields['id']}")
        result.append({**fields, "language": marker.group(2),
                       "command": text[marker.end():end]})
    return result

def execute(blocks: list[dict[str, str]], profile_name: str, evidence: Path) -> int:
    chosen = [b for b in blocks if b.get("profile") == profile_name]
    if not chosen or any(b["language"] != "sh" for b in chosen):
        raise Error("profile must contain executable sh blocks")
    if not (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")):
        raise Error("private release execution requires GH_TOKEN or GITHUB_TOKEN")
    evidence.mkdir(parents=True, exist_ok=True)
    env_record = {"schema": "vellum.readme-exec-environment.v1",
                  "profile": profile_name, "platform": platform.platform(),
                  "machine": platform.machine(), "python": platform.python_version(),
                  "releaseAuthenticationPresent": True}
    (evidence/"environment.json").write_text(
        json.dumps(env_record, indent=2, sort_keys=True)+"\n")
    safe_keys = {"CI","GH_TOKEN","GITHUB_TOKEN","HOME","LANG","LC_ALL","PATH",
                 "RUNNER_ARCH","RUNNER_OS","SHELL","TMPDIR"}
    env = {k:v for k,v in os.environ.items() if k in safe_keys}
    timings, status = [], "passed"
    with (evidence/"transcript.log").open("w", encoding="utf-8") as stream:
        for block in chosen:
            stream.write(f"$ README block {block['id']}\n{block['command']}\n")
            stream.flush(); started = time.monotonic()
            run = subprocess.run(["bash","-euo","pipefail","-c",block["command"]],
                cwd=ROOT, env=env, text=True, stdout=stream,
                stderr=subprocess.STDOUT, check=False)
            timings.append({"id":block["id"],"seconds":round(time.monotonic()-started,3),
                            "exitCode":run.returncode})
            if run.returncode: status = "failed"; break
    (evidence/"timings.json").write_text(json.dumps(
        {"schema":"vellum.readme-exec-result.v1","profile":profile_name,
         "status":status,"blocks":timings}, indent=2, sort_keys=True)+"\n")
    return 0 if status == "passed" else 1

def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--readme",type=Path,default=README)
    parser.add_argument("--lint",action="store_true")
    parser.add_argument("--execute-profile")
    parser.add_argument("--evidence-dir",type=Path)
    args=parser.parse_args(argv)
    if args.lint == bool(args.execute_profile): parser.error("choose one mode")
    try:
        blocks=parse(args.readme)
        if args.lint: print(f"readme-exec: OK ({len(blocks)} blocks)"); return 0
        if args.evidence_dir is None: raise Error("--evidence-dir is required")
        return execute(blocks,args.execute_profile,args.evidence_dir)
    except (OSError,Error) as error:
        print(f"readme-exec: FAIL: {error}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
