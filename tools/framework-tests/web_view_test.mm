// WebViewPanel on macOS: the bridge and resource helpers, the backend
// detector, and a live WKWebView driven from the main run loop with no window
// (readiness, evaluate_js, the message bridge both ways, a bound function and
// an in-process snapshot). A live case fails rather than skips when the
// WebView never becomes ready: WebKit ships with macOS.

#include "framework_test.hpp"

#include <vellum/view/asset_manager.hpp>
#include <vellum/view/web_view.hpp>

#import <AppKit/AppKit.h>
#import <CoreFoundation/CoreFoundation.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::view;
using namespace std::chrono_literals;

bool contains(const std::string& text, const std::string& needle) { return text.find(needle) != std::string::npos; }

bool pump_until(const std::function<bool()>& ready, std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (!ready() && std::chrono::steady_clock::now() < deadline)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    return ready();
}

// Evaluates `js` and pumps until its result arrives. The pair is
// {result_json, error}; error is "timeout" when no result came back.
std::pair<std::string, std::string> await_eval(WebViewPanel& panel, const std::string& js,
                                               std::chrono::milliseconds timeout = 5000ms) {
    auto result = std::make_shared<std::optional<std::pair<std::string, std::string>>>();
    panel.evaluate_js(js, [result](const std::string& json, const std::string& error) {
        *result = std::make_pair(json, error);
    });
    if (!pump_until([&] { return result->has_value(); }, timeout))
        return {"", "timeout"};
    return **result;
}

// Evaluates `js` until it yields `expected`, for page state that settles
// asynchronously after set_html().
bool eventually(WebViewPanel& panel, const std::string& js, const std::string& expected,
                std::chrono::milliseconds timeout = 5000ms) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        const auto [json, error] = await_eval(panel, js, 500ms);
        if (error.empty() && json == expected)
            return true;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.02, true);
    }
    return false;
}

std::unique_ptr<WebViewPanel> ready_panel(const WebViewOptions& options = {}) {
    auto panel = WebViewPanel::create(options);
    const bool ready = panel && pump_until([&] { return panel->is_ready(); }, 5000ms);
    return ready ? std::move(panel) : nullptr;
}

void write_text(const std::filesystem::path& path, const std::string& text) {
    std::ofstream(path, std::ios::binary).write(text.data(), std::streamsize(text.size()));
}

// Decodes a PNG and reads the pixel at its centre as 8-bit sRGB RGBA, with
// the image size. Empty when the bytes are not an image.
std::optional<std::array<int, 4>> centre_pixel(const std::vector<uint8_t>& png, size_t& width,
                                               size_t& height) {
    NSData* data = [NSData dataWithBytes:png.data() length:png.size()];
    NSBitmapImageRep* rep = data.length ? [NSBitmapImageRep imageRepWithData:data] : nil;
    if (!rep)
        return std::nullopt;
    width = size_t(rep.pixelsWide);
    height = size_t(rep.pixelsHigh);
    // The raw 8-bit samples, as WebKit wrote them.
    NSUInteger px[4] = {0, 0, 0, 255};
    [rep getPixel:px atX:rep.pixelsWide / 2 y:rep.pixelsHigh / 2];
    return std::array<int, 4>{int(px[0]), int(px[1]), int(px[2]),
                              rep.samplesPerPixel > 3 ? int(px[3]) : 255};
}

// ── Pure helpers ────────────────────────────────────────────────────────────

FRAMEWORK_TEST(bridge_message_json_round_trips) {
    WebViewMessage message;
    message.type = "theme.update";
    message.payload_json = R"({"accent":"#ff5500","alpha":0.8})";
    message.id = "msg-7";
    const auto encoded = encode_webview_message_json(message);
    CHECK(contains(encoded, "\"type\":\"theme.update\""));
    CHECK(contains(encoded, "\"id\":\"msg-7\""));

    WebViewMessage decoded;
    CHECK(decode_webview_message_json(encoded, decoded));
    CHECK(decoded.type == "theme.update");
    CHECK(decoded.id == "msg-7");
    CHECK(encode_webview_message_json(decoded) == encoded);

    WebViewMessage rejected;
    CHECK(!decode_webview_message_json("[1,2]", rejected));
    CHECK(!decode_webview_message_json("{\"payload\":1}", rejected));
}

FRAMEWORK_TEST(bootstrap_and_eval_scripts_name_the_bridge) {
    const auto bootstrap = make_webview_bridge_bootstrap_script();
    CHECK(contains(bootstrap, "window.pulp"));
    CHECK(contains(bootstrap, "postMessage"));
    CHECK(contains(bootstrap, "__pulpPostMessage"));
    CHECK(contains(bootstrap, "__deliverFromNative"));

    const auto eval = make_webview_eval_script("eval-42", "JSON.stringify({ ok: true })");
    CHECK(contains(eval, "__pulpEvalResult"));
    CHECK(contains(eval, "globalThis.eval"));
    CHECK(contains(eval, "\"eval-42\""));
    CHECK(contains(eval, "JSON.stringify({ ok: true })"));
}

FRAMEWORK_TEST(mime_types_and_data_uris) {
    CHECK(guess_webview_mime_type("index.html") == "text/html");
    CHECK(guess_webview_mime_type("app.js") == "text/javascript");
    CHECK(guess_webview_mime_type("styles.css") == "text/css");
    CHECK(guess_webview_mime_type("data.json") == "application/json");
    CHECK(guess_webview_mime_type("icon.svg") == "image/svg+xml");
    CHECK(guess_webview_mime_type("photo.jpg") == "image/jpeg");
    CHECK(guess_webview_mime_type("photo.jpeg") == "image/jpeg");
    CHECK(guess_webview_mime_type("unknown.bin") == "application/octet-stream");

    const std::string json = "{\"label\":\"caf\xC3\xA9\",\"kanji\":\"\xE9\x9F\xB3\"}";
    const std::vector<uint8_t> bytes(json.begin(), json.end());
    const auto uri = make_webview_data_uri("application/json", bytes);
    CHECK(uri == "data:application/json;charset=utf-8;base64,eyJsYWJlbCI6ImNhZsOpIiwia2FuamkiOiLpn7MifQ==");
}

FRAMEWORK_TEST(embedded_html_rewrites_to_offline_data_uris) {
    static const uint8_t html[] = "<!doctype html><html><head>"
                                  "<link rel=\"stylesheet\" href=\"styles.css\">"
                                  "</head><body><script src=\"./app.js\"></script></body></html>";
    static const uint8_t js[] = "console.log('webview');";
    static const uint8_t css[] = "body{background:#123456;}";
    auto& assets = AssetManager::instance();
    assets.register_embedded("webview_page_html", html, sizeof(html) - 1);
    assets.register_embedded("webview_app_js", js, sizeof(js) - 1);
    assets.register_embedded("webview_styles_css", css, sizeof(css) - 1);

    const auto offline = make_webview_offline_html_from_embedded(
        "webview_page_html",
        {{"app.js", "webview_app_js", ""}, {"styles.css", "webview_styles_css", ""}});
    CHECK(!offline.empty());
    CHECK(contains(offline, "data:text/javascript;base64,"));
    CHECK(contains(offline, "data:text/css;base64,"));
    CHECK(!contains(offline, "src=\"./app.js\""));
    CHECK(!contains(offline, "href=\"styles.css\""));
}

FRAMEWORK_TEST(embedded_resource_fetcher_serves_root_and_assets) {
    static const uint8_t html[] = "<!doctype html><html><body><script src=\"app.js\"></script></body></html>";
    static const uint8_t js[] = "window.webviewFixture = 'ready';";
    static const uint8_t json[] = "{\"label\":\"caf\xC3\xA9\"}";
    auto& assets = AssetManager::instance();
    assets.register_embedded("webview_fetch_html", html, sizeof(html) - 1);
    assets.register_embedded("webview_fetch_js", js, sizeof(js) - 1);
    assets.register_embedded("webview_fetch_json", json, sizeof(json) - 1);

    const auto fetch = make_webview_embedded_resource_fetcher(
        "webview_fetch_html",
        {{"app.js", "webview_fetch_js", ""}, {"data/config.json", "webview_fetch_json", ""}});
    CHECK(static_cast<bool>(fetch));
    if (!fetch)
        return;
    const auto root = fetch("/");
    CHECK(root && root->mime_type == "text/html" && root->data.size() == sizeof(html) - 1);
    const auto script = fetch("/app.js");
    CHECK(script && script->mime_type == "text/javascript" && script->data.size() == sizeof(js) - 1);
    const auto config = fetch("/data/config.json");
    CHECK(config && config->mime_type == "application/json" &&
          std::string(config->data.begin(), config->data.end()) ==
              std::string(reinterpret_cast<const char*>(json), sizeof(json) - 1));
    CHECK(!fetch("/missing.txt").has_value());
}

FRAMEWORK_TEST(directory_resource_fetcher_blocks_parent_traversal) {
    namespace fs = std::filesystem;
    const auto root_dir = fs::temp_directory_path() / "vellum_webview_files";
    fs::remove_all(root_dir);
    fs::create_directories(root_dir / "data");
    write_text(root_dir / "index.html", "<!doctype html><body>webview</body>");
    write_text(root_dir / "data" / "config.json", R"({"label":"x"})");
    write_text(root_dir.parent_path() / "vellum_webview_outside.txt", "secret");

    const auto fetch = make_webview_directory_resource_fetcher(root_dir);
    const auto root = fetch("/");
    CHECK(root && root->mime_type == "text/html" &&
          contains(std::string(root->data.begin(), root->data.end()), "webview"));
    const auto config = fetch("/data/config.json");
    CHECK(config && config->mime_type == "application/json");
    CHECK(!fetch("/../vellum_webview_outside.txt").has_value());
    CHECK(!fetch("/data/../../vellum_webview_outside.txt").has_value());

    fs::remove_all(root_dir);
    fs::remove(root_dir.parent_path() / "vellum_webview_outside.txt");
}

// ── Backend detector ────────────────────────────────────────────────────────

FRAMEWORK_TEST(backend_is_wkwebview_on_macos) {
    CHECK(detect_webview_backend() == "wkwebview");
    CHECK(webview_backend_available());
}

FRAMEWORK_TEST(backend_override_applies_and_clears) {
    static const detail::WebViewBackendProvider none{
        +[] { return std::string("none"); }, +[] { return false; }};
    const auto* previous = detail::set_webview_backend_provider(&none);
    CHECK(previous == nullptr);
    CHECK(detect_webview_backend() == "none");
    CHECK(!webview_backend_available());
    CHECK(detail::set_webview_backend_provider(nullptr) == &none);
    CHECK(detect_webview_backend() == "wkwebview");
}

// ── Live WKWebView ──────────────────────────────────────────────────────────

FRAMEWORK_TEST(live_panel_becomes_ready_and_evaluates) {
    auto panel = ready_panel();
    CHECK(panel != nullptr);
    if (!panel)
        return;
    CHECK(panel->native_handle() != nullptr);

    bool fired = false;
    panel->set_ready_handler([&] { fired = true; });
    CHECK(fired); // already ready: the handler runs at once

    const auto [json, error] = await_eval(*panel, "6 * 7");
    CHECK(error.empty());
    CHECK(json == "42");

    const auto thrown = await_eval(*panel, "(function(){ throw new Error('boom'); })()");
    CHECK(!thrown.second.empty() && thrown.second != "timeout");
}

FRAMEWORK_TEST(replacing_the_page_fails_pending_evaluations) {
    auto panel = ready_panel();
    CHECK(panel != nullptr);
    if (!panel)
        return;
    bool settled = false;
    std::string error;
    // A promise that never resolves stays pending until the page is replaced.
    panel->evaluate_js("new Promise(function(){})",
                       [&](const std::string&, const std::string& err) {
                           settled = true;
                           error = err;
                       });
    CHECK(!settled);
    panel->set_html("<!doctype html><html><body data-page=\"replaced\"></body></html>");
    CHECK(settled);
    CHECK(!error.empty());
}

FRAMEWORK_TEST(initial_html_shows_before_later_content) {
    WebViewOptions options;
    options.initial_html = "<!doctype html><html><body data-phase=\"initial\">loading</body></html>";
    auto panel = ready_panel(options);
    CHECK(panel != nullptr);
    if (!panel)
        return;
    const std::string phase = "document.body ? document.body.getAttribute('data-phase') : ''";
    CHECK(eventually(*panel, phase, "\"initial\""));
    panel->set_html("<!doctype html><html><body data-phase=\"final\">ready</body></html>");
    CHECK(eventually(*panel, phase, "\"final\""));
}

FRAMEWORK_TEST(message_bridge_round_trips_in_both_directions) {
    auto panel = ready_panel();
    CHECK(panel != nullptr);
    if (!panel)
        return;

    std::string last_type, last_payload, last_id;
    panel->set_message_handler([&](const WebViewMessage& message) -> std::string {
        last_type = message.type;
        last_payload = message.payload_json;
        last_id = message.id;
        return R"({"ack":true,"source":"native"})";
    });

    panel->set_html(std::string("<!doctype html><html><body><script>") +
                    make_webview_bridge_bootstrap_script() + R"JS(
window.testState = { nativePayload: null, reply: null };
window.pulp.on('native-ready', function(message) {
  window.testState.nativePayload = JSON.stringify(message.payload);
});
window.pingNative = function() {
  Promise.resolve(window.pulp.postMessage('page-ready', { from: 'html' }, 'page-1'))
    .then(function(response) { window.testState.reply = JSON.stringify(response); });
  return true;
};
</script></body></html>)JS");

    CHECK(eventually(*panel, "typeof window.pingNative === 'function'", "true"));

    // Native to page.
    panel->post_message({.type = "native-ready", .payload_json = R"({"from":"native"})", .id = "n-1"});
    CHECK(eventually(*panel,
                     "window.testState.nativePayload ? JSON.parse(window.testState.nativePayload).from : ''",
                     "\"native\""));

    // Page to native, and native's reply back to the page.
    CHECK(eventually(*panel, "window.pingNative()", "true"));
    CHECK(pump_until([&] { return !last_type.empty(); }, 5000ms));
    CHECK(last_type == "page-ready");
    CHECK(last_id == "page-1");
    CHECK(contains(last_payload, "\"from\"") && contains(last_payload, "\"html\""));
    CHECK(eventually(*panel,
                     "window.testState.reply ? String(JSON.parse(window.testState.reply).payload.ack) : ''",
                     "\"true\""));
}

FRAMEWORK_TEST(bound_function_receives_arguments_and_returns_json) {
    auto panel = ready_panel();
    CHECK(panel != nullptr);
    if (!panel)
        return;
    std::string seen_args;
    panel->bind("vellumDouble", [&](const std::string& args) -> std::string {
        seen_args = args;
        return "{\"value\":84}";
    });
    // The binding's init script runs on every page, so wait for the new page
    // itself before calling: a call made on the outgoing page is lost with it.
    panel->set_html("<!doctype html><html><body data-page=\"bound\"></body></html>");
    CHECK(eventually(*panel, "document.body ? document.body.getAttribute('data-page') : ''",
                     "\"bound\""));
    CHECK(eventually(*panel, "typeof window.vellumDouble === 'function'", "true"));
    panel->evaluate_js("window.vellumDouble(42).then(function(r){ window.doubled = r.value; });");
    CHECK(pump_until([&] { return !seen_args.empty(); }, 5000ms));
    CHECK(contains(seen_args, "42"));
    CHECK(eventually(*panel, "window.doubled === 84", "true"));
}

FRAMEWORK_TEST(snapshot_captures_rendered_content) {
    auto panel = ready_panel();
    CHECK(panel != nullptr);
    if (!panel)
        return;
    // No host embeds the panel here, so set_size() alone gives the view its size.
    panel->set_size(160, 120);
    panel->set_html("<!doctype html><html><body style=\"margin:0;background:#1e90ff\"></body></html>");
    CHECK(eventually(*panel, "getComputedStyle(document.body).backgroundColor", "\"rgb(30, 144, 255)\""));

    // The first snapshot after a load can precede the first composited frame.
    const std::array<int, 4> page{0x1e, 0x90, 0xff, 0xff};
    std::vector<uint8_t> png;
    std::optional<std::array<int, 4>> pixel;
    size_t w = 0, h = 0;
    pump_until([&] {
        png = panel->snapshot_png();
        pixel = centre_pixel(png, w, h);
        return pixel == page;
    }, 5000ms);
    CHECK(png.size() > 8 && png[0] == 0x89 && png[1] == 'P' && png[2] == 'N' && png[3] == 'G');
    CHECK(pixel == page);
    if (pixel)
        std::printf("  snapshot %zux%zu centre rgba(%d,%d,%d,%d)\n", w, h, (*pixel)[0],
                    (*pixel)[1], (*pixel)[2], (*pixel)[3]);
    // The PNG is in device pixels, so a Retina runner returns a multiple of the point size.
    const auto scaled_to = [](size_t pw, size_t ph, size_t w_pt, size_t h_pt) {
        return pw >= w_pt && pw % w_pt == 0 && ph == pw / w_pt * h_pt;
    };
    CHECK(scaled_to(w, h, 160, 120));
    // set_size() resizes the native view, and the next snapshot follows it.
    panel->set_size(200, 150);
    centre_pixel(panel->snapshot_png(), w, h);
    CHECK(scaled_to(w, h, 200, 150));
    if (const char* out = std::getenv("VELLUM_WEB_VIEW_SNAPSHOT_PNG"))
        std::ofstream(out, std::ios::binary).write(reinterpret_cast<const char*>(png.data()),
                                                   std::streamsize(png.size()));
}

} // namespace

int main() {
    return framework_test::run_all();
}
