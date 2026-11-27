// Non-macOS WebView backend detector stub.
//
// The public detector remains linkable on platforms that do not carry a native
// WebView implementation. Those builds report no backend until their provider
// is added, while retaining the same test override as macOS.

#include <vellum/view/web_view.hpp>

#include <atomic>
#include <string>

namespace vellum::view {

namespace {

std::string build_webview_backend() {
    return "none";
}

bool build_webview_backend_available() {
    return false;
}

constexpr detail::WebViewBackendProvider kBuildProvider{&build_webview_backend,
                                                        &build_webview_backend_available};
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
