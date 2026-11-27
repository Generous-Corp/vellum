// WebView backend detector.
//
// It is compiled together with web_view.cpp, which the build carries on
// macOS only, so it answers "wkwebview" there and "none" on any other target.
// The answer is a constant, so it is correct from the first call, including
// one made by another translation unit's static initializer. Tests may install
// an override through detail::set_webview_backend_provider().

#include <vellum/view/web_view.hpp>

#include <atomic>
#include <string>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

namespace vellum::view {

namespace {

std::string build_webview_backend() {
#if defined(__APPLE__) && TARGET_OS_OSX
    return "wkwebview";
#else
    return "none";
#endif
}

bool build_webview_backend_available() {
#if defined(__APPLE__) && TARGET_OS_OSX
    return true;
#else
    return false;
#endif
}

constexpr detail::WebViewBackendProvider kBuildProvider{&build_webview_backend,
                                                        &build_webview_backend_available};

// An override installed by set_webview_backend_provider (tests). Constant-
// initialized, so it is valid before any dynamic initializer runs.
std::atomic<const detail::WebViewBackendProvider*> g_override{nullptr};

const detail::WebViewBackendProvider* active_provider() {
    if (const auto* provider = g_override.load(std::memory_order_acquire))
        return provider;
    return &kBuildProvider;
}

} // namespace

const detail::WebViewBackendProvider*
detail::set_webview_backend_provider(const WebViewBackendProvider* provider) {
    return g_override.exchange(provider, std::memory_order_acq_rel);
}

std::string detect_webview_backend() {
    const auto* provider = active_provider();
    return provider && provider->detect ? provider->detect() : std::string("none");
}

bool webview_backend_available() {
    const auto* provider = active_provider();
    return provider && provider->available ? provider->available() : false;
}

} // namespace vellum::view
