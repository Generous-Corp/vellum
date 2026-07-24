#!/usr/bin/env python3

from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("mint_github_app_jwt.py")
SPEC = importlib.util.spec_from_file_location("mint_github_app_jwt", MODULE_PATH)
assert SPEC and SPEC.loader
jwt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jwt)


def decode_segment(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class GitHubAppJwtTests(unittest.TestCase):
    def test_token_is_short_lived_rs256_and_signature_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_key = root / "app.pem"
            public_key = root / "app.pub.pem"
            subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "RSA",
                    "-pkeyopt",
                    "rsa_keygen_bits:2048",
                    "-out",
                    str(private_key),
                ],
                check=True,
                capture_output=True,
            )
            private_key.chmod(0o600)
            subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-in",
                    str(private_key),
                    "-pubout",
                    "-out",
                    str(public_key),
                ],
                check=True,
                capture_output=True,
            )
            now = dt.datetime(
                2026, 7, 23, 23, 35, tzinfo=dt.timezone.utc
            )
            token = jwt.mint_jwt(
                app_id=3878000, private_key=private_key, now=now
            )
            header_segment, payload_segment, signature_segment = token.split(
                "."
            )
            self.assertEqual(
                json.loads(decode_segment(header_segment)),
                {"alg": "RS256", "typ": "JWT"},
            )
            payload = json.loads(decode_segment(payload_segment))
            self.assertEqual(payload["iss"], 3878000)
            self.assertEqual(payload["exp"] - payload["iat"], 9 * 60)

            signed = root / "signed.txt"
            signature = root / "signature.bin"
            signed.write_bytes(
                f"{header_segment}.{payload_segment}".encode("ascii")
            )
            signature.write_bytes(decode_segment(signature_segment))
            verified = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(public_key),
                    "-signature",
                    str(signature),
                    str(signed),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("Verified OK", verified.stdout)

    def test_key_permissions_and_app_id_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "app.pem"
            key.write_text("not a key\n", encoding="utf-8")
            key.chmod(0o644)
            with self.assertRaisesRegex(jwt.JwtError, "group/world"):
                jwt.mint_jwt(app_id=1, private_key=key)
            key.chmod(0o600)
            with self.assertRaisesRegex(jwt.JwtError, "positive"):
                jwt.mint_jwt(app_id=0, private_key=key)


if __name__ == "__main__":
    unittest.main()
