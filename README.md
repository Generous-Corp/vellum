# Vellum

Vellum is an experimental, audio-free application framework extracted to test
one product question: can a developer import a design, add TypeScript or
JavaScript behavior, and ship a GPU-rendered application without Chromium or an
OS WebView as the primary UI runtime?

This repository is at the independent-validation milestone. The project CLI
and repository shape are usable; native rendering, import, and packaging remain
capabilities supplied by an SDK backend and may report `capability_unavailable`
until that backend artifact is installed.

## Five-minute local-development start

No public SDK release exists yet. Install the CLI from this checkout, create a
sterile application, and inspect exactly what is and is not ready:

```sh
git clone git@github.com:Generous-Corp/vellum.git
cd vellum
./scripts/install.sh --local "$PWD"
export PATH="$HOME/.local/bin:$PATH"

tmp_app="$(mktemp -d)/vellum-hello"
vellum create "Vellum Hello" --directory "$tmp_app"
cd "$tmp_app"
vellum doctor --fix
vellum --json build
```

The final command intentionally fails with structured
`capability_unavailable` output until the native SDK backend is installed. It
must not pretend that a renderer or package was produced.

On Windows PowerShell, the equivalent local-development installer is:

```powershell
.\scripts\install.ps1 -LocalRoot $PWD
$env:Path = "$HOME\.local\bin;$env:Path"
vellum create "Vellum Hello" -d "$env:TEMP\vellum-hello"
```

## CLI journey

```sh
vellum create MyApp
cd myapp
vellum doctor --fix
vellum import ./design.pulp.zip --source-type figma
vellum reimport
vellum build --target macos
vellum run --target macos
vellum test
vellum capture --scenario smoke --output artifacts/smoke.png
vellum package --target macos --output dist
```

`create` is deterministic and separates imported snapshots, normalized
DesignIR, generated UI, tokens/assets, hand-written app logic, optional native
components, platform modules, tests, and packaging configuration. Runtime
commands require a compatible `vellum-backend` discovered through
`VELLUM_SDK_ROOT`, `VELLUM_BACKEND`, or `PATH`.

Every command accepts `--json` before or after the command and emits one stable
`vellum.cli.result.v1` object. See [the CLI contract](docs/cli/contract.md).

## Installer verification

The local-development installer copies files from an already trusted checkout;
it is explicitly not release verification. A future release archive must be
accompanied by `SHA256SUMS`, and both installers refuse to extract an archive
without exactly one matching SHA-256 entry:

```sh
./scripts/install.sh \
  --archive ./vellum-sdk-darwin-arm64.tar.gz \
  --checksums ./SHA256SUMS
```

For a cautious future network install, download the version-pinned installer,
archive, and checksum manifest separately; verify the installer source before
running it; then pass the local archive and manifest as above. Checksum
verification protects the downloaded bytes but does not make an unreviewed
network script intrinsically safe. No `curl | sh` command is advertised before
there is an immutable, checksummed release to install.

## Current boundary

- No Pulp audio, plug-in, host, or product adapters belong in Vellum.
- Pulp does not consume this repository during independent validation.
- macOS and browser/Wasm are the first proof targets; other platforms should
  not be claimed before executable evidence exists.
- Import compatibility is a documented subset, not arbitrary DOM/CSS support.
