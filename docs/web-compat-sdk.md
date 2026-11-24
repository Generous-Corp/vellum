# Web-compat SDK package

Vellum 0.2.3 publishes the web-compat resources needed by its scripted UI
consumers under `lib/cmake/Vellum/web-compat/`. The package contains the
versioned prelude set, `embed_js.py`, `VellumWebCompatPackage.cmake`, and a
SHA-256 manifest. The four host and harness scripts remain available at
`lib/cmake/Vellum/scripts/` for existing consumers.

An installed consumer calls `vellum_embed_web_compat_preludes(<output.cpp>
<output.hpp>)` after `find_package(Vellum CONFIG REQUIRED)`. The helper checks
that every manifest prelude is installed, writes declarations, and adds a
custom command that generates the compressed C++ definitions.

The package is consumed by Pulp only through `integrations/pulp-vellum`.
Pulp core does not find, fetch, or link Vellum.
