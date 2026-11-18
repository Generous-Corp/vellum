# Dependencies

This file describes Vellum's active source, build, and redistributed dependency
surface. The filtered Git history contains older Pulp paths for provenance;
those paths are not automatically active Vellum dependencies. Only dependencies
reachable from a Vellum target, tool, installed SDK, or release artifact belong
in the active inventory.

Vellum does not include audio, MIDI, DSP, plug-in formats, plug-in hosting, or
audio-device dependencies.

## Installed authoring tool dependency

The macOS arm64 GPU SDK carries esbuild 0.28.1 and its matching
`@esbuild/darwin-arm64` binary so a created application can bundle TS, JS, and
JSX without installing project-local packages. Both packages are MIT licensed.
Their exact npm tarball integrities are locked by
`packages/vellum-ui/package-lock.json` and repeated in
`provenance/third-party-lock.json`. The SDK builder installs them with
`npm ci --ignore-scripts` into a temporary directory, copies only these two
packages into the payload, and inventories the resulting installed bytes by
SHA-256 in `metadata.json`.

## Active renderer dependency

The macOS arm64 GPU proof uses one byte-locked prebuilt renderer artifact:

| Artifact | Locked identity | Use |
|---|---|---|
| `skia-build-mac-universal-gpu-release.zip` | `danielraffel/skia-builder` release `chrome/m158`; SHA-256 `dca73c0eee5b0f6d129fe357d0754f7165c6b436051e0de947aeedd3e1bde250` | Build-time Skia Graphite and Dawn/Metal static libraries and headers |

The pin itself is written once, in `gpu-runtime/renderer-lock.json`; this
table, `provenance/third-party-lock.json` and the attestation must agree with
it (`scripts/check_renderer_lock.py`). The published
`vellum-gpu-runtime-<version>-darwin-arm64.tar.gz` redistributes the
artifact's `libdawn_combined.a` unchanged, with the Dawn and Tint, Abseil and
PartitionAlloc notices the legal inventory places in it.

The artifact was built by skia-builder's `build-skia.yml` on `refs/heads/main`
at builder commit `312f05e24a5d5a45d4a3e70ca70ff3ccff1b4d97` (run
40626447857, attempt 1, GitHub-hosted). Its source tuple is Skia commit
`be7d7c3e996e567eda7dc9774274f7a0103cdb8c` and Dawn commit
`1716e88cc9e58398fab5d4473f7f2e858667b677`, as recorded by the release's legal
manifest. The Dawn commit is also embedded in
`build/include/dawn/dawn_version.h`; the exact Skia and builder commits are not
embedded in the artifact. The builder's Sigstore build-provenance attestation
covers the archive, its legal manifest and its Apple toolchain receipt
(`docs/provenance/renderer-release-attestation.md`).

The artifact SHA-256, all eleven static-archive SHA-256 values, and a
construction-specific digest over all 811 header files are locked in
[`provenance/third-party-lock.json`](provenance/third-party-lock.json). Release
construction must verify those bytes before configuring Vellum. An arbitrary
local `VELLUM_SKIA_DIR` is a development convenience, not release provenance.

The locked archives are linked into the installed `Vellum::SkiaDawnProvider`
shared library. `Vellum::Gpu` and `Vellum::MirrorCanvas` share that provider
and its installed declaration roots. Consumers do not need a Skia checkout
or receive the prebuilt static archives. A distinct provider is rejected by
the `VELLUM_SKIA_DAWN_PROVIDER` compatible-interface requirement.

### Code packaged inside `libskia.a`

The sealed archive's member table demonstrates that `libskia.a` packages the
following observed upstream code. These are not inferred merely from Skia's
full DEPS file; each row has matching object members in the locked archive.

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Skia, including skcms | `be7d7c3e996e567eda7dc9774274f7a0103cdb8c` | BSD-3-Clause | Skia/core/Graphite/skcms object members |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | `libexpat.*` members |
| libjpeg-turbo | `e14cbfaa85529d47f9f55b0f104a579c1061f9ad` (Chromium's 3.1.0 snapshot) | IJG and zlib for the packaged libjpeg API/SIMD code | `libjpeg.*`, `libjpeg12.*`, and `libjpeg16.*` members; no TurboJPEG API member was identified |
| libpng | `d5515b5b8be3901aac04e5bd8bd5c89f287bcd33` (1.6.56) | Libpng-2.0 | `libpng.*` members |
| libwebp | `845d5476a866141ba35ac133f856fa62f0b7445f` | BSD-3-Clause | `libwebp.*` and `libwebp_sse41.*` members |
| Wuffs | `e3f919ccfe3ef542cfc983a82146070258fb57f8` | Apache-2.0 | `libwuffs.wuffs-v0.3.o` |
| Chromium zlib | `646b7f569718921d7d4b5b8e22572ff6c76f2596` (`1.3.0.1-motley`) | zlib | `libzlib.*` and `zlib_*` members |

### Code packaged inside `libdawn_combined.a`

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Dawn and Tint | `1716e88cc9e58398fab5d4473f7f2e858667b677` | BSD-3-Clause | Dawn/Tint objects and symbol namespaces; exact revision embedded in `dawn_version.h` |
| Abseil | `af5aede14a0b4013be6890148d34e0fd7b1fac69` | Apache-2.0 | `absl::` symbols and implementation members including `raw_hash_set.cc.o` and `crc32c.cc.o` |
| PartitionAlloc | `03cc513177b4340bee3dbfd46f6dd5fdded43b79` | BSD-3-Clause | `partition_alloc::` symbols and allocator implementation members including `low_level_alloc.cc.o` |

Vellum also links the byte-locked `libskparagraph.a`, `libskshaper.a`,
`libskunicode_core.a`, and `libskunicode_icu.a` archives into the provider to
provide paragraph-level attributed text, shaping, bidi, and Unicode fallback.
The CMake integration checks each archive against the digest already recorded
in the lock before linking it. HarfBuzz and ICU implementation objects are
packaged in these separate archives rather than `libskia.a` or
`libdawn_combined.a`. The provider also carries the locked `libsvg.a` and
`libskresources.a` used by the mirror. Whole-archive linking keeps the core definitions
available to either frontend and to consumers of the installed declarations.
The SVG factory anchor uses ordinary archive selection because `libsvg.a`
also embeds duplicate Skia/resource objects.

The linked `libskshaper.a` contains HarfBuzz objects from commit
`9cb1fee51069b206effb4736e443b038d230789d`. The linked
`libskunicode_icu.a` contains ICU objects from commit
`d578f2e8b7bd5938e21cfb6bf15c079e0aa5b738`. Both identities come from the
exact locked Skia revision's primary-source `DEPS`; archive member names provide
the binary evidence. Their complete upstream license texts are reproduced in
`NOTICE.md` and therefore travel with the installed SDK.

## Packaged font assets

The installed GPU SDK carries byte-locked Inter Regular; Jost Regular, Medium,
SemiBold, and Bold; variable-weight Noto Sans JP; and variable-width/weight Noto
Sans Arabic under `share/vellum/fonts`. They are active runtime assets used for
deterministic measurement, paint, Japanese fallback, and Arabic shaping, not
historical extraction material.
Exact versions, hashes, sources, and selection behavior are recorded in
[`assets/fonts/README.md`](assets/fonts/README.md); all seven files use the SIL
Open Font License 1.1.

The rows above name the components with their own license texts. The
exhaustive inventory is the builder's content-addressed legal manifest for the
locked archive: it maps every zip member and every object member of every
static archive to its license files, and the renderer verifier re-derives
that inventory from the archive bytes before the renderer is release
eligible. The manifest resolves each file to its nearest ancestor license file;
it does not adjudicate license compatibility.

## Three.js runtime payload

`assets/threejs` carries Three.js 0.183.1 (MIT), the runtime a scripted view
loads as ES modules: `build/three.webgpu.js`, `build/three.core.js`,
`build/three.module.js`, the OrbitControls, GLTFLoader, BufferGeometryUtils and
SkeletonUtils add-ons, `LICENSE` and `package.json`. The files are unmodified
bytes of `mrdoob/three.js` at revision `077dd13c0e869d9f3dbe55875686f920367de457`.
Each file is pinned by SHA-256 in `assets/threejs/payload-manifest.json`, and
that manifest is pinned in `provenance/third-party-lock.json`.
`scripts/check_threejs_payload.py` refuses a missing, extra or changed file,
and the root `CMakeLists.txt` runs it at configure time.

## Compatibility observations, not attestations

Representative Mach-O members report these `LC_BUILD_VERSION` tuples:

| Input | Observed minimum macOS | Observed SDK |
|---|---:|---:|
| `libskia.a` | 13.0 | 15.5 |
| `libdawn_combined.a` | 13.0 | 15.5 |

The strictest observed minimum is therefore macOS 13.0. These are observations
from representative members, not proof over every object. The artifact does
not record the exact compiler build, libc++ ABI, or linker identity. It also
has no signed provenance or source-to-asset attestation and is not claimed to
be reproducible from source. These limitations prevent the current
incubation lock from being described as a production supply-chain attestation.

## Optional RmlUi module dependencies

The optional `rmlui/` module builds these from pinned source when it is
enabled (`VELLUM_ENABLE_RMLUI=ON`, default OFF, or a standalone
`cmake -S rmlui` build). Every input is fetched by exact identity and fails
the configure on a mismatch. They are compiled as static, hidden-visibility
archives and linked privately into the `VellumRmlUi::Core` shared library (or,
for stb_image_write, the `VellumRmlUi::SoftRenderer` archive, and for Lua the
`VellumRmlUi::Lua` archive). The installed `VellumRmlUi` package carries none
of their headers and the Core library exports none of their symbols; when the
Lua host is built, the static Lua archive is installed beside it for linking.
The opt-in `VellumRmlUi::Native` component (`VELLUM_RMLUI_NATIVE=ON`) is the
exception: it links RmlUi publicly, so its install component redistributes
RmlUi's headers and archives, and FreeType's archive when fonts are on, with
`rmlui/NOTICE-Native.md` and their licence files under `share/doc/VellumRmlUi/`
(under `share/doc/VellumRmlUiNative/` when `VELLUM_RMLUI_NATIVE_PACKAGE=ON`
installs it as its own `VellumRmlUiNative` package).
The base `VellumRmlUi` package installs RmlUi's licence there too, because
Core redistributes RmlUi. The native SDK artifact carries the base package
(Core, SoftRenderer and VellumRenderer) and never the Native component.
Nothing here enters the default Vellum build or the Vellum SDK artifact. The
optional RmlUi SDK artifact, `vellum-rmlui-sdk-<version>-darwin-arm64.tar.gz`,
is the `VellumRmlUiNative` package alone (Native, Metal, RmlUi's headers and
archives, and FreeType's archive), with the same notices.

| Dependency | Pinned identity | License | Used by | Notes |
|---|---|---|---|---|
| FreeType | `2.13.3`, tag `VER-2-13-3` archive of FreeType's GitHub mirror (same tag object as upstream), SHA-256 `bc5c898e4756d373e0d991bab053036c5eb2aa7c0d5c67e8662ddc6da40c4103` | FTL (FreeType License; the GPLv2 alternative is not used) | RmlUi font engine, only with `VELLUM_RMLUI_FONTS=ON` | PNG, zlib, bzip2, Brotli and HarfBuzz integrations disabled; FTL credit in `NOTICE.md` and, for the redistributed Native archive, `rmlui/NOTICE-Native.md` with `FTL.TXT` |
| Lua | `5.4.7`, `lua-5.4.7.tar.gz` SHA-256 `9fbf5e28ef86c69858f6d3d34eccc32e911c1a28b4120ff3e84aaa70cfbf1e30` | MIT | The sandboxed `VellumRmlUi::Lua` host, only with `VELLUM_RMLUI_LUA=ON` | Library only; the `lua` and `luac` front ends are not built. RmlUi's own Lua plugin is not built, since it keeps one process-wide `lua_State` |
| RmlUi | `6.3`, commit `ba95ffe8bfb6370efb2cdcca927eaad4710c5413` | MIT | `VellumRmlUi::Core`; `VellumRmlUi::Native` (redistributed headers and archives) | Samples, third-party containers, SVG, Lottie and Tracy disabled; notice in `rmlui/NOTICE-Native.md` with RmlUi's `LICENSE.txt`; one Vellum patch, `rmlui/cmake/patches/rmlui-fractional-glyph-advances.patch` (the default font engine accumulates glyph advances and kerning in 26.6 and rounds only the pen, so text is not up to 5 % wider at small sizes; an upstream candidate, not filed) |
| stb_image_write | `v1.16`, `nothings/stb` commit `d14d46f6b44456eb88beac58cba833a4135d4c89`, `stb_image_write.h` SHA-256 `cbd5f0ad7a9cf4468affb36354a1d2338034f2c12473cf1a8e32053cb6914a05` | MIT (chosen of MIT or public domain) | `VellumRmlUi::SoftRenderer` PNG encoder | Single header, compiled into one translation unit |

## Optional V8 provider

With `VELLUM_ENABLE_V8=ON` (default OFF; it needs `VELLUM_ENABLE_MIRROR=ON`)
the mirror's `view-script` unit compiles its V8 backend, and the installed SDK
carries the sealed V8 shared library that backend runs on. The pin is the
`V8` row of `provenance/third-party-lock.json`; the configure step
(`framework/tools/cmake/VellumV8Provider.cmake`) downloads the archive or
takes `VELLUM_V8_ARCHIVE`, refuses any other SHA-256, and refuses a manifest
that is not sealed or that does not pair with the Skia and Dawn commits
`gpu-runtime/renderer-lock.json` pins. Every source file that includes V8
headers compiles with the archive's own `consumer_defines`, which are part of
V8's ABI.

| Dependency | Pinned identity | License | Used by | Notes |
|---|---|---|---|---|
| V8 | `v8-m158-15.8.62.6-38351a002e2c` (V8 `15.8.62.6`, revision `38351a002e2c2890eca99d3abb6622728bfd7798`), `danielraffel/v8-builder` asset `v8-mac-arm64-m158-15.8.62.6-38351a002e2c.zip` SHA-256 `828ec374aad252afd284da044743c2099ce3c9401d5e914df79aec4713517eff`; `release-metadata.json` SHA-256 `5393fe41d2b16e39bed2e5e526dab8e08bc1d4b865eb13ea5b3056c05b67e6b1` | BSD-3-Clause; bundles ICU (Unicode-3.0), zlib and Abseil (Apache-2.0) | `Vellum::SkiaDawnProvider` links `libv8.dylib`; the SDK installs it in `lib/` beside the provider and its manifest in `share/vellum/v8/` | Milestone-matched to `chrome/m158`: built against Skia `be7d7c3e996e567eda7dc9774274f7a0103cdb8c` and Dawn `1716e88cc9e58398fab5d4473f7f2e858667b677`. The library exports only the `v8::` and `cppgc::` API; its ICU, zlib and Abseil stay internal, so they cannot bind to the provider's copies (`tools/mirror/v8_symbol_census.py`). The archive ships no ICU locale data file, so `Intl` formatting and collation fail (and `Date.prototype.toLocaleString` aborts the process); Unicode property escapes, case mapping and normalization work. Notice in `NOTICE.md` |

## Optional native tracing

With `VELLUM_ENABLE_TRACING=ON` (default OFF; it needs `VELLUM_ENABLE_MIRROR=ON`)
the mirrored framework units compile with their trace macros on and the
foundation unit's library carries one Perfetto, so the framework's slices and
the renderer's frame trace share one trace session. It is development tooling:
the default build and every shippable SDK or release artifact are built with
it OFF, fetch nothing for it, and link nothing from it. Installing a tracing-ON
build is not blocked: its foundation library carries Perfetto, so a development
SDK installed by hand from such a build contains it and must not be shipped. The configure step
(`cmake/VellumTracing.cmake`) downloads the release artifact and refuses any
other SHA-256. `FETCHCONTENT_SOURCE_DIR_VELLUM_PERFETTO` builds an unpacked
copy instead, unchecked. `tools/tracing/check_tracing.py` keeps the module,
this row and `provenance/third-party-lock.json` on one pin.

| Dependency | Pinned identity | License | Used by | Notes |
|---|---|---|---|---|
| Perfetto | `v57.2`, `google/perfetto` release asset `perfetto-cpp-sdk-src.zip` (`perfetto.h` and `perfetto.cc`) SHA-256 `c6fa3d89aee30f7da39402c9cd178c9f2e344544fda5c2109fd8457e319c3a2f` | Apache-2.0; the amalgamation also carries BSD-2-Clause code from OpenBSD's `tree.h` | `vellum-perfetto`, compiled as one position-independent static library and whole-archive linked into `vellum-mirror-foundation`, only with `VELLUM_ENABLE_TRACING=ON` | Never linked into a default build or a release artifact. A development SDK installed by hand from a tracing-ON build does contain it (the foundation library is installed as built); do not ship one. Perfetto's `TRACE_EVENT` takes a mutex when a chunk rolls over, so it is not real-time safe: trace UI, render and process-level code, never a live audio thread. Notice in `NOTICE.md` |

## Developer prerequisites

CMake, a C++20 compiler, Python 3, Node.js, and npm are developer-supplied
tools. The macOS proof additionally uses Xcode command-line tools and Apple
system frameworks. These tools and frameworks are not bundled or redistributed
by Vellum.

One build does not use the developer's CMake: the published
`vellum-gpu-runtime` archive. Its pinned build tool is fetched for that build
and for the release check that follows, and is not redistributed.

| Name | Version | License | Source | Why |
|---|---|---|---|---|
| CMake | `4.3.3`, `cmake-4.3.3-macos-universal.tar.gz` SHA-256 `5221a13450c7a0219a2a0d1b6c9085eb06489721fafd8488ccebc1584175d2fb` | BSD-3-Clause | Kitware's GitHub release `v4.3.3`, pinned in `gpu-runtime/cmake-lock.json` | CMake writes four of the archive's package files and their text differs between CMake releases. The archive is built on one host and held to its source on another, so both generate those files with this release. |

## Historical extraction material

The filtered seed once placed 232 projected source and vendored files in the
working tree. Those paths are deleted from the active tip. Their exact
historical identities remain in `provenance/cut-manifest.json` and Git history;
they are not build inputs, installed files, or release payloads. The active
source boundary is `provenance/active-source-boundary.json`. The Pulp framework
units admitted by Decision 0010 were mirrored blob by blob (recorded in
`provenance/mirror/blob-origins.json`) and then transferred, renamed, to the
native root `framework/`; `provenance/framework/transfer.json` pairs each
`framework/` file with its Pulp path and blob.

## Transferred framework inventory

The following rows are the legal inventory for the bytes under `framework/`:
the external inputs and vendored files of the units transferred from Pulp.
They are scoped to that root and do not make the corresponding historical Pulp paths active
Vellum dependencies. Versions and licenses were cross-checked against Pulp's
`DEPENDENCIES.md`, `NOTICE.md`, and `tools/deps/manifest.json` at the `46cee5e4e6e2` export.

| Dependency | Version or source identity | License | Mirror scope | Compatibility / handling |
|---|---|---|---|---|
| Abseil | `af5aede14a0b4013be6890148d34e0fd7b1fac69` | Apache-2.0 | not mirrored; no landed unit consumes it yet (planned renderer and V8 units) | MIT-compatible; transitive notice |
| Brotli | `v1.2.1` (`db24a98a`) | MIT | `canvas` unit external input (`framework/core/canvas/`, WOFF2 decoder); bytes not mirrored | MIT-compatible |
| CHOC | `pulp-webview-dnd-poc2` (`b586f33d`) | ISC | `foundation`, `state-core`, `view-core`, `design-import` and `view-script` unit external input, patched (JSON exponent, V8 compatibility); bytes not mirrored | MIT-compatible; retain fork attribution |
| cpp-httplib | vendored snapshot | MIT | `framework/external/cpp-httplib/` (`foundation` unit) | MIT-compatible |
| Dawn and Tint | `1716e88cc9e58398fab5d4473f7f2e858667b677` | BSD-3-Clause | `canvas` unit external input: headers inside the Skia build tree; bytes not mirrored | MIT-compatible; renderer notice exists |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | not mirrored; no landed unit consumes it yet (planned renderer units) | MIT-compatible; renderer notice exists |
| HarfBuzz | `9cb1fee51069b206effb4736e443b038d230789d` | Old MIT | `canvas` unit external input through the Skia build (text shaping); bytes not mirrored | MIT-compatible; notice exists |
| Highway | `1.4.0` (`2607d3b5`) | Apache-2.0 | `foundation` unit external input (`framework/core/simd/`); bytes not mirrored | MIT-compatible |
| ICU | `d578f2e8b7bd5938e21cfb6bf15c079e0aa5b738` | Unicode-3.0 plus bundled notices | `canvas` unit external input through the Skia build (text shaping); bytes not mirrored | Permissive; notice exists |
| Inter | `4.001; git-9221beed3` | SIL OFL 1.1 | `framework/external/fonts/` (`canvas` unit) | Allowed font license |
| JavaScriptCore | Apple system framework | LGPL-2.1+ components and Apple terms | Never mirrored; system framework only | **Not MIT-compatible**; system-only, never mirrored |
| JetBrains Mono | `2.304` | SIL OFL 1.1 | `framework/external/fonts/` (`canvas` unit) | Allowed font license |
| Jost | `3.710` | SIL OFL 1.1 | `framework/external/fonts/` (`canvas` unit) | Allowed font license |
| Mbed TLS | `v3.6.2` | Apache-2.0 | `foundation` unit external input (`framework/core/runtime/`); bytes not mirrored | MIT-compatible |
| miniz | `3.0.2` | MIT | `framework/external/miniz/` (`foundation` unit) | MIT-compatible |
| pugixml | `1.15` | MIT | `framework/external/pugixml/` (`foundation` unit) | MIT-compatible |
| TweetNaCl | `20140427` | Public domain | `framework/external/tweetnacl/` (`foundation` unit) | Public-domain code |
| WOFF2 | `fb9c3379f260` | MIT | `canvas` unit external input (`framework/core/canvas/`); bytes not mirrored | MIT-compatible |
| NanoSVG | vendored snapshot | zlib | `framework/external/nanosvg/` (`canvas` unit) | MIT-compatible |
| Noto Sans Arabic | variable font | SIL OFL 1.1 | not mirrored; no landed unit consumes it yet | Allowed font license |
| Noto Sans JP | variable font | SIL OFL 1.1 | not mirrored; no landed unit consumes it yet | Allowed font license |
| PartitionAlloc | `03cc513177b4340bee3dbfd46f6dd5fdded43b79` | BSD-3-Clause | not mirrored; no landed unit consumes it yet (planned GPU and renderer units) | MIT-compatible; transitive notice |
| QuickJS | `2021-03-27`, bundled in CHOC (`choc_javascript_QuickJS.h`) | MIT | `view-script` unit external input through that CHOC header; bytes not mirrored. Its MIT notice is in the header, and CHOC's license ships under `sdk/mirror/share/licenses/choc/` | MIT-compatible |
| SDL3 | `release-3.4.18` (`829a65d7`) | zlib | `render-2d` and `view-core` unit external input, built from source as the `SDL3-static` link target the link policy allows; bytes not mirrored; license ships under `sdk/mirror/share/licenses/sdl3/` | MIT-compatible |
| SheenBidi | `v3.0.0` | Apache-2.0 | `canvas` unit external input (`framework/core/canvas/`); bytes not mirrored | MIT-compatible |
| Skia and skcms | `be7d7c3e996e567eda7dc9774274f7a0103cdb8c` | BSD-3-Clause | `canvas` unit external input (`framework/core/canvas/`); bytes not mirrored | MIT-compatible; renderer notice exists |
| V8 | `v8-m158-15.8.62.6-38351a002e2c` | BSD-3-Clause | `view-script` unit external input with `VELLUM_ENABLE_V8=ON` (see "Optional V8 provider"); bytes not mirrored | MIT-compatible; sealed provider |
| yaml-cpp | `0.9.0` (`56e3bb55`) | MIT | `design-import` unit external input, built from source as the `yaml-cpp` link target the link policy allows; bytes not mirrored; license ships under `sdk/mirror/share/licenses/yaml-cpp/` | MIT-compatible |
| Yoga | `v3.2.1` (`042f5013`) | MIT | `view-core` unit external input, built from source as the `yogacore` link target the link policy allows; bytes not mirrored; license ships under `sdk/mirror/share/licenses/yoga/` | MIT-compatible |

### Units built into the native SDK

The macOS arm64 GPU SDK installs one `sdk/mirror/lib/libvellum-mirror-<unit>.dylib`
for every unit of the current mirror receipt (`foundation`, `gpu-device-api`,
`canvas`, `render-2d`, `state-core`, `view-core`, `design-import` and
`view-script`; `tools/mirror/VellumMirror.cmake`). The units' external inputs
above are fetched at the commits their closure manifests pin, and every file a
closure lists is checked against its recorded SHA-256 before configure
(`tools/mirror/mirror_package.py`). Only the foundation and state-core
libraries carry code of their own, and the provider loads both at launch;
Highway and Mbed TLS objects are linked into the foundation library. The other
six are installed forwarding dylibs that re-export the shared
`Vellum::SkiaDawnProvider`, which owns their objects, the SheenBidi, Brotli,
WOFF2, SDL3, Yoga and yaml-cpp objects, and the locked Skia/Dawn archives.
CHOC headers that the public headers include ship under
`sdk/mirror/include/choc/`. Each fetched dependency's license file is
installed under `sdk/mirror/share/licenses/`.
`Vellum::MirrorCanvas` exports only the mirrored namespace
and links that provider transitively, so a consumer can link it and
`Vellum::Gpu` into one image; a distinct provider is rejected by
`VELLUM_SKIA_DAWN_PROVIDER`.

Pulp's external contributor `matthewfudge` authored four mirrored view files:
`core/view/src/design_frame_view.cpp`,
`core/view/include/pulp/view/widget_bridge.hpp`,
`core/view/src/widget_bridge/state_binding_api.cpp`, and
`core/view/src/widget_bridge/widget_callbacks.cpp`. The history-preserving
mirror retains those author records; the inbound and outbound license is MIT,
so no separate relicensing or waiver is required. JavaScriptCore is the only
listed dependency with a non-MIT-compatible license concern, and it remains an
OS-provided, non-mirrored adapter dependency.
