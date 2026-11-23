#if defined(__ANDROID__)

#include "android_gpu_surface.hpp"

#include <jni.h>
#include <android/native_window_jni.h>
#include <android/native_window.h>
#include <cstdint>

namespace {

vellum::render::AndroidGpuSurface& surface() {
    static vellum::render::AndroidGpuSurface instance;
    return instance;
}

}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_com_vellum_render_VellumGpuSurface_nativeCreate(JNIEnv* env, jclass,
                                                     jobject java_surface,
                                                     jint width, jint height,
                                                     jfloat scale) {
    if (width <= 0 || height <= 0)
        return JNI_FALSE;
    ANativeWindow* window = ANativeWindow_fromSurface(env, java_surface);
    if (!window)
        return JNI_FALSE;
    const char* error = nullptr;
    const bool created = surface().create(window, static_cast<std::uint32_t>(width),
                                          static_cast<std::uint32_t>(height), scale, &error);
    ANativeWindow_release(window);
    return created ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT void JNICALL
Java_com_vellum_render_VellumGpuSurface_nativeResize(JNIEnv*, jclass, jint width, jint height) {
    if (width <= 0 || height <= 0)
        return;
    surface().resize(static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height));
}

extern "C" JNIEXPORT void JNICALL
Java_com_vellum_render_VellumGpuSurface_nativeDestroy(JNIEnv*, jclass) {
    surface().destroy();
}

#endif
