# SDK artifact and installer contract

No release artifact is hosted in the extraction milestone. The repository can
build a reproducible local artifact, and the installers support local
development, verified local archive, and exact-version release modes so the
release boundary is executable before publication.

The gzip-compressed tar has this root layout:

```text
vellum_cli.py
vellum_backend.py
templates/basic/...
metadata.json
sdk/include/...
sdk/lib/cmake/Vellum/...
sdk/lib/libvellum-*.a
design-ir/bin/vellum-backend.js
design-ir/src/...
design-ir/schema/...
design-ir/LICENSE.md
```

`metadata.json` uses `vellum.sdk-artifact.v1`, records framework and CLI
compatibility, inventories every payload file by SHA-256, and states capability
claims. The current artifact claims the authoring CLI, deterministic DesignIR
import/reimport, and CMake SDK; it explicitly records native application and GPU
rendering capabilities as unavailable.

The installer creates three distinct executable roles under
`$VELLUM_SDK_ROOT/bin`: `vellum-backend` is the stable dispatcher,
`vellum-import-backend` runs the packaged DesignIR backend, and the absent
`vellum-native-backend` name is reserved for a future native application
backend. Import and native command implementations therefore cannot shadow one
another. Node.js 20 or newer is required when installing this artifact.

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
project-lock compatibility. It also runs installed CLI import and reimport from
two fixture revisions and verifies that the accepted active revision advances.

A production release must additionally provide immutable versioned assets,
GitHub asset digests, release provenance/attestation, and a separately
reviewable installer checksum. Those controls are intentionally not claimed by
the current local artifact flow.
