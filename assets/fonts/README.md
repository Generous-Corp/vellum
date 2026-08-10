# Packaged fonts

Vellum installs these exact faces under `share/vellum/fonts` so native text
measurement and paint do not depend on fonts present on the host. The GPU
library resolves that directory relative to its own installed location; the
build tree uses this source directory. `SkiaDawnSurface::Config::font_directory`
and `SkiaDawnSurface::measure_text` accept an explicit override for tests and
specialized embedding.

| File | Version | SHA-256 | License | Source |
|---|---|---|---|---|
| `Inter-Regular.ttf` | `4.001;git-9221beed3` | `40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82` | SIL OFL 1.1 | https://github.com/rsms/inter |
| `Jost-Regular.ttf` | `3.710` | `c3143e923ed1ca7bdf27f96c351fbafaebcbd3cf3f4c2d30d03e6c7f98e73d7a` | SIL OFL 1.1 | https://github.com/indestructible-type/Jost |
| `Jost-Medium.ttf` | `3.710` | `d6ff7726ec21576cf2fdac55080b2d43832780fa981f03f0b66d2723a7c1ea09` | SIL OFL 1.1 | https://github.com/indestructible-type/Jost |
| `Jost-SemiBold.ttf` | `3.710` | `a63c8d75600a2d42e0e152e4c4810474a90a0b93206f47530a741dbb78a9e571` | SIL OFL 1.1 | https://github.com/indestructible-type/Jost |
| `Jost-Bold.ttf` | `3.710` | `3e49280c154002dcbab4344a77ad291d5587d4157b24b5a02341f68cccd24615` | SIL OFL 1.1 | https://github.com/indestructible-type/Jost |
| `NotoSansJP[wght].ttf` | `google/fonts@038b637da7b3fd956a4ed93ffc607c3d5e4ce172` | `c2f3b4d463500a2ddcd3849cded1fceeb9fd6d1c32e6cbecd568453ba50fc68f` | SIL OFL 1.1 | https://github.com/google/fonts/tree/038b637da7b3fd956a4ed93ffc607c3d5e4ce172/ofl/notosansjp |
| `NotoSansArabic[wdth,wght].ttf` | `google/fonts@038b637da7b3fd956a4ed93ffc607c3d5e4ce172` | `63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e` | SIL OFL 1.1 | https://github.com/google/fonts/tree/038b637da7b3fd956a4ed93ffc607c3d5e4ce172/ofl/notosansarabic |

Inter is the default face. Jost requests select the nearest packaged 400, 500,
600, or 700 face. Characters absent from a requested face fall back only to the
packaged variable Noto Sans Arabic, Noto Sans JP, and Inter families; host font
databases are not part of measurement or paint. Copyright notices and the full
SIL OFL 1.1 text are in the repository and installed `NOTICE.md`.
Installed native app builds copy these seven faces into
`Contents/Resources/vellum/fonts`; the relocated GPU library resolves that
bundle-local directory before the SDK install prefix.
Packaged apps also carry `LICENSE.md`, `NOTICE.md`, and `DEPENDENCIES.md` under
`Contents/Resources/vellum/legal` for the redistributed runtime and font stack.
