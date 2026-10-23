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
| `skia-build-mac-universal-gpu-release.zip` | `danielraffel/skia-builder` release `chrome/m157`; SHA-256 `bb24c74f05699fa084df6d68ecb9a427802c3df49ce85d8a7dc696aeb9e19a86` | Build-time Skia Graphite and Dawn/Metal static libraries and headers |

The artifact was produced from builder commit
`f710b2834eaf37dbf1bd39408f9361d10d85eda2`. Its declared source tuple is
Skia commit `b94213a11b3a2edadfae89f0c57a7f4ab72772c9` and Dawn commit
`ada567700e13aecf7989d78b21f7d330b522ea80`. The Dawn commit is embedded in
`build/include/dawn/dawn_version.h`; the exact Skia and builder commits are not
embedded in the artifact.

The artifact SHA-256, all eleven static-archive SHA-256 values, and a
construction-specific digest over all 809 header files are locked in
[`provenance/third-party-lock.json`](provenance/third-party-lock.json). Release
construction must verify those bytes before configuring Vellum. An arbitrary
local `VELLUM_SKIA_DIR` is a development convenience, not release provenance.

Selected archive members are statically linked into Vellum's installed
`vellum-gpu` shared library. Consumers do not need a Skia checkout or receive
the prebuilt static archives.

### Code packaged inside `libskia.a`

The sealed archive's member table demonstrates that `libskia.a` packages the
following observed upstream code. These are not inferred merely from Skia's
full DEPS file; each row has matching object members in the locked archive.

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Skia, including skcms | `b94213a11b3a2edadfae89f0c57a7f4ab72772c9` | BSD-3-Clause | Skia/core/Graphite/skcms object members |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | `libexpat.*` members |
| libjpeg-turbo | `e14cbfaa85529d47f9f55b0f104a579c1061f9ad` (Chromium's 3.1.0 snapshot) | IJG and zlib for the packaged libjpeg API/SIMD code | `libjpeg.*`, `libjpeg12.*`, and `libjpeg16.*` members; no TurboJPEG API member was identified |
| libpng | `d5515b5b8be3901aac04e5bd8bd5c89f287bcd33` (1.6.56) | Libpng-2.0 | `libpng.*` members |
| libwebp | `845d5476a866141ba35ac133f856fa62f0b7445f` | BSD-3-Clause | `libwebp.*` and `libwebp_sse41.*` members |
| Wuffs | `e3f919ccfe3ef542cfc983a82146070258fb57f8` | Apache-2.0 | `libwuffs.wuffs-v0.3.o` |
| Chromium zlib | `646b7f569718921d7d4b5b8e22572ff6c76f2596` (`1.3.0.1-motley`) | zlib | `libzlib.*` and `zlib_*` members |

### Code packaged inside `libdawn_combined.a`

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Dawn and Tint | `ada567700e13aecf7989d78b21f7d330b522ea80` | BSD-3-Clause | Dawn/Tint objects and symbol namespaces; exact revision embedded in `dawn_version.h` |
| Abseil | `af5aede14a0b4013be6890148d34e0fd7b1fac69` | Apache-2.0 | `absl::` symbols and implementation members including `raw_hash_set.cc.o` and `crc32c.cc.o` |
| PartitionAlloc | `03cc513177b4340bee3dbfd46f6dd5fdded43b79` | BSD-3-Clause | `partition_alloc::` symbols and allocator implementation members including `low_level_alloc.cc.o` |

Vellum also links the byte-locked `libskparagraph.a`, `libskshaper.a`,
`libskunicode_core.a`, and `libskunicode_icu.a` archives into `vellum-gpu` to
provide paragraph-level attributed text, shaping, bidi, and Unicode fallback.
The CMake integration checks each archive against the digest already recorded
in the lock before linking it. HarfBuzz and ICU implementation objects are
packaged in these separate archives rather than `libskia.a` or
`libdawn_combined.a`; archive membership does not prove that every member
survives the static linker's dead-code selection in a particular binary.

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

This observed list is **not an exhaustive legal/SBOM claim**. The sealed asset
does not include a build graph, source-to-object map, SBOM, or license
manifest. Static archive inspection positively identifies the rows above but
cannot prove that no additional third-party implementation was incorporated
under a generic object name. GPU release eligibility therefore remains blocked
until the release-producing builder emits and attests an exhaustive transitive
source/license manifest.

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

## Developer prerequisites

CMake, a C++20 compiler, Python 3, Node.js, and npm are developer-supplied
tools. The macOS proof additionally uses Xcode command-line tools and Apple
system frameworks. These tools and frameworks are not bundled or redistributed
by Vellum.

## Historical extraction material

The filtered seed once placed 232 projected source and vendored files in the
working tree. Those paths are deleted from the active tip. Their exact
historical identities remain in `provenance/cut-manifest.json` and Git history;
they are not build inputs, installed files, or release payloads. The active
source boundary is `provenance/active-source-boundary.json`. Mirrored Pulp
framework source admitted by Decision 0010 is recorded blob by blob in
`provenance/mirror/blob-origins.json`; none is present yet.

## Planned mirrored framework inventory

The following rows are the legal inventory for bytes that may arrive under
`mirror/` from the D3 units and the foundation amendment. They are scoped to
the mirror root and do not make the corresponding historical Pulp paths active
Vellum dependencies. Versions and licenses were cross-checked against Pulp's
`DEPENDENCIES.md`, `NOTICE.md`, and `tools/deps/manifest.json` on 2026-10-10.

| Dependency | Version or source identity | License | Mirror scope | Compatibility / handling |
|---|---|---|---|---|
| Abseil | `af5aede14a0b4013be6890148d34e0fd7b1fac69` | Apache-2.0 | not mirrored; no landed unit consumes it yet (planned renderer and V8 units) | MIT-compatible; transitive notice |
| Brotli | `v1.2.0` | MIT | `canvas` unit external input (`mirror/core/canvas/`, WOFF2 decoder); bytes not mirrored | MIT-compatible |
| CHOC | `pulp-webview-dnd-poc1` (`df148a41`) | ISC | `foundation` unit external input, patched (`mirror/core/runtime/`, `mirror/core/events/`); bytes not mirrored | MIT-compatible; retain fork attribution |
| cpp-httplib | vendored snapshot | MIT | `mirror/external/cpp-httplib/` (`foundation` unit) | MIT-compatible |
| Dawn and Tint | `ada567700e13aecf7989d78b21f7d330b522ea80` | BSD-3-Clause | `canvas` unit external input: headers inside the Skia build tree; bytes not mirrored | MIT-compatible; renderer notice exists |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | not mirrored; no landed unit consumes it yet (planned renderer units) | MIT-compatible; renderer notice exists |
| HarfBuzz | `9cb1fee51069b206effb4736e443b038d230789d` | Old MIT | `canvas` unit external input through the Skia build (text shaping); bytes not mirrored | MIT-compatible; notice exists |
| Highway | `1.2.0` | Apache-2.0 | `foundation` unit external input (`mirror/core/simd/`); bytes not mirrored | MIT-compatible |
| ICU | `d578f2e8b7bd5938e21cfb6bf15c079e0aa5b738` | Unicode-3.0 plus bundled notices | `canvas` unit external input through the Skia build (text shaping); bytes not mirrored | Permissive; notice exists |
| Inter | `4.001; git-9221beed3` | SIL OFL 1.1 | `mirror/external/fonts/` (`canvas` unit) | Allowed font license |
| JavaScriptCore | Apple system framework | LGPL-2.1+ components and Apple terms | Never mirrored; system framework only | **Not MIT-compatible**; system-only, never mirrored |
| JetBrains Mono | `2.304` | SIL OFL 1.1 | `mirror/external/fonts/` (`canvas` unit) | Allowed font license |
| Jost | `3.710` | SIL OFL 1.1 | `mirror/external/fonts/` (`canvas` unit) | Allowed font license |
| Mbed TLS | `v3.6.2` | Apache-2.0 | `foundation` unit external input (`mirror/core/runtime/`); bytes not mirrored | MIT-compatible |
| miniz | `3.0.2` | MIT | `mirror/external/miniz/` (`foundation` unit) | MIT-compatible |
| pugixml | `1.15` | MIT | `mirror/external/pugixml/` (`foundation` unit) | MIT-compatible |
| TweetNaCl | `20140427` | Public domain | `mirror/external/tweetnacl/` (`foundation` unit) | Public-domain code |
| WOFF2 | `fb9c3379f260` | MIT | `canvas` unit external input (`mirror/core/canvas/`); bytes not mirrored | MIT-compatible |
| NanoSVG | vendored snapshot | zlib | `mirror/external/nanosvg/` (`canvas` unit) | MIT-compatible |
| Noto Sans Arabic | variable font | SIL OFL 1.1 | not mirrored; no landed unit consumes it yet | Allowed font license |
| Noto Sans JP | variable font | SIL OFL 1.1 | not mirrored; no landed unit consumes it yet | Allowed font license |
| PartitionAlloc | `03cc513177b4340bee3dbfd46f6dd5fdded43b79` | BSD-3-Clause | not mirrored; no landed unit consumes it yet (planned GPU and renderer units) | MIT-compatible; transitive notice |
| QuickJS | `2024-02-14` | MIT | not mirrored; no landed unit consumes it yet (planned scripted-view unit) | MIT-compatible |
| SheenBidi | `v3.0.0` | Apache-2.0 | `canvas` unit external input (`mirror/core/canvas/`); bytes not mirrored | MIT-compatible |
| Skia and skcms | `b94213a11b3a2edadfae89f0c57a7f4ab72772c9` | BSD-3-Clause | `canvas` unit external input (`mirror/core/canvas/`); bytes not mirrored | MIT-compatible; renderer notice exists |
| V8 | `v8-m153-15.3.76.5-26cef0256b0e` | BSD-3-Clause | not mirrored; no landed unit consumes it yet (planned scripted-view unit, when selected) | MIT-compatible; sealed provider |
| Yoga | `3.2.1` | MIT | not mirrored; no landed unit consumes it yet (planned view units) | MIT-compatible |

### Units built into the native SDK

The macOS arm64 GPU SDK compiles the landed `foundation` and `canvas` units
into `sdk/mirror/lib/libvellum-mirror-foundation.dylib` and
`sdk/mirror/lib/libvellum-mirror-canvas.dylib`
(`tools/mirror/VellumMirror.cmake`). The units' external inputs above are
fetched at the commits their closure manifests pin, and every file a closure
lists is checked against its recorded SHA-256 before configure
(`tools/mirror/mirror_package.py`). Highway, Mbed TLS, SheenBidi, Brotli, and
WOFF2 objects are linked into those libraries; CHOC headers that the public
headers include ship under `sdk/mirror/include/choc/`. Each fetched
dependency's license file is installed under `sdk/mirror/share/licenses/`. The
canvas library links the locked Skia/Dawn archive above privately, exports only
the mirrored namespace, and declares itself a Skia/Dawn provider, so a consumer
cannot link it and `Vellum::Gpu` into one image.

Pulp's external contributor `matthewfudge` authored four mirrored view files:
`core/view/src/design_frame_view.cpp`,
`core/view/include/pulp/view/widget_bridge.hpp`,
`core/view/src/widget_bridge/state_binding_api.cpp`, and
`core/view/src/widget_bridge/widget_callbacks.cpp`. The history-preserving
mirror retains those author records; the inbound and outbound license is MIT,
so no separate relicensing or waiver is required. JavaScriptCore is the only
listed dependency with a non-MIT-compatible license concern, and it remains an
OS-provided, non-mirrored adapter dependency.
