// pulp-platform implementation
// Currently header-only; this file ensures the library links correctly.

#include <vellum/platform/platform.hpp>

namespace vellum::platform {

// Verify compile-time platform detection works
static_assert(current_os != OS::Unknown || !is_desktop,
    "Platform detection failed on a desktop OS");

} // namespace vellum::platform
