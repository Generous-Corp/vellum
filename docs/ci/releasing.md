# Releasing an SDK version

The release version lives only in `CMakeLists.txt` (and the matching
`FRAMEWORK_VERSION` constants). Workflows derive the version, tag, archive
and release name from it, and a tag that differs from `v<version>` is
refused. Never hard-code a version in a workflow.

The immutable-release preflight (`provenance/immutable-release-preflight.json`)
is only accepted for 6 hours after its `checked_at`. A stale or wrong-tag file
fails the `Record private-release trust controls` step, so the refresh and the
tag must happen together, last.

1. **Version bump PR** (an agent may open it): bump `CMakeLists.txt`,
   `cli/vellum_cli.py`, `scripts/build_sdk_artifact.py` and the other
   `0.x.y` references, merge through the queue. No time limit.
2. Start the clock only once step 1 is on `main`. Sync to that commit.
3. Run `python3 scripts/refresh_immutable_release_preflight.py --release-tag vX.Y.Z`
   (needs `gh` with Administration: read).
4. Commit the refreshed file. The tag must point at a commit that contains
   it, so it is committed before tagging. If that commit must first land on
   `main` via PR, it goes through the full required gate and the merge
   queue; budget that time out of the 6h window (the tagged run itself also
   needs about 20 minutes before the trust step), and if the PR is slow,
   re-run step 3 and amend rather than reusing an aging file. Whether a tag
   on a commit not yet on `main` is acceptable to the downstream
   `sdk-release.yml` checks is untested; do not rely on it.
5. `git tag -s vX.Y.Z -m "Vellum X.Y.Z"` then `git push origin vX.Y.Z`.
   The run must reach the trust step within 6h of `checked_at`;
   `sdk-release.yml` re-checks it when it finalizes.
