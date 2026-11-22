# Windows and Linux PluginView follow-up

This lane is based on `lane/w-rmlui-hosts-3` at `ca11bb7abfe54ec1a2dc570489e29eef78726d80`, which is PR #686's branch. It keeps macOS packaging unchanged and makes the mirror unit packaging path portable for the Windows and Linux platform records.

The platform closure records still need to be produced from the exact Pulp commit by the platform closure exporter and checked into `provenance/framework/platforms/`. The records must cover `windows-x64`, `windows-arm64`, and `linux-x64`, use the renderer lock asset for each platform, and include the `pulp-view-core` host sources already scoped in `provenance/framework/ports.json`.

## Linux VM

From the Vellum checkout, after the `linux-x64` record is present:

```sh
cmake -S . -B build-rmlui-linux -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DVELLUM_BUILD_TESTS=ON \
  -DVELLUM_ENABLE_MIRROR=ON \
  -DVELLUM_ENABLE_GPU=ON \
  -DVELLUM_ENABLE_GRAPHICS=ON \
  -DVELLUM_ENABLE_AUTHORING=OFF \
  -DVELLUM_ENABLE_RMLUI=ON \
  -DVELLUM_RMLUI_BUILD_TESTS=ON \
  -DVELLUM_BUILD_SMOKE_NATIVE=OFF
~/bin/leased-build --wait-secs 3600 -- cmake --build build-rmlui-linux
xvfb-run -a dbus-run-session -- ctest --test-dir build-rmlui-linux -L linux-host --output-on-failure
```

The `vellum.rmlui.plugin-view` contract is included in `linux-host` and should run in the same Xvfb invocation as the existing Linux host tests.

## Windows VM

Run the same configure and build for both architectures after the corresponding record is present:

```sh
cmake -S . -B build-rmlui-windows-x64 -A x64 \
  -DCMAKE_BUILD_TYPE=Release \
  -DVELLUM_BUILD_TESTS=ON \
  -DVELLUM_ENABLE_MIRROR=ON \
  -DVELLUM_ENABLE_GPU=ON \
  -DVELLUM_ENABLE_GRAPHICS=ON \
  -DVELLUM_ENABLE_AUTHORING=OFF \
  -DVELLUM_ENABLE_RMLUI=ON \
  -DVELLUM_RMLUI_BUILD_TESTS=ON \
  -DVELLUM_BUILD_SMOKE_NATIVE=OFF
~/bin/leased-build --wait-secs 3600 -- cmake --build build-rmlui-windows-x64 --config Release
ctest --test-dir build-rmlui-windows-x64 -C Release -R '^vellum\\.rmlui\\.plugin-view$' --output-on-failure

cmake -S . -B build-rmlui-windows-arm64 -A ARM64 \
  -DCMAKE_BUILD_TYPE=Release \
  -DVELLUM_BUILD_TESTS=ON \
  -DVELLUM_ENABLE_MIRROR=ON \
  -DVELLUM_ENABLE_GPU=ON \
  -DVELLUM_ENABLE_GRAPHICS=ON \
  -DVELLUM_ENABLE_AUTHORING=OFF \
  -DVELLUM_ENABLE_RMLUI=ON \
  -DVELLUM_RMLUI_BUILD_TESTS=ON \
  -DVELLUM_BUILD_SMOKE_NATIVE=OFF
~/bin/leased-build --wait-secs 3600 -- cmake --build build-rmlui-windows-arm64 --config Release
ctest --test-dir build-rmlui-windows-arm64 -C Release -R '^vellum\\.rmlui\\.plugin-view$' --output-on-failure
```

The Windows commands are compile and contract coverage only. A real GPU-backed surface and DAW/editor embedding still need a GPU-capable lead run.
