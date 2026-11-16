// Compiled only for Android. References the JNI bridge and motion bridge
// declarations so the lane proves they are visible on Android, not just that
// an empty translation unit compiled.

#include <vellum/platform/android/jni.hpp>
#include <vellum/platform/android/motion_bridge.h>

#include <type_traits>

#if !defined(__ANDROID__)
#error "jni_probe.cpp must be built with the Android toolchain"
#endif

static_assert(std::is_same_v<decltype(vellum::android::g_vm), JavaVM*>);
static_assert(std::is_same_v<decltype(&vellum::android::get_env), JNIEnv* (*)()>);

// Exported with C linkage so the CI lane can find them with llvm-nm.
extern "C" __attribute__((visibility("default"))) int vellum_android_probe_abi() {
    // Keeps a reference to the C ABI the Kotlin motion facade links against.
    using Tracing = bool (*)();
    Tracing tracing = &pulp_motion_tracing_enabled;
    return tracing != nullptr && vellum::android::has_asset_manager() ? 1 : 0;
}
