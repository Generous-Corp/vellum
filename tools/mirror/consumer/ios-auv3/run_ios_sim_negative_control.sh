#!/bin/sh
# Negative control for run_ios_sim.sh: the app is built to fail its pixel check.
# usage: run_ios_sim_negative_control.sh <run_ios_sim.sh> <App.app> [args...]
# Passes only if the wrapper returns exit code 1 AND the app printed the pixel
# message; exit 77 (no simulator) is passed through as a skip.
set -u
wrapper="$1"; shift
log=$(mktemp "${TMPDIR:-/tmp}/ios-neg-control.XXXXXX") || exit 1
trap 'rm -f "$log"' EXIT
/bin/sh "$wrapper" "$@" >"$log" 2>&1
rc=$?
cat "$log"
[ "$rc" -eq 77 ] && exit 77
if [ "$rc" -eq 1 ] && grep -q 'negative control: expected pixel 255, got' "$log"; then
    exit 0
fi
echo "negative control did not fail as expected (exit $rc)" >&2
exit 1
