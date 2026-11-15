#!/usr/bin/env bash
# Would the next release be refused? See release_canary.py. The version is
# MAJOR.MINOR.PATCH without the v; the default is the next patch release.
set -euo pipefail
exec python3 -I "$(dirname "$0")/release_canary.py" "$@"
