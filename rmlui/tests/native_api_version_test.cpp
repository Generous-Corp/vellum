// The API-level macros in native.hpp name interfaces that exist: each feature
// bit a release claims is compiled against here, so a bit that outlives the
// interface it names, or a release that adds an interface without its bit,
// fails to build or to run.
#include <vellum/rmlui/cpu_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#include <cstddef>
#include <type_traits>

#ifndef VELLUM_RMLUI_NATIVE_API_VERSION
#error "native.hpp defines no API version"
#endif
static_assert(VELLUM_RMLUI_NATIVE_API_VERSION >= 1);

// The bits are distinct, and the set is exactly the bits this release names.
static_assert((VELLUM_RMLUI_NATIVE_FEATURE_DENSITY_RATIO & VELLUM_RMLUI_NATIVE_FEATURE_WARM) == 0);
static_assert((VELLUM_RMLUI_NATIVE_FEATURE_DENSITY_RATIO & VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM) == 0);
static_assert((VELLUM_RMLUI_NATIVE_FEATURE_WARM & VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM) == 0);
static_assert(VELLUM_RMLUI_NATIVE_FEATURES ==
              (VELLUM_RMLUI_NATIVE_FEATURE_DENSITY_RATIO | VELLUM_RMLUI_NATIVE_FEATURE_WARM |
               VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM));

using vellum::rmlui::CpuRenderInterface;
using vellum::rmlui::NativeContext;
using vellum::rmlui::Status;

#if VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_DENSITY_RATIO
static_assert(std::is_same_v<decltype(&NativeContext::set_density_ratio), Status (NativeContext::*)(float)>);
static_assert(std::is_same_v<decltype(&NativeContext::density_ratio), float (NativeContext::*)() const noexcept>);
#else
#error "the density ratio bit is missing, but NativeContext has set_density_ratio"
#endif

#if VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_WARM
static_assert(std::is_same_v<decltype(&NativeContext::warm), void (NativeContext::*)()>);
#else
#error "the warm bit is missing, but NativeContext has warm"
#endif

#if VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM
static_assert(std::is_same_v<decltype(&CpuRenderInterface::trim), std::size_t (CpuRenderInterface::*)()>);
static_assert(std::is_same_v<decltype(&CpuRenderInterface::cached_bytes),
                             std::size_t (CpuRenderInterface::*)() const noexcept>);
#else
#error "the renderer trim bit is missing, but CpuRenderInterface has trim"
#endif

int main() {
    // The macros are usable in the preprocessor, as a consumer uses them.
    int features = 0;
#if defined(VELLUM_RMLUI_NATIVE_API_VERSION) && (VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_WARM)
    ++features;
#endif
    VELLUM_RMLUI_CHECK(features == 1);
    return 0;
}
