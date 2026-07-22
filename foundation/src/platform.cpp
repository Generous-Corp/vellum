#include <vellum/foundation/platform.hpp>

namespace vellum::foundation {

std::string_view operating_system_name(OperatingSystem operating_system) noexcept {
    switch (operating_system) {
        case OperatingSystem::macos: return "macos";
        case OperatingSystem::ios: return "ios";
        case OperatingSystem::windows: return "windows";
        case OperatingSystem::linux: return "linux";
        case OperatingSystem::android: return "android";
        case OperatingSystem::web: return "web";
        case OperatingSystem::unknown: return "unknown";
    }
    return "unknown";
}

}  // namespace vellum::foundation
