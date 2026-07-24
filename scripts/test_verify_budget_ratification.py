#!/usr/bin/env python3
from __future__ import annotations
import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest import mock
P=Path(__file__).with_name("verify_budget_ratification.py"); S=importlib.util.spec_from_file_location("b",P)
assert S and S.loader
b=importlib.util.module_from_spec(S); S.loader.exec_module(b)
class Tests(unittest.TestCase):
    def test_checked_in_unratified_state(self):
        b.verify(); self.assertEqual(json.loads(b.ARTIFACT.read_text())["status"],"unratified")
    def mutated(self,change,message):
        value=json.loads(b.ARTIFACT.read_text()); change(value)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"x.json"; path.write_text(json.dumps(value))
            with mock.patch.object(b,"ARTIFACT",path), self.assertRaisesRegex(ValueError,message): b.verify()
    def test_false_ratification(self):
        self.mutated(lambda x:x.__setitem__("status","ratified"),"complete evidence")
    def test_partial_evidence(self):
        self.mutated(lambda x:x["ratification"].__setitem__("timings","timings.json"),"partial")
if __name__=="__main__": unittest.main(verbosity=2)
