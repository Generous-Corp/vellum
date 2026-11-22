# RmlUi plugin-host follow-up

This slice adds `vellum::rmlui::PluginView`, which lets the existing Windows
and Linux `PluginViewHost` implementations host a CPU-rendered RmlUi document.
The adapter forwards pointer, wheel, key, text, and leave events and paints the
CPU frame through the host canvas. macOS `NativeParentHost` and its tests are
unchanged.

The GPU renderer choices remain follow-up work. That includes the Vellum
Skia/Dawn renderer and RmlUi's upstream DirectX, OpenGL, Vulkan, SDL, and SDL
GPU backends. The adapter currently encodes the CPU frame as PNG for the host
canvas, which is intentional for this first cross-platform proof.

Run the Linux X11 contract under the same virtual display used by the existing
host tests:

```sh
cd /Volumes/Atelier/Code/agent-worktrees/lanes/w-rmlui-hosts/repo
cmake -S . -B build-linux-rmlui -DVELLUM_ENABLE_MIRROR=ON -DVELLUM_ENABLE_RMLUI=ON -DVELLUM_RMLUI_RENDERER=cpu -DVELLUM_BUILD_TESTS=ON
xvfb-run -a --server-args='-screen 0 1280x800x24' cmake --build build-linux-rmlui --target vellum-rmlui-plugin-view-test
xvfb-run -a --server-args='-screen 0 1280x800x24' ctest --test-dir build-linux-rmlui -R '^vellum\.rmlui\.plugin-view$' --output-on-failure
```

Run the Windows contract in the Windows VM from a checked-out copy of this
branch:

```powershell
cmake -S . -B build-win-rmlui -DVELLUM_ENABLE_MIRROR=ON -DVELLUM_ENABLE_RMLUI=ON -DVELLUM_RMLUI_RENDERER=cpu -DVELLUM_BUILD_TESTS=ON
cmake --build build-win-rmlui --target vellum-rmlui-plugin-view-test --config Release
ctest --test-dir build-win-rmlui -C Release -R '^vellum\.rmlui\.plugin-view$' --output-on-failure
```

The lane has no GPU. A lead run with a real Windows or Linux GPU should add a
pixel-level host proof before enabling any GPU renderer in this adapter.
