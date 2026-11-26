// WebView backend detector.
//
// It is compiled together with web_view.cpp, so a build that carries
// WebViewPanel answers with the platform backend it compiled. The answer is a
// constant, which keeps it correct from the first call, including one made by
// another translation unit's static initializer. Tests may install an override
// through detail::set_webview_backend_provider().
//
//   * macOS / iOS   -> "wkwebview"   (WebKit.framework ships with the OS).
//   * Other         -> "none"        (no backend in this build yet).

#include <vellum/view/web_view.hpp>

#include <atomic>
#include <string>

namespace vellum::view {

namespace {

std::string build_webview_backend() {
#if defined(__APPLE__)
    return "wkwebview";
#else
    return "none";
#endif
}

bool build_webview_backend_available() {
#if defined(__APPLE__)
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
