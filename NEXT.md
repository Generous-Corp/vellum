# RmlUi PluginView validation

`VellumRmlUi::PluginView` exposes a CPU-rendered RmlUi document as a
Vellum view. The headless test renders the document and records its paint
command. Native child-window attachment, input delivery, and display still
need a device run. Pixel output through a GPU-backed host needs a GPU run by
the lead.

## Windows and Linux VM prerequisites

This revision's mirror packager supports Darwin only. Windows and Linux
need verified platform closure records, matching locked Skia/Dawn archives,
and mirror packaging that provides `Vellum::MirrorView_core`. The CPU
setting selects the RmlUi document renderer. The mirror host still needs
its platform Skia/Dawn build. These VM commands apply once those
prerequisites are present. Set `VELLUM_SKIA_ARCHIVE` to the matching
archive on the VM and run from the checkout root.

Linux, under the VM's build lease:

```sh
cmake -S . -B build-linux-rmlui -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DVELLUM_ENABLE_MIRROR=ON -DVELLUM_ENABLE_GRAPHICS=ON -DVELLUM_ENABLE_GPU=ON \
  -DVELLUM_ENABLE_RMLUI=ON -DVELLUM_RMLUI_RENDERER=cpu -DVELLUM_BUILD_TESTS=ON \
  -DVELLUM_SKIA_ARCHIVE="$VELLUM_SKIA_ARCHIVE"
cmake --build build-linux-rmlui --target vellum-rmlui-plugin-view-test
ctest --test-dir build-linux-rmlui --no-tests=error \
  -R '^vellum[.]rmlui[.]plugin-view$' --output-on-failure
```

Windows, in an MSVC developer PowerShell under the VM's build lease:

```powershell
cmake -S . -B build-win-rmlui -G Ninja -DCMAKE_BUILD_TYPE=Release `
  -DVELLUM_ENABLE_MIRROR=ON -DVELLUM_ENABLE_GRAPHICS=ON -DVELLUM_ENABLE_GPU=ON `
  -DVELLUM_ENABLE_RMLUI=ON -DVELLUM_RMLUI_RENDERER=cpu -DVELLUM_BUILD_TESTS=ON `
  "-DVELLUM_SKIA_ARCHIVE=$env:VELLUM_SKIA_ARCHIVE"
cmake --build build-win-rmlui --target vellum-rmlui-plugin-view-test
ctest --test-dir build-win-rmlui --no-tests=error `
  -R '^vellum[.]rmlui[.]plugin-view$' --output-on-failure
```
