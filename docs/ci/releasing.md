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

The release publishes `vellum-gpu-runtime-<version>-darwin-arm64.tar.gz`
beside the SDK archive, with `vellum-gpu-runtime.sha256` and
`gpu-runtime-verification.json`. `sdk-release.yml` does not take the GPU run's
word for it: `scripts/verify_gpu_runtime_release.py validate` uses main's
verifiers against a checkout of the tagged commit to check every file, rebuild
and run the audio-only consumer with a GPU adapter, and check the device API
closure. `finalize` then ties the bytes it publishes to that record. Like the
SDK archive, it is covered by the immutable release's attestation.

CMake writes four of that archive's package files, and their text changes
between CMake releases. The GPU run and `sdk-release.yml` run on different
hosts, so both generate those files with the CMake `gpu-runtime/cmake-lock.json`
pins (`scripts/pinned_cmake.py` fetches it and checks its SHA-256), not the one
installed on the runner. To move to another CMake, change the version and
digest in that lock; the next GPU run builds with it and the finalizer
regenerates with it.

Only the bot's pull request can release: `release-tag.yml` re-derives the bump
from main's history and tags nothing when the landed version differs from it in
any way (a hand-set number, an extra hunk, a skipped version). To release a
higher level, put the trailer on a landed change and let version-at-land
propose it.

Whether immutable releases are enabled is read live, with the release App
token, both before tagging and when `sdk-release.yml` finalizes. There is no
committed preflight to refresh. `release-watchdog.yml` and
`release-cadence-check.yml` open an issue when a release fails or does not
appear.
