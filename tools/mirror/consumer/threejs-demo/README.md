# Native Three.js demo

Build the demo against an installed macOS arm64 Vellum SDK configured with
`VELLUM_ENABLE_V8=ON`, `VELLUM_ENABLE_MIRROR=ON`, and the pinned
Skia/Dawn archive. The demo loads Three.js and its addons from the SDK's
`VELLUM_THREEJS_ROOT`.

From the repository root, with `sdk_prefix` pointing at the installed SDK:

```sh
cmake -S tools/mirror/consumer/threejs-demo -B build-threejs-demo \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$sdk_prefix"
cmake --build build-threejs-demo
ctest --test-dir build-threejs-demo --output-on-failure
```

Run `build-threejs-demo/vellum-threejs-native-demo --demo cube` for the
interactive cube or use `--demo gltf-box` for the textured glTF box.
The glTF mode stages its fixture in a per-run directory under the demo build's
`assets` directory and removes the fixture on exit. The build directory must
be writable.
Add `--capture box.png` to write a PNG from the hidden window's GPU back buffer.

The cube and glTF capture tests skip if Dawn has no real adapter. On a GPU
host, configure with `-DVELLUM_THREEJS_REQUIRE_CAPTURE=ON` to require both
PNGs. The headless CTests check the V8 provider, load the Three.js modules and
addons, fetch the staged fixture through WidgetBridge, and verify the demo
reports an unwritable staging path with a normal error exit.
