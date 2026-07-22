#pragma once

#include <string_view>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

namespace vellum::foundation {

enum class OperatingSystem {
    macos,
    ios,
    windows,
    linux,
    android,
    web,
    unknown,
};

constexpr OperatingSystem current_operating_system() noexcept {
#if defined(__EMSCRIPTEN__)
    return OperatingSystem::web;
#elif defined(__ANDROID__)
    return OperatingSystem::android;
#elif defined(_WIN32)
    return OperatingSystem::windows;
#elif defined(__APPLE__)
#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
    return OperatingSystem::ios;
#else
    return OperatingSystem::macos;
#endif
#elif defined(__linux__)
    return OperatingSystem::linux;
#else
    return OperatingSystem::unknown;
#endif
}

std::string_view operating_system_name(OperatingSystem operating_system) noexcept;

}  // namespace vellum::foundation
