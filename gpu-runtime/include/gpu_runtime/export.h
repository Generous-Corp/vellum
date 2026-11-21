#pragma once

// Public gpu-runtime ABI used by the provider-owned Vellum::Gpu frontend.
// Most device implementation symbols stay hidden; these declarations cross
// the provider dylib boundary and therefore opt back into default visibility.
#if !defined(GPU_RUNTIME_EXPORT_ABI)
#define GPU_RUNTIME_API
#elif defined(_WIN32) && defined(GPU_RUNTIME_BUILD_SHARED)
#define GPU_RUNTIME_API __declspec(dllexport)
#elif defined(__GNUC__) || defined(__clang__)
#define GPU_RUNTIME_API __attribute__((visibility("default")))
#else
#define GPU_RUNTIME_API
#endif
