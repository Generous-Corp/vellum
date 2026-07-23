from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).with_name("templates_smoke.py")
SPEC = importlib.util.spec_from_file_location("templates_smoke", PATH)
assert SPEC and SPEC.loader
templates_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(templates_smoke)


class Tests(unittest.TestCase):
    def test_every_variant_uses_only_the_installed_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            (sdk / "bin").mkdir(parents=True)
            log = root / "calls.jsonl"
            cli = sdk / "bin/vellum"
            cli.write_text(
                f"#!{Path(os.sys.executable)}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['VELLUM_TEMPLATE_SMOKE_LOG'], 'a') as out:\n"
                "    out.write(json.dumps(args) + '\\n')\n"
                "command = next(value for value in args if value in "
                "['create','doctor','build','test'])\n"
                "data = {}\n"
                "if command == 'create':\n"
                "    template = args[args.index('--template') + 1]\n"
                "    destination = Path(args[args.index('--directory') + 1])\n"
                "    destination.mkdir(parents=True)\n"
                "    data = {'template': template}\n"
                "print(json.dumps({'schema':'vellum.cli.result.v1','ok':True,"
                "'status':command + '_passed','message':'ok','data':data,"
                "'diagnostics':[]}))\n",
                encoding="utf-8",
            )
            cli.chmod(0o755)
            old = os.environ.get("VELLUM_TEMPLATE_SMOKE_LOG")
            os.environ["VELLUM_TEMPLATE_SMOKE_LOG"] = str(log)
            try:
                payload = templates_smoke.smoke(sdk, root / "evidence.json")
            finally:
                if old is None:
                    os.environ.pop("VELLUM_TEMPLATE_SMOKE_LOG", None)
                else:
                    os.environ["VELLUM_TEMPLATE_SMOKE_LOG"] = old
            self.assertTrue(payload["ok"])
            self.assertEqual(
                [row["template"] for row in payload["templates"]],
                list(templates_smoke.VARIANTS),
            )
            calls = [
                json.loads(line) for line in log.read_text().splitlines()
            ]
            self.assertEqual(len(calls), 12)
            self.assertEqual(
                [next(value for value in call if value in {"create", "doctor", "build", "test"})
                 for call in calls],
                ["create", "doctor", "build", "test"] * 3,
            )
            imported = calls[4]
            self.assertIn("--from", imported)
            self.assertEqual(imported[imported.index("--template") + 1], "imported-app")

    def test_missing_installed_cli_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(templates_smoke.Error, "missing"):
                templates_smoke.smoke(Path(temporary))


if __name__ == "__main__":
    unittest.main(verbosity=2)
