#!/usr/bin/env python3
"""Disable each refusal in turn, run the tests that own it, and report CAUGHT or MISSED.

A refusal is a `raise`, an `errors.append(...)`/`extend(...)`, or a `return [f"..."]`
statement. Each is replaced by `pass`, the owning tests run, and the file is restored
with `git checkout`. A MISSED line means no test fails without that refusal.

Run it in a clean checkout whose tests are committed (the restore is `git checkout`):

    python3 tools/mirror/refusal_sweep.py <repo> mirror [LINE ...]
    python3 tools/mirror/refusal_sweep.py <repo> prov   [LINE ...]

`mirror` covers mirror_package.py, framework_ports.py and port_respell.py; `prov` covers the framework
ports and transfer functions of verify_extraction.py. LINE limits the sweep to
refusals starting on those lines. SHARD=i/n runs every n-th site, for parallel
sweeps in separate checkouts.
"""
import ast, subprocess, sys, pathlib
repo = pathlib.Path(sys.argv[1]); which = sys.argv[2]
only = {int(x) for x in sys.argv[3:]}
M = "tools/mirror/mirror_package.py"; F = "tools/mirror/framework_ports.py"; R = "tools/mirror/port_respell.py"; V = "tools/provenance/verify_extraction.py"
VF = {"derive_framework_transfer","load_framework_ports","framework_ports_history_errors","framework_ports_landed",
 "framework_ports_joining_errors","framework_ports_pulp_errors","framework_ports_native_errors","framework_ports_state",
 "framework_transfer_state","framework_introduction_errors","framework_landed_respell",
 "framework_transfer_records","framework_record_texts","framework_respell_base_table",
 "framework_respell_removal_errors",
 "framework_respell_errors","framework_respell_edit_errors"}
def sites(path, funcs=None, all_raise=False):
    src = (repo/path).read_text(); tree = ast.parse(src); out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
        if funcs is not None and fn.name not in funcs: continue
        for n in ast.walk(fn):
            ok = False
            if isinstance(n, ast.Raise): ok = True
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and n.value.func.attr in ("append","extend") and "err" in ast.unparse(n.value.func.value): ok = True
            elif isinstance(n, ast.Return) and isinstance(n.value, ast.List) and n.value.elts and isinstance(n.value.elts[0], ast.JoinedStr): ok = True
            if ok: out.append((n.lineno, n.end_lineno, fn.name))
    return sorted(set(out))
def run(cmd):
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True).returncode
if which == "mirror":
    jobs = [(M, s) for s in sites(M)] + [(F, s) for s in sites(F)] + [(R, s) for s in sites(R)]
    tests = [["python3","-m","unittest","discover","-s","tools/mirror","-p","test_mirror_package.py"],
             ["python3","-m","unittest","discover","-s","tools/mirror","-p","test_framework_ports.py"],
             ["python3","-m","unittest","discover","-s","tools/mirror","-p","test_port_respell.py"]]
else:
    jobs = [(V, s) for s in sites(V, VF)]
    tests = [["python3","-m","unittest","tools.provenance.test_verify_extraction","-k","ports","-k","transfer","-k","introduc","-k","framework","-k","respell"]]
assert all(run(t)==0 for t in tests), "baseline red"
import os
sh,n=map(int,os.environ.get('SHARD','0/1').split('/'))
for idx,(path,(a,b,fn)) in enumerate(jobs):
    if idx%n!=sh: continue
    if only and a not in only: continue
    p = repo/path; lines = p.read_text().split("\n")
    indent = lines[a-1][:len(lines[a-1])-len(lines[a-1].lstrip())]
    text = " ".join(l.strip() for l in lines[a-1:b])[:90]
    mut = lines[:a-1] + [indent+"pass"] + lines[b:]
    p.write_text("\n".join(mut))
    red = any(run(t)!=0 for t in tests)
    subprocess.run(["git","checkout","--",path], cwd=repo, check=True)
    print("CAUGHT" if red else "MISSED", f"{path}:{a}", fn, text, flush=True)
