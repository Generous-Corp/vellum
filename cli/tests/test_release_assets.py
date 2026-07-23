from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(REPO / "scripts/verify_release_assets.py"))
verify = MODULE["verify"]
ReleaseVerificationError = MODULE["ReleaseVerificationError"]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ReleaseAssetsTests(unittest.TestCase):
    def test_exact_published_immutable_asset_set_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "sdk.tar.gz"
            second = root / "SHA256SUMS"
            first.write_bytes(b"sdk bytes")
            second.write_bytes(b"checksums")
            release = {
                "tag_name": "v0.1.0",
                "draft": False,
                "immutable": True,
                "published_at": "2026-07-23T00:00:00Z",
                "assets": [
                    {
                        "name": first.name,
                        "state": "uploaded",
                        "size": first.stat().st_size,
                        "digest": f"sha256:{digest(first.read_bytes())}",
                    },
                    {
                        "name": second.name,
                        "state": "uploaded",
                        "size": second.stat().st_size,
                        "digest": f"sha256:{digest(second.read_bytes())}",
                    },
                ],
            }
            result = verify(
                release,
                "v0.1.0",
                [(first.name, first), (second.name, second)],
                require_published=True,
                require_immutable=True,
            )
            self.assertEqual(result["status"], "pass")
            self.assertEqual(set(result["assets"]), {first.name, second.name})

    def test_digest_and_asset_set_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "sdk.tar.gz"
            asset.write_bytes(b"sdk bytes")
            base = {
                "tag_name": "v0.1.0",
                "draft": False,
                "immutable": True,
                "published_at": "2026-07-23T00:00:00Z",
                "assets": [
                    {
                        "name": asset.name,
                        "state": "uploaded",
                        "size": asset.stat().st_size,
                        "digest": f"sha256:{digest(asset.read_bytes())}",
                    }
                ],
            }
            tampered = json.loads(json.dumps(base))
            tampered["assets"][0]["digest"] = f"sha256:{'0' * 64}"
            with self.assertRaisesRegex(
                ReleaseVerificationError, "digest differs"
            ):
                verify(
                    tampered,
                    "v0.1.0",
                    [(asset.name, asset)],
                    require_published=True,
                    require_immutable=True,
                )

            unexpected = json.loads(json.dumps(base))
            unexpected["assets"].append(
                {
                    "name": "unexpected.txt",
                    "state": "uploaded",
                    "size": 0,
                    "digest": f"sha256:{digest(b'')}",
                }
            )
            with self.assertRaisesRegex(
                ReleaseVerificationError, "asset set differs"
            ):
                verify(
                    unexpected,
                    "v0.1.0",
                    [(asset.name, asset)],
                    require_published=True,
                    require_immutable=True,
                )

    def test_published_and_immutable_requirements_are_real_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            asset = Path(temporary) / "sdk.tar.gz"
            asset.write_bytes(b"sdk bytes")
            release = {
                "tag_name": "v0.1.0",
                "draft": True,
                "immutable": False,
                "published_at": None,
                "assets": [
                    {
                        "name": asset.name,
                        "state": "uploaded",
                        "size": asset.stat().st_size,
                        "digest": f"sha256:{digest(asset.read_bytes())}",
                    }
                ],
            }
            verify(
                release,
                "v0.1.0",
                [(asset.name, asset)],
                require_published=False,
                require_immutable=False,
            )
            with self.assertRaisesRegex(
                ReleaseVerificationError, "not published"
            ):
                verify(
                    release,
                    "v0.1.0",
                    [(asset.name, asset)],
                    require_published=True,
                    require_immutable=False,
                )
            release["draft"] = False
            release["published_at"] = "2026-07-23T00:00:00Z"
            with self.assertRaisesRegex(
                ReleaseVerificationError, "not immutable"
            ):
                verify(
                    release,
                    "v0.1.0",
                    [(asset.name, asset)],
                    require_published=True,
                    require_immutable=True,
                )


if __name__ == "__main__":
    unittest.main()
