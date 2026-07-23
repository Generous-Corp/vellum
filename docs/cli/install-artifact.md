# SDK artifact and installer contract

No release artifact is hosted in the extraction milestone. The repository can
build a reproducible local artifact, and the installers support local
development, verified local archive, and exact-version release modes so the
release boundary is executable before publication.

The gzip-compressed tar has this root layout:

```text
vellum_cli.py
vellum_backend.py
vellum_manifest.py
vellum_png.py
templates/basic/...
metadata.json
sdk/include/...
sdk/lib/cmake/Vellum/...
sdk/lib/libvellum-*.a
sdk/bin/vellum-app-host             # pinned-Skia mode
sdk/lib/libvellum-authoring.dylib   # pinned-Skia mode
sdk/lib/libvellum-gpu.dylib         # pinned-Skia mode
design-ir/bin/vellum-backend.js
design-ir/src/...
design-ir/schema/...
design-ir/LICENSE.md
ui/package.json                     # pinned-Skia mode
ui/scripts/build-project.mjs        # pinned-Skia mode
ui/node_modules/esbuild/...         # exact package-lock versions
ui/node_modules/typescript/...
vellum_native_backend.py            # pinned-Skia mode
```

`metadata.json` uses `vellum.sdk-artifact.v1`, records framework and CLI
compatibility, inventories every payload file by SHA-256, and states capability
claims. Claims are derived from the files and installed CMake targets, then
independently recomputed by the verifier. The default artifact claims the
authoring CLI, deterministic DesignIR import/reimport, and CMake SDK while
recording GPU rendering and native application commands as unavailable.

Pass the verified pinned Skia archive explicitly to compose the installed
`Vellum::Gpu` and `Vellum::Authoring` targets plus `@vellum/ui` and its exact
esbuild/TypeScript dependencies:

```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip \
  --output-dir dist --json
```

`build`, `run`, `test`, `capture`, and `package` become available only when the
installed app host, GPU/authoring libraries, UI compiler and locked dependencies,
and `cli/vellum_native_backend.py` all exist. Missing any one of those payloads
keeps every native command claim false.

The installer creates three distinct executable roles under
`$VELLUM_SDK_ROOT/bin`: `vellum-backend` is the stable dispatcher,
`vellum-import-backend` runs the packaged DesignIR backend, and
`vellum-native-backend` is installed only when its source implementation was
packaged. Import and native command implementations therefore cannot shadow one
another. Node.js 20 or newer is required when installing this artifact. The GPU
artifact contains its locked esbuild platform binary and TypeScript compiler;
application builds do not resolve framework packages from the network. Project
creation materializes the exact runtime package from the SDK and proves the
committed npm lock with `npm ci`.

The installer writes `$VELLUM_SDK_ROOT/install-manifest.json`. A verified
archive install records the exact archive basename, SHA-256, target, framework
version, and source commit. A `--local` install records `verified: false` with
null archive, hash, and commit fields; it never manufactures release identity.
`vellum create` copies that identity into `framework.lock`, and later commands
reject a different installed artifact even when its framework version matches.

`SHA256SUMS` must contain exactly one basename entry for the archive. Both
installers calculate SHA-256 and abort before extraction when the entry is
missing, duplicated, malformed, or mismatched. Release mode requires an exact
version and resolves one archive name from OS/architecture; `latest` is rejected.

Build and validate the artifact:

```sh
python3 scripts/build_sdk_artifact.py --output-dir dist --json
python3 scripts/verify_sdk_artifact.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS --json
python3 scripts/validate_installed_sdk.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS \
  --output dist/installed-validation.json --json
```

The normal builder refuses a dirty source tree so `source_commit` identifies
the packaged bytes. Its explicit `--allow-dirty` mode exists for local testing
and records `source_tree_clean=false` in metadata and evidence.

The last command produces `vellum.installed-sdk-validation.v1` evidence for
checksum verification, clean-prefix installation, CMake relocation, sterile
consumer configure/build/test, installed-CLI project creation, and exact
project-lock compatibility, including the exact artifact SHA. It also runs
installed CLI import and reimport from
two fixture revisions, verifies that the accepted active revision advances,
embeds the materialized imported design in a real `.app`, and exercises the
installed build/run/test/capture/package journey.

A production release must additionally provide immutable versioned assets,
GitHub asset digests, release provenance/attestation, and a separately
reviewable installer checksum. Those controls are intentionally not claimed by
the current local artifact flow.
