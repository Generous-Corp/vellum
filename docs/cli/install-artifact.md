# CLI artifact and installer contract

No release artifact is published in the extraction milestone. The installers
support a local-development mode and a verified local archive mode so the
release boundary can be tested before publishing.

A CLI archive is a gzip-compressed tar with this root layout:

```text
vellum_cli.py
templates/basic/...
bin/vellum-backend        # optional on Unix
bin/vellum-backend.exe    # optional on Windows
```

`SHA256SUMS` must contain exactly one basename entry for the archive. Both
installers calculate SHA-256 and abort before extraction when the entry is
missing, duplicated, malformed, or mismatched.

A production release must additionally provide immutable versioned assets,
release provenance/attestation, and a separately reviewable installer checksum.
Those controls are intentionally not claimed by the current local-development
flow.
