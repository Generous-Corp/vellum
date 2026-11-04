# Releasing an SDK version

The release version lives only in `CMakeLists.txt` (and the matching
`FRAMEWORK_VERSION` constants and the other version surfaces that
`scripts/version_at_land.py` lists). Workflows derive the version, tag, archive
and release name from it, and a tag that differs from `v<version>` is
refused. Never hard-code a version in a workflow.

1. **Nothing, usually.** After every landing on `main`, `version-at-land.yml`
   counts the changes since the newest release tag. When something that ships
   in the SDK has changed, it opens (or updates) a pull request from
   `automation/version-bump` that moves every version surface to the next
   version. It lands through the queue like any other change. To steer the
   level, put `Version-Bump: vellum=<patch|minor|major>` or
   `Version-Bump: skip reason="..."` on a commit of the change.
2. The Pulp observatory receiver reconciles the landing and lands its evidence
   commit; `release-tag.yml` tags that commit, signs it, freezes the tag, and
   the tag push runs `gpu-macos.yml` and then `sdk-release.yml`. See
   `docs/provenance/release-signing.md`.

A person may still open the bump by hand, for example to release early: run
`python3 -I scripts/version_at_land.py apply --to <version>` and open the
result as a pull request.

Whether immutable releases are enabled is read live, with the release App
token, both before tagging and when `sdk-release.yml` finalizes. There is no
committed preflight to refresh. `release-watchdog.yml` and
`release-cadence-check.yml` open an issue when a release fails or does not
appear.
