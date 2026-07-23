#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, os, tempfile, unittest
from pathlib import Path
from unittest import mock
P=Path(__file__).with_name("readme_exec.py"); S=importlib.util.spec_from_file_location("r",P)
assert S and S.loader
r=importlib.util.module_from_spec(S); S.loader.exec_module(r)
class Tests(unittest.TestCase):
    def temp(self,text):
        path=Path(tempfile.mkdtemp())/"README.md"; path.write_text(text); return path
    def test_repository_readme(self):
        blocks=r.parse(r.README)
        self.assertEqual([b["id"] for b in blocks if b.get("profile")=="clean-release"],
                         ["release-prerequisites","release-install-create-run"])
    def test_unclassified_fails_closed(self):
        with self.assertRaisesRegex(r.Error,"every sh"):
            r.parse(self.temp("```sh\ntrue\n```\n"))
    def test_non_adjacent_marker_fails_closed(self):
        with self.assertRaisesRegex(r.Error,"every sh"):
            r.parse(self.temp("<!-- readme-exec: id=x manual=requires-user-supplied-export -->\ntext\n```sh\ntrue\n```\n"))
    def test_profile_xor_manual(self):
        with self.assertRaisesRegex(r.Error,"exactly one"):
            r.parse(self.temp("<!-- readme-exec: id=x profile=p manual=requires-user-supplied-export -->\n```sh\ntrue\n```\n"))
    def test_arbitrary_manual_reason_fails_closed(self):
        with self.assertRaisesRegex(r.Error,"unknown manual reason"):
            r.parse(self.temp("<!-- readme-exec: id=x manual=whatever -->\n```sh\ntrue\n```\n"))
    def test_auth_required(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ,{"GH_TOKEN":"","GITHUB_TOKEN":""}):
            with self.assertRaisesRegex(r.Error,"requires"):
                r.execute([{"id":"x","profile":"p","language":"sh","command":"true"}],"p",Path(d))
    def test_evidence_and_failure(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ,{"GH_TOKEN":"x"}):
            path=Path(d); code=r.execute([{"id":"x","profile":"p","language":"sh",
                "command":"printf output; exit 7"}],"p",path)
            self.assertEqual(code,1)
            self.assertIn("output",(path/"transcript.log").read_text())
            self.assertIn('"exitCode": 7',(path/"timings.json").read_text())
            self.assertTrue((path/"environment.json").is_file())
    def test_manual_annotations_are_retained(self):
        blocks=[
            {"id":"x","profile":"p","language":"sh","command":"true"},
            {"id":"y","manual":"requires-user-supplied-export",
             "language":"sh","command":"false"},
        ]
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ,{"GH_TOKEN":"x"}):
            path=Path(d)
            self.assertEqual(r.execute(blocks,"p",path),0)
            evidence=(path/"timings.json").read_text()
            self.assertIn('"manualBlockCount": 1',evidence)
            self.assertIn('"requires-user-supplied-export"',evidence)
if __name__=="__main__": unittest.main(verbosity=2)
