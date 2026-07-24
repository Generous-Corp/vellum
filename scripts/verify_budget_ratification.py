#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ARTIFACT=ROOT/"product/budget-ratification.v1.json"
SCHEMA=ROOT/"product/schemas/budget-ratification.v1.schema.json"
def verify():
    value=json.loads(ARTIFACT.read_text()); schema=json.loads(SCHEMA.read_text())
    expected={"schema","status","budgetSource","requiredEvidence","ratification"}
    if set(value)!=expected or value["schema"]!=schema["properties"]["schema"]["const"]:
        raise ValueError("schema identity or fields differ")
    if value["budgetSource"]!="product/budgets.yaml" or value["status"] not in {"unratified","ratified"}:
        raise ValueError("budget source or status differs")
    evidence=value["requiredEvidence"]
    if not isinstance(evidence,list) or len(evidence)<3 or len(set(evidence))!=len(evidence) or not all(isinstance(x,str) and x for x in evidence):
        raise ValueError("required evidence is incomplete")
    rat=value["ratification"]; fields={"evidenceCommit","environment","timings","ratifiedValues"}
    if not isinstance(rat,dict) or set(rat)!=fields: raise ValueError("ratification fields differ")
    values=[rat[x] for x in fields]
    if value["status"]=="unratified" and any(x is not None for x in values):
        raise ValueError("unratified state cannot claim partial evidence")
    if value["status"]=="ratified":
        if any(x is None for x in values): raise ValueError("ratification requires complete evidence")
        if not isinstance(rat["evidenceCommit"],str) or not re.fullmatch(r"[0-9a-f]{40}",rat["evidenceCommit"]):
            raise ValueError("evidence commit must be a full SHA")
        if any(not isinstance(rat[field],str) or not rat[field].endswith(".json")
               for field in ("environment","timings")):
            raise ValueError("evidence paths must be non-empty JSON paths")
        if not isinstance(rat["ratifiedValues"],dict) or not rat["ratifiedValues"]:
            raise ValueError("ratified values must be non-empty")
if __name__=="__main__":
    try: verify()
    except (OSError,json.JSONDecodeError,ValueError) as error:
        print(f"budget-ratification: FAIL: {error}",file=sys.stderr); raise SystemExit(1)
    print("budget-ratification: OK")
