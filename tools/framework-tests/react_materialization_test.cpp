// Exercises JSX snapshot materialization and reuse of verified browser documents.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/design_sources.hpp>
#include <vellum/view/js_engine.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/scripted_ui_prewarm.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <chrono>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>

using namespace vellum::view;
using vellum::state::StateStore;

namespace {

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

std::string js_single_quoted(std::string value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (char c : value) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '\'': out += "\\'"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            default: out += c; break;
        }
    }
    return out;
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

FRAMEWORK_TEST(native_bundle_snapshots_into_design_ir) {
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

FRAMEWORK_TEST(native_snapshot_applies_viewport_layout) {
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

// A materialized document with one verified asset and one inline script past
// the bytecode-cache threshold. `tag` makes a byte-distinct document.
std::string reopen_sidecar(const std::string& tag) {
    std::string pad(3000, 'x');
    return R"JSON({
      "schema":"pulp-materialized-browser-document-v1",
      "version":1,
      "html":"<html><body><script src=\"pulp-materialized-asset-93a17c7b5a173be2da95f76cb62a26ae30c0e13ce230acd243be63023258bf82\"></script><script>globalThis.__inlineRuns = (globalThis.__inlineRuns || 0) + 1; /* )JSON" + tag + pad + R"JSON( */</script></body></html>",
      "assets":[{
        "id":"pulp-materialized-asset-93a17c7b5a173be2da95f76cb62a26ae30c0e13ce230acd243be63023258bf82",
        "mime_type":"text/javascript",
        "byte_length":20,
        "data_base64":"Z2xvYmFsVGhpcy5va1RydWU9MTs=",
        "sha256":"93a17c7b5a173be2da95f76cb62a26ae30c0e13ce230acd243be63023258bf82"
      }]
    })JSON";
}

// Import `sidecar` into a fresh QuickJS realm, as a reopened editor does.
// Returns the bytecode-cache counters across the import call alone.
struct ImportDelta { std::uint64_t compiles = 0; std::uint64_t hits = 0; };
ImportDelta import_in_fresh_realm(const std::string& sidecar) {
    ScriptEngine engine(JsEngineType::quickjs);
    View root;
    StateStore store;
    WidgetBridge bridge(engine, root, store);
    bridge.install_runtime_import_handlers();
    const auto before = script_bytecode_cache_stats();
    engine.evaluate("__pulpRuntimeImport__('" + js_single_quoted(sidecar) +
                    "','materialized-browser');");
    const auto after = script_bytecode_cache_stats();
    require(engine.evaluate("String(globalThis.__pulpRuntimeImportErr__ || '')")
                .getWithDefault<std::string>("").empty());
    CHECK(engine.evaluate("globalThis.okTrue === 1").getWithDefault(false));
    CHECK(engine.evaluate("globalThis.__inlineRuns").getWithDefault<int>(0) == 1);
    return {after.compiles - before.compiles, after.hits - before.hits};
}


FRAMEWORK_TEST(reopened_editors_reuse_verified_browser_document) {
    // Every editor open imports the same captured document into a new realm.
    // The decode + SHA-256 verification runs on the first open only; each
    // reopen reuses the verified bundle and the inline script's bytecode.
    clear_materialized_document_cache();
    clear_script_bytecode_cache();
    const auto doc = reopen_sidecar("a");

    const auto first = import_in_fresh_realm(doc);
    CHECK(first.compiles == 1);  // the inline script
    auto stats = materialized_document_cache_stats();
    CHECK(stats.verifies == 1);
    CHECK(stats.hits == 0);

    const auto second = import_in_fresh_realm(doc);
    CHECK(second.compiles == 0);
    CHECK(second.hits == 1);
    stats = materialized_document_cache_stats();
    CHECK(stats.verifies == 1);
    CHECK(stats.hits == 1);
    CHECK(stats.entries == 1);

    // Control: a byte-distinct document is verified on its own, never a hit.
    import_in_fresh_realm(reopen_sidecar("b"));
    stats = materialized_document_cache_stats();
    CHECK(stats.verifies == 2);
    CHECK(stats.hits == 1);
    CHECK(stats.entries == 2);

    clear_materialized_document_cache();
    clear_script_bytecode_cache();
}

FRAMEWORK_TEST(prewarm_reuses_verified_document_and_compiled_scripts) {
    // A plug-in prewarms at instantiation; the editor's first import then
    // neither verifies the document nor compiles its scripts.
    clear_materialized_document_cache();
    clear_script_bytecode_cache();
    const auto doc = reopen_sidecar("prewarm");
    std::string runtime = "var __prewarmRuntime = 0;\n";
    while (runtime.size() < 16 * 1024)
        runtime += "__prewarmRuntime += 1; // stands in for a bundled UI runtime\n";

    const auto before = scripted_ui_prewarm_stats();
    prewarm_scripted_ui({{runtime}, {doc}});
    require(wait_for_scripted_ui_prewarm(std::chrono::seconds(20)));
    const auto after = scripted_ui_prewarm_stats();
#if defined(_WIN32)
    CHECK(after.requests == before.requests);
    CHECK(after.documents_verified == before.documents_verified);
    CHECK(after.documents_rejected == before.documents_rejected);
    CHECK(after.scripts_compiled == before.scripts_compiled);
#else
    CHECK(after.requests == before.requests + 1);
    CHECK(after.documents_verified == before.documents_verified + 1);
    CHECK(after.documents_rejected == before.documents_rejected);
    CHECK(after.scripts_compiled == before.scripts_compiled + 2);  // runtime + inline
#endif
    // Compiled as WidgetBridge::load_script() evaluates it, so a realm that
    // loads the runtime reads the prewarmed bytecode instead of compiling.
#if defined(_WIN32)
    CHECK(!script_bytecode_cached(WidgetBridge::loaded_script_source(runtime)));
#else
    CHECK(script_bytecode_cached(WidgetBridge::loaded_script_source(runtime)));
#endif
    CHECK(!script_bytecode_cached(runtime));
#if defined(_WIN32)
    CHECK(materialized_document_cache_stats().verifies == 0);
#else
    CHECK(materialized_document_cache_stats().verifies == 1);
#endif
    {
        ScriptEngine engine(JsEngineType::quickjs);
        View root;
        StateStore store;
        WidgetBridge bridge(engine, root, store);
        const auto before_load = script_bytecode_cache_stats();
        bridge.load_script(runtime);
        const auto after_load = script_bytecode_cache_stats();
#if defined(_WIN32)
        CHECK(after_load.compiles == before_load.compiles + 1);
#else
        CHECK(after_load.compiles == before_load.compiles);
        CHECK(after_load.hits == before_load.hits + 1);
#endif
    }

    const auto first_open = import_in_fresh_realm(doc);
#if defined(_WIN32)
    CHECK(first_open.compiles >= 1);
#else
    CHECK(first_open.compiles == 0);
    CHECK(first_open.hits == 1);
#endif
    const auto stats = materialized_document_cache_stats();
    CHECK(stats.verifies == 1);
#if defined(_WIN32)
    CHECK(stats.hits == 0);
#else
    CHECK(stats.hits == 1);
#endif

    // Asking again is free: everything is cached.
    prewarm_scripted_ui({{runtime}, {doc}});
    require(wait_for_scripted_ui_prewarm(std::chrono::seconds(20)));
    CHECK(scripted_ui_prewarm_stats().scripts_compiled == after.scripts_compiled);
    CHECK(materialized_document_cache_stats().verifies == 1);

    clear_materialized_document_cache();
    clear_script_bytecode_cache();
}

FRAMEWORK_TEST(rejected_documents_are_never_cached) {
    clear_materialized_document_cache();
    auto doc = reopen_sidecar("a");
    // Corrupt the payload so its SHA-256 no longer matches.
    const auto at = doc.find("Z2xvYmFsVGhpcy5va1RydWU9MTs=");
    require(at != std::string::npos);
    doc.replace(at, 4, "Y2xv");

    for (int open = 0; open < 2; ++open) {
        ScriptEngine engine(JsEngineType::quickjs);
        View root;
        StateStore store;
        WidgetBridge bridge(engine, root, store);
        bridge.install_runtime_import_handlers();
        engine.evaluate("__pulpRuntimeImport__('" + js_single_quoted(doc) +
                        "','materialized-browser');");
        CHECK(engine.evaluate("String(globalThis.__pulpRuntimeImportErr__ || '')")
                  .getWithDefault<std::string>("")
                  .find("invalid materialized browser document") != std::string::npos);
        CHECK(!engine.evaluate("globalThis.okTrue === 1").getWithDefault(false));
    }
    const auto stats = materialized_document_cache_stats();
    CHECK(stats.verifies == 2);
    CHECK(stats.rejected == 2);
    CHECK(stats.hits == 0);
    CHECK(stats.entries == 0);
    clear_materialized_document_cache();
}

void verify_compiled_react(const std::string& path, const std::string& expected_text) {
    const auto source = read_file(path);
    require(!source.empty());
    const auto bundle = parse_jsx_react(source, "ReactMaterialization");
    require(bundle.has_value());
    std::string error;
    ClaudeRuntimeOptions options;
    options.error_out = &error;
    options.max_total_js_bytes = 4 * 1024 * 1024;
    const auto ir = parse_claude_html_with_runtime(synthesize_runtime_envelope(*bundle), options);
    if (!error.empty()) throw std::runtime_error(error);
    require(ir.capture_method == "runtime_native_snapshot");
    require(ir_contains_text(ir.root, expected_text));
}

} // namespace

int main(int argc, char** argv) {
    if (argc == 1) return framework_test::run_all();
    if (argc != 3) return 2;
    try {
        verify_compiled_react(argv[1], argv[2]);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
