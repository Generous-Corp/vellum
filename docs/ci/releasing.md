# Releasing an SDK version

The release version lives only in `CMakeLists.txt` (and the matching
`FRAMEWORK_VERSION` constants). Workflows derive the version, tag, archive
and release name from it, and a tag that differs from `v<version>` is
refused. Never hard-code a version in a workflow.

1. **Release pull request** (an agent may open it): bump `CMakeLists.txt`,
   `cli/vellum_cli.py`, `scripts/build_sdk_artifact.py` and the other
   `0.x.y` references, set `provenance/release-request.json` to the same
   version, and merge through the queue. No time limit.
2. Nothing else. The Pulp observatory receiver reconciles the landing and
   lands its evidence commit; `release-tag.yml` tags that commit, signs it,
   freezes the tag, and the tag push runs `gpu-macos.yml` and then
   `sdk-release.yml`. See `docs/provenance/release-signing.md`.

Whether immutable releases are enabled is read live, with the release App
token, both before tagging and when `sdk-release.yml` finalizes. There is no
committed preflight to refresh.
