# Changelog

## 0.2.0

The UI framework units that Vellum used to carry as a copy of Pulp are now
Vellum's own, under the `vellum` name. Their source moved from `mirror/` to
`framework/`.

What a consumer changes:

- C++ namespaces: `pulp::<unit>` is now `vellum::<unit>` (for example
  `pulp::view` becomes `vellum::view`, and `pulp::canvas` becomes
  `vellum::canvas`).
- Headers: `#include <pulp/<unit>/...>` is now `#include <vellum/<unit>/...>`.
- CMake: the aliases `pulp::canvas`, `pulp::runtime` and `pulp::foundation`
  are now `vellum::canvas`, `vellum::runtime` and `vellum::foundation`.

There are no compatibility aliases for the old names. The underlying CMake
target names (`pulp-canvas`, `pulp-runtime` and so on) are unchanged in this
release; link the `vellum::` aliases rather than those names.
