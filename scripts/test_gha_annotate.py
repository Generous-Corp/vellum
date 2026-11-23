import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gha_annotate


class GhaAnnotateTests(unittest.TestCase):
    def test_escapes_workflow_command_values(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.dict(
            os.environ, {"GITHUB_ACTIONS": "true"}, clear=False
        ):
            gha_annotate.error("check%\r\n", "item%\r\n")
        self.assertEqual(
            output.getvalue(),
            "::error title=check%25%0D%0A::item%25%0D%0A\n",
        )

    def test_is_silent_outside_actions(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            gha_annotate.error("check", "item")
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
