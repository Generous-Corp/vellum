# Minimal installed-SDK scene

This is Vellum's single source-free CMake consumer. It uses only the installed
package, renders a non-blank GPU scene when `Vellum::Gpu` is present, and keeps
a runtime-only fallback so package relocation can be checked in smaller builds.

```sh
cmake -S apps/minimal-scene -B build/minimal-scene \
  -DCMAKE_PREFIX_PATH="$VELLUM_SDK_ROOT" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/minimal-scene --parallel
ctest --test-dir build/minimal-scene --output-on-failure
```

For a sterile downstream check, copy this directory outside the checkout and
run the same commands against an installed SDK prefix.
