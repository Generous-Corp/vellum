#pragma once

#include <vellum/graphics/dawn_bootstrap.hpp>

#include "dawn/dawn_proc.h"
#include "dawn/dawn_version.h"
#include "dawn/native/DawnNative.h"

#include <cstdint>
#include <string>
#include <string_view>

#if defined(_WIN32)
#define VELLUM_GRAPHICS_API __declspec(dllexport)
#elif defined(__GNUC__) || defined(__clang__)
#define VELLUM_GRAPHICS_API __attribute__((visibility("default")))
#else
#define VELLUM_GRAPHICS_API
#endif

namespace vellum::app_host {
namespace detail {

using NativeDawnVersion = const std::uint8_t* (*)();
using NativeDawnProcs = const DawnProcTable& (*)();
using NativeDawnSetter = void (*)(const DawnProcTable*);

struct NativeDawnBindings final {
    NativeDawnVersion header_version = nullptr;
    NativeDawnVersion proc_version = nullptr;
    NativeDawnProcs native_procs = nullptr;
    NativeDawnSetter set_procs = nullptr;
};

VELLUM_GRAPHICS_API graphics::DawnBootstrapResult bootstrap_native_dawn(
    const graphics::DawnBootstrapRequest& request, void* opaque, std::string* error);

}  // namespace detail

VELLUM_GRAPHICS_API std::string native_dawn_revision();
VELLUM_GRAPHICS_API graphics::DawnBootstrap native_dawn_bootstrap();

VELLUM_GRAPHICS_API bool register_native_dawn_bootstrap(std::string* error = nullptr);
VELLUM_GRAPHICS_API bool register_native_dawn_bootstrap_for_bundle(
    std::string_view bundle, std::string* error = nullptr);

VELLUM_GRAPHICS_API bool native_dawn_bootstrap_installed();

}  // namespace vellum::app_host

#undef VELLUM_GRAPHICS_API
