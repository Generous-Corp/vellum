// Link the fallback implementation directly, including on macOS where the
// mirrored view library supplies a different backend detector.
#include <vellum/view/web_view.hpp>
#include "framework_test.hpp"

namespace {
std::string fake_backend() { return "test-backend"; }
bool fake_available() { return true; }
}

FRAMEWORK_TEST(stub_detector_defaults_and_override_reset) {
    using namespace vellum::view;
    CHECK(detect_webview_backend() == "none");
    CHECK(!webview_backend_available());

    const detail::WebViewBackendProvider fake{fake_backend, fake_available};
    CHECK(detail::set_webview_backend_provider(&fake) == nullptr);
    CHECK(detect_webview_backend() == "test-backend");
    CHECK(webview_backend_available());

    const detail::WebViewBackendProvider empty{};
    CHECK(detail::set_webview_backend_provider(&empty) == &fake);
    CHECK(detect_webview_backend() == "none");
    CHECK(!webview_backend_available());

    CHECK(detail::set_webview_backend_provider(nullptr) == &empty);
    CHECK(detect_webview_backend() == "none");
    CHECK(!webview_backend_available());
    CHECK(detail::set_webview_backend_provider(nullptr) == nullptr);
}

int main() { return framework_test::run_all(); }
