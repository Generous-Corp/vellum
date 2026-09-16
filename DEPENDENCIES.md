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
| `skia-build-mac-universal-gpu-release.zip` | `danielraffel/skia-builder` release `chrome/m153`; SHA-256 `0ebfe03a209ceefe47edfeae70c3cc6c499583b74f35a26140ea55bad7f1e5a9` | Build-time Skia Graphite and Dawn/Metal static libraries and headers |

The artifact was produced from builder commit
`1f8c8d2c343f360a653bce92d11f8ded9a515208`. Its declared source tuple is
Skia commit `8b8c3872fbc03f025855db96ce683f34ec98a815` and Dawn commit
`f91da75afe31d4d6f47a6da307e1fbabd1b1691a`. The Dawn commit is embedded in
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
| Skia, including skcms | `8b8c3872fbc03f025855db96ce683f34ec98a815` | BSD-3-Clause | Skia/core/Graphite/skcms object members |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | `libexpat.*` members |
| libjpeg-turbo | `e14cbfaa85529d47f9f55b0f104a579c1061f9ad` (Chromium's 3.1.0 snapshot) | IJG and zlib for the packaged libjpeg API/SIMD code | `libjpeg.*`, `libjpeg12.*`, and `libjpeg16.*` members; no TurboJPEG API member was identified |
| libpng | `d5515b5b8be3901aac04e5bd8bd5c89f287bcd33` (1.6.56) | Libpng-2.0 | `libpng.*` members |
| libwebp | `845d5476a866141ba35ac133f856fa62f0b7445f` | BSD-3-Clause | `libwebp.*` and `libwebp_sse41.*` members |
| Wuffs | `e3f919ccfe3ef542cfc983a82146070258fb57f8` | Apache-2.0 | `libwuffs.wuffs-v0.3.o` |
| Chromium zlib | `646b7f569718921d7d4b5b8e22572ff6c76f2596` (`1.3.0.1-motley`) | zlib | `libzlib.*` and `zlib_*` members |

### Code packaged inside `libdawn_combined.a`

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Dawn and Tint | `f91da75afe31d4d6f47a6da307e1fbabd1b1691a` | BSD-3-Clause | Dawn/Tint objects and symbol namespaces; exact revision embedded in `dawn_version.h` |
| Abseil | `dd67f5ca84f65ebb88ac0ea0fe2c1d58663e519f` | Apache-2.0 | `absl::` symbols and implementation members including `raw_hash_set.cc.o` and `crc32c.cc.o` |
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
source boundary is `provenance/active-source-boundary.json`.
