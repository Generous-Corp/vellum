# Web-compat SDK package

Vellum 0.2.3 publishes the web-compat resources its scripted UI consumers
need:

- `mirror/share/web-compat/preludes/`: the versioned prelude set. These are
  mirrored framework sources and keep their upstream names, so they install
  under the mirror scope.
- `lib/cmake/Vellum/web-compat/`: `vellum_embed_preludes.py`,
  `VellumWebCompatPackage.cmake`, and `vellum-web-compat-manifest.json`, which
  records the SHA-256 of every prelude and script.
- `lib/cmake/Vellum/scripts/`: the four host and harness scripts for existing
  consumers.

An installed consumer calls, after `find_package(Vellum CONFIG REQUIRED)`:

```cmake
vellum_embed_web_compat_preludes(<output.cpp> <output.hpp>
    NAMESPACE <ns> INFLATE_HEADER <include> INFLATE_FUNCTION <fn>)
```

`NAMESPACE` is the namespace of the generated declarations and definitions.
The declaration header is written under the file name of `<output.hpp>`, only
when its content changes, and the generated source includes it by that name.
`INFLATE_FUNCTION` (declared by `INFLATE_HEADER`) must be callable as
`std::optional<std::string>(const uint8_t*, size_t, size_t)`; the generated
source defines `source()` on top of it, so the consumer links without a
separate definition. The call fails at configure time when a prelude or script
named in the manifest is missing or its SHA-256 differs, and a changed install
re-runs configure.

The package is consumed by Pulp only through `integrations/pulp-vellum`.
Pulp core does not find, fetch, or link Vellum. The prelude list matches the
framework's embedded list in order; `scripts/test_web_compat_packaging.py`
fails when the two drift.
