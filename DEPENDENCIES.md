# Dependencies

This file describes Vellum's active source, build, and redistributed dependency
surface. The filtered Git history contains older Pulp paths for provenance;
those paths are not automatically active Vellum dependencies. Only dependencies
reachable from a Vellum target, tool, installed SDK, or release artifact belong
in the active inventory.

Vellum does not include audio, MIDI, DSP, plug-in formats, plug-in hosting, or
audio-device dependencies.

## Active renderer dependency

The macOS arm64 GPU proof uses one byte-locked prebuilt renderer artifact:

| Artifact | Locked identity | Use |
|---|---|---|
| `skia-build-mac-arm64-gpu-release.zip` | `danielraffel/skia-builder` release `chrome/m150`; SHA-256 `13b0e9818c3b05db661af85cb1e2bf2ef10e30d468b81351dd90295237d17734` | Build-time Skia Graphite and Dawn/Metal static libraries and headers |

The artifact was produced from builder commit
`9b0215b1b2f4f3f06bac493c7e43fdeb0d35f9bc`. Its declared source tuple is
Skia commit `587c5b0f5a7b0260826a0c19094c2d952195066e` and Dawn commit
`63f25feec51e9351fb25222b6d5de1af791d7c4f`. The Dawn commit is embedded in
`build/include/dawn/dawn_version.h`; the exact Skia and builder commits are not
embedded in the artifact.

The artifact SHA-256, all nine static-archive SHA-256 values, and a
construction-specific digest over all 805 header files are locked in
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
| Skia, including skcms | `587c5b0f5a7b0260826a0c19094c2d952195066e` | BSD-3-Clause | Skia/core/Graphite/skcms object members |
| Expat | `6154446fccefbf3ca644894f598969113b0c7bcd` | MIT | `libexpat.*` members |
| libjpeg-turbo | `e14cbfaa85529d47f9f55b0f104a579c1061f9ad` (Chromium's 3.1.0 snapshot) | IJG and zlib for the packaged libjpeg API/SIMD code | `libjpeg.*`, `libjpeg12.*`, and `libjpeg16.*` members; no TurboJPEG API member was identified |
| libpng | `d5515b5b8be3901aac04e5bd8bd5c89f287bcd33` (1.6.56) | Libpng-2.0 | `libpng.*` members |
| libwebp | `845d5476a866141ba35ac133f856fa62f0b7445f` | BSD-3-Clause | `libwebp.*` and `libwebp_sse41.*` members |
| Wuffs | `e3f919ccfe3ef542cfc983a82146070258fb57f8` | Apache-2.0 | `libwuffs.wuffs-v0.3.o` |
| Chromium zlib | `646b7f569718921d7d4b5b8e22572ff6c76f2596` (`1.3.0.1-motley`) | zlib | `libzlib.*` and `zlib_*` members |

### Code packaged inside `libdawn_combined.a`

| Name | Exact source identity | License | Binary evidence |
|---|---|---|---|
| Dawn and Tint | `63f25feec51e9351fb25222b6d5de1af791d7c4f` | BSD-3-Clause | Dawn/Tint objects and symbol namespaces; exact revision embedded in `dawn_version.h` |
| Abseil | `d16e32215c3ab90ba57c2e904a5344d85c7353e4` | Apache-2.0 | `absl::` symbols and implementation members including `raw_hash_set.cc.o` and `crc32c.cc.o` |
| PartitionAlloc | `76c74af3a92809278b20d6816865a296d4704ca6` | BSD-3-Clause | `partition_alloc::` symbols and allocator implementation members including `low_level_alloc.cc.o` |

HarfBuzz and ICU are present in separate static archives in the toolchain
asset, but they were not found inside `libskia.a` or `libdawn_combined.a`.
Archive membership also does not prove that every packaged member survives the
static linker's dead-code selection in a particular `vellum-gpu` binary.

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
| `libskia.a` | 11.0 | 15.5 |
| `libdawn_combined.a` | 15.0 | 15.5 |

The strictest observed minimum is therefore macOS 15.0. These are observations
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
