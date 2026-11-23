# Android plug-in and app host plan

Android is the remaining Vellum host platform. The first slices keep the
platform behind an explicit closure and an NDK-only canary. macOS remains the
release priority. No slice below treats a host build as proof of an Android
window, Vulkan surface, input bridge, or packaged application.

## Ordered PR slices

### 1. Android closure record and NDK platform plumbing

**Files**

- `provenance/framework/platforms/index.json`
- `provenance/framework/platforms/android-arm64-v8a/record.json` and per-unit closure manifests
- `tools/android/AndroidPlatform.cmake`
- `tools/android/CMakeLists.txt`
- `tools/android/check_android_objects.py` and its tests
- `.github/workflows/android.yml`

The record names the Pulp commit, exporter blob, NDK/toolchain image, API floor,
ABI, unit manifest digests, and renderer asset. The initial supported ABI is
`arm64-v8a`, with `x86_64` reserved for emulator proof. The helper normalizes
the key to `android-arm64-v8a` or `android-x86_64` and rejects an API below 26.

**No-device checks**

```sh
cmake -P tools/android/test_android_platform.cmake
python3 -I tools/android/test_check_android_objects.py
cmake -S tools/android -B "$RUNNER_TEMP/android" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 \
  -DVELLUM_CHOC_ROOT="$VELLUM_CHOC_ROOT" -DCMAKE_BUILD_TYPE=Release
cmake --build "$RUNNER_TEMP/android" --parallel "$(nproc)"
python3 -I tools/android/check_android_objects.py \
  --build-dir "$RUNNER_TEMP/android" \
  --expect-sources "$(wc -l < "$RUNNER_TEMP/android/android-sources.txt")"
```

The lane must also compile the JNI header probe with
`aarch64-linux-android26-clang++`, require its probe symbol with `llvm-nm`, and
compile one host object as a negative control that the ELF checker rejects.

**Device or emulator**

None for this slice. The lead should later run the full Android workflow on a
configured runner and retain the closure manifests and toolchain receipt.

### 2. GPU surface using Pulp's Android path

**Files**

- `framework/core/render/platform/android/` with Vellum-owned names for the surface, lifecycle, and JNI bridge
- `framework/core/render/src/render_loop_android.cpp`
- `gpu-runtime/` Android platform selection and the Android renderer lock entry
- `provenance/renderer-attestation.json`
- closure and ports rows for every source

Port the API boundary from Pulp's `gpu_surface_android.cpp`,
`gpu_surface_android_internal.hpp`, `gpu_surface_android_jni.cpp`, and
`choreographer_android.cpp`. Use Dawn's Vulkan backend through Pulp's existing
Android build path and keep the app-owned `ANativeWindow` lifecycle above the
render target.

**No-device checks**

```sh
cmake -S gpu-runtime -B build/android-gpu \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 \
  -DGPU_RUNTIME_BUILD_TESTS=ON
cmake --build build/android-gpu --parallel "$(nproc)"
cmake -P tools/android/test_android_platform.cmake
```

Run source audits, closure reproduction, ELF symbol checks, and the Vulkan/Dawn
link check. These prove compilation and linkage only.

**Device or emulator**

```sh
emulator -list-avds
QEMU_AUDIO_DRV=coreaudio emulator -avd Medium_Phone_API_36 -gpu host -no-snapshot &
adb wait-for-device
adb shell getprop sys.boot_completed
adb logcat -c
adb shell am start -n com.vellum/.VellumActivity
adb logcat -d -s Vellum:D AndroidRuntime:E
```

The lead must run the surface lifecycle through pause, resume, resize, and
surface destruction, and capture a Vulkan validation log. This lane needs a
GPU-capable emulator or an arm64 device.

### 3. View host

**Files**

- `framework/core/view/platform/android/` for accessibility, motion JNI, and the Android plug-in editor host
- view platform service registration and Android closure rows
- `android/` Gradle shell sources and JNI entry points

Keep the host API parallel to the existing macOS, iOS, Linux, and Windows
`gpu_plugin_editor_host_*` implementations. Android owns `SurfaceView`, touch,
IME, accessibility, and lifecycle callbacks. The native view host owns widget
routing and editor bounds.

**No-device checks**

```sh
cmake -S tools/android -B build/android-view \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 \
  -DVELLUM_CHOC_ROOT="$VELLUM_CHOC_ROOT"
cmake --build build/android-view --parallel "$(nproc)"
python3 -I tools/android/check_android_objects.py \
  --build-dir build/android-view \
  --expect-sources "$(wc -l < build/android-view/android-sources.txt)"
```

Add compile-only JNI and accessibility probes plus CMake source-selection checks.

**Device or emulator**

```sh
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.vellum/.VellumActivity
adb shell uiautomator dump /sdcard/window.xml
adb pull /sdcard/window.xml /tmp/vellum-android-window.xml
```

The lead must verify TalkBack node exposure, touch dispatch, IME composition,
editor resize, and activity recreation with `adb logcat` attached.

### 4. RmlUi CPU renderer on Android

**Files**

- `rmlui/` Android renderer adapter and CPU surface bridge
- Android-specific RmlUi CMake target and closure rows
- a headless renderer fixture using an Android-compatible pixel buffer

Keep this renderer CPU-only. It is the fallback for devices without the locked
Dawn/Vulkan asset and must not pull desktop OpenGL or Objective-C++ sources.

**No-device checks**

```sh
cmake -S rmlui -B build/rmlui-android \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26
cmake --build build/rmlui-android --parallel "$(nproc)"
ctest --test-dir build/rmlui-android --output-on-failure
```

Run deterministic layout, clipping, text, and pixel-buffer tests. A host CPU
renderer result is not Android surface proof.

**Device or emulator**

```sh
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
adb shell am force-stop com.vellum
adb shell am start -n com.vellum/.VellumActivity
adb exec-out screencap -p > /tmp/vellum-android-rmlui.png
```

The lead must compare the screenshot against the CPU reference at the device's
reported density and check text input and accessibility overlays.

### 5. Packaging

**Files**

- `android/settings.gradle*`, `android/build.gradle*`, and `android/app/`
- ABI-specific Gradle packaging and native library install rules
- `tools/android/` packaging and manifest checks
- release provenance, checksums, and signing metadata

Package arm64-v8a first. Add x86_64 only for emulator QA, then make the release
ABI set explicit in the artifact manifest. Keep debug installation separate
from signed release publication.

**No-device checks**

```sh
cd android
./gradlew assembleDebug
./gradlew lint test
```

Run APK manifest, ABI, native-library, checksum, and reproducible archive checks.

**Device or emulator**

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am force-stop com.vellum
adb shell am start -n com.vellum/.VellumActivity
adb shell dumpsys package com.vellum | rg 'primaryCpuAbi|versionName'
adb shell pm path com.vellum
```

The lead must test install, upgrade, uninstall, cold start, rotation, and
activity recreation on arm64 hardware. Signing and publication require the
release owner and protected credentials.

## Current slice boundary

This branch implements only the platform identity helper and its CMake-script
check around the existing NDK canary. It does not claim a closure manifest,
Dawn/Vulkan pixels, an Android editor window, or an APK. Those require the
ordered slices above and the lead's device or GPU runs.
