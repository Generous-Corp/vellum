#!/bin/sh
# Run an iOS simulator app bundle under `xcrun simctl` and propagate its exit code.
# usage: run_ios_sim.sh <App.app> [args...]
# Exits 77 (CTest SKIP_RETURN_CODE) when no simulator runtime/device is available.
set -u
app="$1"; shift
command -v xcrun >/dev/null 2>&1 || { echo "skip: xcrun missing"; exit 77; }
xcrun simctl help >/dev/null 2>&1 || { echo "skip: simctl unavailable"; exit 77; }
udid=$(xcrun simctl list devices available -j 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)["devices"]
for rt in sorted(d):
    if "iOS" not in rt: continue
    for x in d[rt]:
        if x.get("isAvailable") and x["name"].startswith("iPhone"):
            print(x["udid"]); sys.exit(0)
' 2>/dev/null)
[ -n "${udid:-}" ] || { echo "skip: no available iOS simulator device"; exit 77; }
xcrun simctl boot "$udid" 2>/dev/null || true
xcrun simctl bootstatus "$udid" -b >/dev/null 2>&1 || { echo "skip: simulator failed to boot"; exit 77; }
bid=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Info.plist" 2>/dev/null) \
    || { echo "error: no CFBundleIdentifier in $app"; exit 1; }
xcrun simctl install "$udid" "$app" || { echo "error: simctl install failed"; exit 1; }
xcrun simctl launch --console-pty --wait-for-exit "$udid" "$bid" "$@"
