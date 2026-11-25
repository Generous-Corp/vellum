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

# Pulp design cutover map

The three rows map to Vellum owners as follows.

| Pulp row | Pulp capability | Vellum owner | Cutover shape |
| --- | --- | --- | --- |
| C2 | `tools/import-design/` and the `pulp-import-design` executable | `packages/vellum-design-ir/`, `authoring/design_pipeline/`, and the installed `vellum` import backend in `cli/vellum_cli.py` | Keep the import executable contract at the Vellum CLI boundary. Move source adapters and DesignIR normalization into the Vellum design-import package, with the native pipeline as the C++ consumer. |
| C7 | `test/fixtures/emitter-conformance/` and `test/test_design_emitter_conformance.cpp` | `framework/core/view/src/design_import*`, `framework/core/view/src/design_*_codegen.cpp`, and `authoring/design_pipeline/tests/` | Keep one DesignIR fixture set and compare the native materializer, bridge JavaScript, baked C++, and SwiftUI lowerings in Vellum's design-import test lane. |
| C14 | `tools/cli/cmd_design_subcommands.cpp`, `tools/design/pulp_design_debug.cpp`, and `tools/ui-build/` | `cli/vellum_cli.py`, `packages/vellum-design-ir/bin/`, `packages/vellum-ui/`, and the `vellum` installed CLI surface | Keep the design command family in Vellum and have commands forward to the installed design-import backend and UI build tooling. |

C7 is the first implementation slice. The C2 and C14 moves remain planned until the conformance gate has a Vellum owner and test lane.

# iOS Metal renderer lane

The baseline test commit `dce2f4110` registered
`vellum.rmlui.metal-ios-compile` before its target existed. On the baseline,
CTest failed with:

```
Error: /private/tmp/rmlui-ios-test/rmlui is not a directory
0% tests passed, 1 tests failed out of 1
```

The fixed simulator consumer configures with arm64 and compiles
`vellum-rmlui-metal`, plus the Metal test translation units, as static
libraries. The Cocoa negative control passes because Cocoa is unavailable in
the iOS SDK. No simulator runtime or GPU pixel test ran in this lane, so
`rmlui/renderers.json` remains `planned` for iOS.

Lead runtime commands, after configuring a runtime-capable iOS test build:

```sh
xcrun simctl list devices available
xcrun simctl boot "iPhone 16 Pro"
xcrun simctl bootstatus "iPhone 16 Pro" -b
ctest --test-dir <ios-runtime-build> -R 'vellum.rmlui.metal-(visual|layers|trim|filters|image-decode)' --output-on-failure
xcrun simctl shutdown "iPhone 16 Pro"
```

Device compile check:

```sh
cmake -S tools/mirror/consumer/ios-auv3 -B <ios-device-build> -G Ninja \
  -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT=iphoneos \
  -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_BUILD_TYPE=Release
~/bin/leased-build --wait-secs 3600 -- cmake --build <ios-device-build> \
  --target vellum-rmlui-metal-ios-compile
```

# iOS Metal asset-bundle regression

The new regression contract was added before the implementation and fails on the pre-fix head 46abf596bb7573e58bf18bbe4023acc9f2b94b60:

```
AssertionError: missing contract text: 'progress: fonts loaded'
```

The implementation must bundle the fonts, fixtures, and metallib into both runtime app bundles and resolve them through NSBundle when argv paths are absent or unreadable. The simulator/device run needs the lead's GPU-capable execution environment.
