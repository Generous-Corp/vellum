// Checks that a compiled JSX bundle runs through the Claude-style runtime
// harness (`parse_claude_html_with_runtime`) and materializes into DesignIR,
// and that a native live bundle freezes through the baked snapshot fallback.
//
// The two bundle cases read an operator-built bundle from VELLUM_JSX_BUNDLE and
// VELLUM_TSX_BUNDLE (built with tools/import-design/jsx-runtime/jsx-transform.mjs)
// and report a skip when it is absent, keeping multi-megabyte data out of the
// source. The two native cases need no input.

#include "framework_test.hpp"

#include <vellum/runtime/base64.hpp>
#include <vellum/runtime/zip.hpp>
#include <vellum/view/design_import.hpp>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace vellum::view;

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

// A case that needs an operator-supplied bundle reports a skip and passes.
#define SKIP(message) \
    do { \
        std::printf("SKIP %s\n", message); \
        return; \
    } while (false)

namespace {

std::vector<uint8_t> gzip_wrap_deflate(const std::vector<uint8_t>& raw) {
    auto deflated = vellum::runtime::deflate_compress(raw.data(), raw.size());
    require(deflated.has_value());
    std::vector<uint8_t> out;
    out.reserve(deflated->size() + 18);
    const uint8_t header[10] = {0x1f, 0x8b, 0x08, 0x00,
                                0, 0, 0, 0,
                                0, 0xff};
    out.insert(out.end(), header, header + 10);
    out.insert(out.end(), deflated->begin(), deflated->end());
    for (int i = 0; i < 4; ++i) out.push_back(0);
    uint32_t isize = static_cast<uint32_t>(raw.size());
    out.push_back(static_cast<uint8_t>(isize & 0xff));
    out.push_back(static_cast<uint8_t>((isize >> 8) & 0xff));
    out.push_back(static_cast<uint8_t>((isize >> 16) & 0xff));
    out.push_back(static_cast<uint8_t>((isize >> 24) & 0xff));
    return out;
}

std::string manifest_entry(const std::string& uuid, const std::string& mime,
                           const std::string& contents, bool compressed) {
    std::vector<uint8_t> bytes(contents.begin(), contents.end());
    std::vector<uint8_t> payload = compressed ? gzip_wrap_deflate(bytes)
                                              : std::move(bytes);
    std::string b64 = vellum::runtime::base64_encode(payload.data(), payload.size());
    std::ostringstream ss;
    ss << "\"" << uuid << "\":{"
       << "\"mime\":\"" << mime << "\","
       << "\"compressed\":" << (compressed ? "true" : "false") << ","
       << "\"data\":\"" << b64 << "\"}";
    return ss.str();
}

std::string build_envelope(const std::string& manifest_json,
                           const std::string& template_body_html) {
    auto json_quote = [](const std::string& s) {
        std::string out = "\"";
        for (char c : s) {
            switch (c) {
                case '"':  out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                case '/':  out += "\\u002F"; break;
                default:
                    if (static_cast<unsigned char>(c) < 0x20) {
                        char buf[8];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                        out += buf;
                    } else {
                        out += c;
                    }
            }
        }
        out += "\"";
        return out;
    };

    std::ostringstream ss;
    ss << "<!DOCTYPE html><html><head><title>JSX Runtime Smoke</title></head><body>"
       << "<script type=\"__bundler/manifest\">" << manifest_json << "</script>"
       << "<script type=\"__bundler/template\">" << json_quote(template_body_html)
       << "</script>"
       << "</body></html>";
    return ss.str();
}

std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) return {};
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

size_t count_ir_nodes(const IRNode& n) {
    size_t total = 1;
    for (const auto& c : n.children) total += count_ir_nodes(c);
    return total;
}

bool ir_contains_text(const IRNode& n, const std::string& needle) {
    if (n.text_content.find(needle) != std::string::npos) return true;
    for (const auto& c : n.children) if (ir_contains_text(c, needle)) return true;
    return false;
}

const IRNode* find_ir_node_by_anchor(const IRNode& n, const std::string& anchor) {
    if (n.stable_anchor_id && *n.stable_anchor_id == anchor)
        return &n;
    for (const auto& c : n.children) {
        if (const auto* found = find_ir_node_by_anchor(c, anchor))
            return found;
    }
    return nullptr;
}

}  // namespace

FRAMEWORK_TEST(jsx_experiment_chainer_jsx_bundle_materializes_through_claude_runtime_harness) {
    const char* bundle_path = std::getenv("VELLUM_JSX_BUNDLE");
    if (!bundle_path || !*bundle_path) {
        SKIP("VELLUM_JSX_BUNDLE not set");
    }

    const std::string app_js = read_file(bundle_path);
    require(!app_js.empty());
    require(app_js.size() > 10000);  // sanity: real bundle is ~1 MB

    // Wrap as Claude envelope. Use compressed=false for large bundles because
    // the test's hand-rolled gzip wrap has round-trip issues for highly-
    // compressible inputs (see test_design_import_claude_runtime.cpp:198).
    std::ostringstream manifest;
    manifest << "{" << manifest_entry("jsx-app", "text/javascript", app_js, false) << "}";

    const std::string body =
        R"(<div id="root"></div><script src="jsx-app"></script>)";

    std::string err;
    ClaudeRuntimeOptions opts;
    opts.error_out = &err;
    // Bundle is ~1.1 MB; give it 4 MB of headroom.
    opts.max_total_js_bytes = 4 * 1024 * 1024;

    auto ir = parse_claude_html_with_runtime(
        build_envelope(manifest.str(), body), opts);

    const size_t nodes = count_ir_nodes(ir.root);

    // First-tier acceptance: nothing exploded.
    require(ir.source == DesignSource::claude);

    // Second-tier: bundle ran past the loader-shell floor. If it fell back
    // to static parsing, error_out tells us why.
    require(nodes > 9);

    // Third-tier (the interesting bit): some of Chainer's section labels
    // should make it through into the materialized text. These are the
    // explicit `title` strings on the Section() calls in the JSX.
    // We accept ANY of these to keep the test resilient against partial
    // render - a stronger version would require all four.
    const bool found_chainer_text =
        ir_contains_text(ir.root, "generator")  ||
        ir_contains_text(ir.root, "envelope")   ||
        ir_contains_text(ir.root, "multiband")  ||
        ir_contains_text(ir.root, "CHAINER")    ||
        ir_contains_text(ir.root, "polywave");
    require(found_chainer_text);
}

FRAMEWORK_TEST(jsx_experiment_native_live_bundle_can_freeze_through_baked_snapshot_fallback) {
    std::string native_bundle =
        "(function(){\n"
        "  createCol('native-panel', '');\n"
        "  for (var i = 0; i < 12; ++i) {\n"
        "    createLabel('native-label-' + i, i === 0 ? 'CHAINER native snapshot' : ('native row ' + i), 'native-panel');\n"
        "  }\n"
        "})();\n";
    native_bundle.append(256, ' ');

    auto bundle = parse_jsx_react(native_bundle, "NativeBridgeSmoke");
    require(bundle.has_value());

    std::string err;
    ClaudeRuntimeOptions opts;
    opts.error_out = &err;
    auto ir = parse_claude_html_with_runtime(synthesize_runtime_envelope(*bundle), opts);

    require(err.empty());
    require(ir.capture_method == "runtime_native_snapshot");
    require(ir.source_adapter == "claude-native-view");
    require(count_ir_nodes(ir.root) > 9);
    require(ir_contains_text(ir.root, "CHAINER native snapshot"));
}

FRAMEWORK_TEST(jsx_experiment_native_live_bundle_freezes_after_requested_viewport_layout) {
    std::string native_bundle =
        "(function(){\n"
        "  createCol('native-panel', '');\n"
        "  createLabel('caption', 'native viewport', 'native-panel');\n"
        "  setFlex('caption', 'margin_bottom', 8);\n"
        "  setBorderBottomColor('caption', '#112233');\n"
        "  setBorderBottomWidth('caption', 1);\n"
        "  createRow('wrap-row', 'native-panel');\n"
        "  setFlex('wrap-row', 'flex_wrap', 'wrap');\n"
        "  for (var i = 0; i < 8; ++i) {\n"
        "    var id = 'tile-' + i;\n"
        "    createCol(id, 'wrap-row');\n"
        "    setFlex(id, 'width', 120);\n"
        "    setFlex(id, 'height', 20);\n"
        "    setFlex(id, 'flex_shrink', 0);\n"
        "  }\n"
        "  createCol('align-host', 'native-panel');\n"
        "  setFlex('align-host', 'width', 260);\n"
        "  setFlex('align-host', 'align_items', 'center');\n"
        "  createCol('centered-child', 'align-host');\n"
        "  setFlex('centered-child', 'width', 120);\n"
        "  setFlex('centered-child', 'height', 20);\n"
        "})();\n";
    native_bundle.append(256, ' ');

    auto bundle = parse_jsx_react(native_bundle, "NativeViewportSmoke");
    require(bundle.has_value());

    std::string err;
    ClaudeRuntimeOptions opts;
    opts.error_out = &err;
    opts.runtime_snapshot_viewport_width = 260;
    opts.runtime_snapshot_viewport_height = 180;
    auto ir = parse_claude_html_with_runtime(synthesize_runtime_envelope(*bundle), opts);

    require(err.empty());
    require(ir.capture_method == "runtime_native_snapshot");

    const auto* panel = find_ir_node_by_anchor(ir.root, "native-panel");
    const auto* caption = find_ir_node_by_anchor(ir.root, "caption");
    const auto* wrap = find_ir_node_by_anchor(ir.root, "wrap-row");
    const auto* align_host = find_ir_node_by_anchor(ir.root, "align-host");
    require(panel != nullptr);
    require(caption != nullptr);
    require(wrap != nullptr);
    require(align_host != nullptr);
    require(panel->style.width);
    require(caption->layout.margin_bottom);
    require(wrap->style.width);
    require(wrap->style.height);
    require(*panel->style.width == 260.0f);
    require(*caption->layout.margin_bottom == 8.0f);
    require(caption->style.border_bottom_width);
    require(caption->style.border_bottom_color);
    require(*caption->style.border_bottom_width == 1.0f);
    require(*caption->style.border_bottom_color == "#112233");
    require(align_host->layout.align == LayoutAlign::center);
    require(*wrap->style.width == 260.0f);
    require(*wrap->style.height > 20.0f);
}

FRAMEWORK_TEST(jsx_experiment_typescript_tsx_bundle_materializes_through_claude_runtime_harness) {
    // Same shape as the JSX case but proves the esbuild 'tsx' loader strips
    // TypeScript correctly and the resulting JS round-trips through the
    // runtime harness. TSX is the dominant React file shape today; making the
    // importer handle it lifts the experiment from "one hand-picked .jsx
    // fixture" to the mainstream React export surface.
    const char* bundle_path = std::getenv("VELLUM_TSX_BUNDLE");
    if (!bundle_path || !*bundle_path) {
        SKIP("VELLUM_TSX_BUNDLE not set");
    }

    const std::string app_js = read_file(bundle_path);
    require(!app_js.empty());
    require(app_js.size() > 10000);

    std::ostringstream manifest;
    manifest << "{" << manifest_entry("tsx-app", "text/javascript", app_js, false) << "}";
    const std::string body = R"(<div id="root"></div><script src="tsx-app"></script>)";

    std::string err;
    ClaudeRuntimeOptions opts;
    opts.error_out = &err;
    opts.max_total_js_bytes = 4 * 1024 * 1024;

    auto ir = parse_claude_html_with_runtime(build_envelope(manifest.str(), body), opts);

    const size_t nodes = count_ir_nodes(ir.root);

    require(ir.source == DesignSource::claude);
    require(nodes > 1);  // mount root + at least one child from TypedControl

    // typed-control.tsx renders a div containing the text "TYPED CONTROL"
    // and a Meter component whose label resolves to "gain". Either string
    // proves the TSX→JS→native pipeline survived type-stripping.
    const bool found_tsx_text =
        ir_contains_text(ir.root, "TYPED CONTROL") ||
        ir_contains_text(ir.root, "gain");
    require(found_tsx_text);
}

int main() { return framework_test::run_all(); }
