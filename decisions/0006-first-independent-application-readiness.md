# Decision 0006: first independent application validation

- Date: 2026-07-24
- Status: independent application validated; zero-remediation readiness not yet proven

Palette Board is the first independent Vellum application. It lives in
`Generous-Corp/vellum-palette-board`, contains no Vellum or Pulp source, and
consumes the immutable Vellum `v0.1.6` SDK release through its checked-in
`framework.lock`.

The reviewed application commit is
`7137e045b6a135595704b06b009fdc3c19691410`. Its clean hosted validation is
GitHub Actions run `30052032733`, which completed successfully at that exact
commit. The retained application record is `docs/validation.json`; Vellum's
machine-readable registration is
`provenance/downstream-consumers.v1.json`.

The evidence proves the complete first-application journey:

1. verified installation from an immutable private release;
2. deterministic Figma import and safe reimport across three revisions;
3. preservation of developer-owned TypeScript/JSX behavior;
4. native Skia Graphite, Dawn, and Metal rendering without fallback;
5. an application-owned C++ component through the public native ABI;
6. pointer, keyboard, persistence, capability-denial, screenshot, and montage
   scenarios;
7. browser JavaScript calling the shared C++ core compiled to Wasm, including
   the portable C++ component; and
8. reproducible macOS and web packages.

Palette Board carries no framework patch or private-API exception. The
consumer registry's exception list is empty.

This does not prove that Vellum needed zero framework remediation on the first
attempt. Palette Board exposed issues that were fixed in Vellum first and
delivered through immutable releases `v0.1.4`, `v0.1.5`, and `v0.1.6` before
the application updated its pin. That is the required framework-first
development model, but it does not satisfy the stricter Phase 4
zero-same-day-patch readiness gate.

The retained private-incubation evidence also leaves three narrower Phase 2
claims open:

- `product/budgets.yaml` remains provisional because the clean hosted evidence
  was not recorded as a normalized, owner-ratified timing set;
- the cautious bootstrap uses a pinned signed tag, GitHub immutable-release
  verification, and checksums, but does not yet provide the specification's
  independent pinned-public-key detached signature route; and
- transactional interruption recovery is covered by negative-control tests,
  including a retry after a pre-activation failure, but that injected lifecycle
  has not yet been replayed and retained as a checkout-free clean-VM transcript.

These are explicit limitations, not implied passes or public release claims.
They do not invalidate the demonstrated installed-product journey, but they
must be closed or deliberately amended before claiming the complete v3 Phase 2
gate.

Decision: continue standalone validation. The framework has demonstrated that
an interesting non-audio application can be built and maintained through its
public installed-product surface. It has not yet earned Pulp dependency
adoption or a claim that a new application needs no framework remediation.

The exact next product step is to create a second small non-audio application
from `v0.1.6` using only the public installer and CLI, without changing
Vellum during initial scaffold/import/build/run. Passing that frozen-release
exercise will close the zero-remediation readiness gate; any required Vellum
change remains framework-first and resets the readiness window.
