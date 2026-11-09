#!/usr/bin/env python3
"""Download a URL, retrying through a release CDN's transient failures.

GitHub's release downloads can return 5xx for a minute or two at a time. A
CI step that gives up after a few seconds then fails a whole merge-group batch
before any check runs. This retries server errors (5xx, 429) and connection
failures with exponential backoff inside a total time budget, and fails at
once on any other client error (a 404 is a wrong URL, not a wobble). The
final error names the URL, the attempts and the last failure.

    retrying_download.py URL DESTINATION

Exit codes: 0 = downloaded, 1 = failed.
"""

from __future__ import annotations

import http.client
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

USER_AGENT = "vellum-ci"


class DownloadError(Exception):
    pass


def _retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500 or error.code == 429
    # A body cut short (IncompleteRead) is the same CDN wobble as a 5xx.
    return isinstance(error, (urllib.error.URLError, socket.timeout, TimeoutError,
                              ConnectionError, http.client.HTTPException))


def download_with_retry(
    url: str, destination: Path, *,
    budget: float = 180.0, first_delay: float = 2.0, max_delay: float = 30.0,
    timeout: float = 120.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Download url to destination; returns the number of attempts made."""
    started = clock()
    delay = first_delay
    attempt = 0
    while True:
        attempt += 1
        partial = destination.with_name(destination.name + ".partial")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response, \
                    partial.open("wb") as out:
                shutil.copyfileobj(response, out)
                # read(n) returns a short body without raising, so a connection
                # dropped mid-transfer would otherwise pass as a download.
                expected = response.headers.get("Content-Length")
                if expected is not None and out.tell() != int(expected):
                    raise http.client.IncompleteRead(b"", int(expected) - out.tell())
            partial.replace(destination)
            return attempt
        except Exception as error:  # noqa: BLE001 - classified below
            partial.unlink(missing_ok=True)
            if not _retryable(error):
                raise DownloadError(f"{url}: {error} (attempt {attempt}, not retried)") from error
            elapsed = clock() - started
            if elapsed + delay > budget:
                raise DownloadError(
                    f"{url}: {error} after {attempt} attempts in {elapsed:.0f}s "
                    f"(retry budget {budget:.0f}s)") from error
            sleep(delay)
            delay = min(delay * 2.0, max_delay)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: retrying_download.py URL DESTINATION", file=sys.stderr)
        return 1
    try:
        download_with_retry(args[0], Path(args[1]))
    except DownloadError as error:
        print(f"retrying_download: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
