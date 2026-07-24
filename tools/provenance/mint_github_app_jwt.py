#!/usr/bin/env python3
"""Mint a short-lived GitHub App JWT from a file-backed RSA private key."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
from pathlib import Path
import stat
import subprocess


class JwtError(RuntimeError):
    pass


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def canonical_segment(value: dict[str, object]) -> str:
    return base64url(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def validate_key(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        raise JwtError(f"cannot stat private key: {error}") from error
    if mode & 0o077:
        raise JwtError("GitHub App private key must not be group/world accessible")


def mint_jwt(
    *, app_id: int, private_key: Path, now: dt.datetime | None = None
) -> str:
    if app_id <= 0:
        raise JwtError("GitHub App ID must be positive")
    validate_key(private_key)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() != dt.timedelta(0):
        raise JwtError("JWT clock must be timezone-aware UTC")
    issued = int(current.timestamp()) - 60
    expires = issued + 9 * 60
    header = canonical_segment({"alg": "RS256", "typ": "JWT"})
    payload = canonical_segment({"exp": expires, "iat": issued, "iss": app_id})
    signing_input = f"{header}.{payload}".encode("ascii")
    try:
        completed = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-sign",
                str(private_key),
            ],
            input=signing_input,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (
            error.stderr.decode(errors="replace").strip()
            if isinstance(error, subprocess.CalledProcessError)
            else str(error)
        )
        raise JwtError(f"cannot sign GitHub App JWT: {detail}") from error
    return f"{header}.{payload}.{base64url(completed.stdout)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            mint_jwt(
                app_id=args.app_id,
                private_key=args.private_key.resolve(),
            )
        )
        return 0
    except JwtError as error:
        print(f"github-app-jwt: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
