# Follow-up platform closure

This lane proves the macOS RmlUi PluginView bridge and its mirror link boundary. Windows and Linux remain gated because the mirror view unit is not yet packaged and consumed through verified platform closures on those targets.

The next PR should package `Vellum::MirrorView_core` and its public headers for Windows and Linux, add installed-consumer coverage for each platform, and run the platform-native RmlUi CTests with the mirror enabled. Keep the platform work separate from this folder-level provenance exemption.

# RmlUi idle follow-up

The origin regression test is committed as `3058e8ee8`. The required native-parent-host test could not be executed in this lane because `~/bin/leased-build --wait-secs 60 -- cmake -S rmlui -B build-rmlui -DVELLUM_RMLUI_NATIVE=ON -DVELLUM_RMLUI_CPU=ON -DVELLUM_RMLUI_NATIVE_HOST=ON -DVELLUM_RMLUI_BUILD_TESTS=ON -DVELLUM_RMLUI_FONTS=OFF -DCMAKE_BUILD_TYPE=Release` returned `leased-build: no lease within 60s (cores=8)` before configuring. On origin, the new assertion is expected to fail because every idle tick replaces the layer contents.

`rmlui/src/metal_image_decode.mm` decodes synchronously through ImageIO and draws into a bitmap context on its caller's thread. The file has no queue or cancellation seam, so image decode can block the UI thread and delay close. This lane leaves it unchanged because moving ownership and lifetime off-thread is larger than a small safe fix. A lead-owned follow-up should add an explicit decode queue and close cancellation before changing this path.

The idle/presentation behavior needs a macOS host run. The lead should run the native-parent-host test and an animation fixture on a GPU-capable macOS machine, plus the installed native-package consumer proof. The lane cannot claim those results.

The required control was performed locally with `git revert --no-edit --no-commit acb08a736`: the only fix files disappeared from the worktree while the regression test remained. The control test could not be executed because the same build lease was unavailable, so no passing control result is claimed.

The requested texture grace-period cache is not implemented in this slice. The current native host already retains the RmlUi context and its textures across `detach()` and hidden visibility. `invalidate()` is terminal and closes the context, so adding delayed release there requires a reopen/load lifecycle and a fake-clock seam. That belongs in the next coherent change with a Metal texture-cache test.

`scripts/gates.sh origin/main` was attempted after removing stray `.DS_Store` files. It stopped before repository checks because the sandbox rejected its fallback temporary worktree location (`/Users/danielraffel/agent-worktrees` is not writable). No gate result is claimed.

Lead run list, from this checkout after a macOS GPU-capable configure with the same RmlUi options:

```sh
~/bin/leased-build --wait-secs 3600 -- cmake --build build-rmlui --target vellum-rmlui-native-parent-host-test
ctest --test-dir build-rmlui --output-on-failure -R '^vellum\\.rmlui\\.native-parent-host$'
ctest --test-dir build-rmlui --output-on-failure -R '^vellum\\.rmlui\\.install-consumer-native-package$'
```

The lead should also run the animation-specific host fixture covering zero idle presents, one present after a DOM/data change, and presents on each animation tick until completion. This lane contains no GPU result.

## Android GPU surface slice baseline

The required contract test was added before the implementation and failed on the lane baseline (origin/lane/w-android-host, commit e512185ab):

```text
CMake Error at tools/android/test_android_gpu_surface.cmake:14 (message):
  android-gpu-surface: missing framework/core/render/platform/android/android_gpu_surface.hpp
```
