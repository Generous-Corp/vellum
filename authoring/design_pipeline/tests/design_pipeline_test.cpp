#include <vellum/authoring/design_pipeline.hpp>

#include <vellum/view/design_codegen.hpp>

#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>

namespace {

namespace da = vellum::authoring;
namespace view = vellum::view;

int failures = 0;

#define CHECK(condition)                                                                   \
    do {                                                                                   \
        if (!(condition)) {                                                                \
            std::cerr << __FILE__ << ":" << __LINE__ << " check failed: " #condition "\n"; \
            ++failures;                                                                    \
        }                                                                                  \
    } while (false)

std::string read_fixture(const char* name) {
    std::ifstream in(std::string(VELLUM_DESIGN_FIXTURE_DIR) + "/" + name, std::ios::binary);
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

da::ImportResult import_fixture() {
    da::ImportInput input;
    input.source = da::DesignSource::figma_plugin;
    input.payload = read_fixture("pulp-emitter-generic.input.json");
    return da::import_design(input);
}

const std::string* find_file(const da::EmitResult& result, const std::string& role) {
    for (const auto& file : result.files)
        if (file.role == role)
            return &file.content;
    return nullptr;
}

bool has_code(const std::vector<da::Diagnostic>& diagnostics, const std::string& code) {
    for (const auto& d : diagnostics)
        if (d.code == code)
            return true;
    return false;
}

void test_api_version() {
    CHECK(da::design_pipeline_api_version() == 1);
}

void test_import_matches_parser() {
    da::ImportResult result = import_fixture();
    CHECK(result.status == da::Status::ok);
    CHECK(result.ir.has_value());
    CHECK(result.source == da::DesignSource::figma_plugin);
    const view::DesignIR direct = view::parse_figma_plugin_json(read_fixture("pulp-emitter-generic.input.json"));
    CHECK(result.ir && result.ir->root.children.size() == direct.root.children.size());
    CHECK(result.ir && !result.ir->root.children.empty());
}

void test_emit_golden() {
    da::ImportResult imported = import_fixture();
    CHECK(imported.ir.has_value());
    if (!imported.ir)
        return;
    const view::DesignIR& ir = *imported.ir;
    da::EmitOptions options;
    options.asset_manifest = ir.asset_manifest;

    da::EmitResult js = da::emit_design(ir, da::EmitTarget::js, options);
    CHECK(js.status == da::Status::ok);
    CHECK(js.target == da::EmitTarget::js);
    const std::string* js_text = find_file(js, "js");
    CHECK(js_text && *js_text == view::generate_pulp_js(ir));
    CHECK(js_text && !js_text->empty());

    da::EmitResult cpp = da::emit_design(ir, da::EmitTarget::cpp, options);
    CHECK(cpp.status == da::Status::ok);
    const view::CppExportResult direct_cpp = view::generate_pulp_cpp(ir, ir.asset_manifest);
    CHECK(cpp.files.size() == 3);
    const std::string* header = find_file(cpp, "cpp.header");
    const std::string* source = find_file(cpp, "cpp.source");
    const std::string* manifest = find_file(cpp, "cpp.binding_manifest");
    CHECK(header && *header == direct_cpp.header);
    CHECK(source && *source == direct_cpp.source);
    CHECK(manifest && *manifest == direct_cpp.binding_manifest);
    CHECK(header && !header->empty());

    da::EmitResult swift = da::emit_design(ir, da::EmitTarget::swift, options);
    CHECK(swift.status == da::Status::ok);
    const view::SwiftExportResult direct_swift = view::generate_pulp_swift(ir, ir.asset_manifest);
    CHECK(swift.files.size() == 3);
    const std::string* view_source = find_file(swift, "swift.view");
    const std::string* theme = find_file(swift, "swift.theme");
    const std::string* swift_manifest = find_file(swift, "swift.binding_manifest");
    CHECK(view_source && *view_source == direct_swift.view_source);
    CHECK(theme && *theme == direct_swift.theme_source);
    CHECK(swift_manifest && *swift_manifest == direct_swift.binding_manifest);
    CHECK(view_source && !view_source->empty());
    CHECK(view_source && cpp.files[0].content != *view_source);
}

void test_emit_options_pass_through() {
    da::ImportResult imported = import_fixture();
    CHECK(imported.ir.has_value());
    if (!imported.ir)
        return;
    da::EmitOptions options;
    options.asset_manifest = imported.ir->asset_manifest;
    options.cpp.function_name = "build_custom_ui";
    options.swift.root_view_name = "CustomView";
    da::EmitResult cpp = da::emit_design(*imported.ir, da::EmitTarget::cpp, options);
    const std::string* source = find_file(cpp, "cpp.source");
    CHECK(source && source->find("build_custom_ui") != std::string::npos);
    da::EmitResult swift = da::emit_design(*imported.ir, da::EmitTarget::swift, options);
    const std::string* view_source = find_file(swift, "swift.view");
    CHECK(view_source && view_source->find("CustomView") != std::string::npos);
}

void test_emit_unknown_target_fails() {
    da::ImportResult imported = import_fixture();
    CHECK(imported.ir.has_value());
    if (!imported.ir)
        return;
    da::EmitResult result =
        da::emit_design(*imported.ir, static_cast<da::EmitTarget>(99), da::EmitOptions{});
    CHECK(result.status == da::Status::failed);
    CHECK(result.files.empty());
    CHECK(has_code(result.diagnostics, "emit.unknown-target"));
}

void test_json_is_stable_and_versioned() {
    const std::string first = da::import_result_to_json(import_fixture());
    const std::string second = da::import_result_to_json(import_fixture());
    CHECK(first == second);
    CHECK(first.rfind("{\n  \"schema_version\": 1,\n  \"api_version\": 1,\n  \"status\": \"ok\",", 0) == 0);
    const std::size_t diagnostics = first.find("\"diagnostics\"");
    const std::size_t losses = first.find("\"losses\"");
    const std::size_t summary = first.find("\"loss_summary\"");
    const std::size_t report = first.find("\"report\"");
    CHECK(diagnostics != std::string::npos && diagnostics < losses);
    CHECK(losses < summary && summary < report);
}

void test_json_escapes_text() {
    da::ImportResult result;
    result.status = da::Status::failed;
    da::Diagnostic d;
    d.code = "import.parser-exception";
    d.severity = da::Severity::error;
    d.message = "quote \" backslash \\ newline \n tab \t bell \a";
    result.diagnostics.push_back(d);
    const std::string json = da::import_result_to_json(result);
    CHECK(json.find("quote \\\" backslash \\\\ newline \\n tab \\t bell \\u0007") != std::string::npos);
    CHECK(json.find("\"ir\": {\"present\": false}") != std::string::npos);
}

void test_json_counts_alternate_frames_and_keeps_numbers_finite() {
    da::ImportResult result;
    result.status = da::Status::ok;
    result.ir.emplace();
    result.ir->root.children.resize(1);
    result.ir->root.alternate_frames.resize(2);
    result.ir->root.children[0].alternate_frames.resize(1);
    view::ImportReportEntry entry;
    entry.source_node_id = "1:1";
    entry.confidence_score = std::numeric_limits<float>::quiet_NaN();
    result.report.controls.push_back(entry);
    entry.confidence_score = std::numeric_limits<float>::infinity();
    result.report.controls.push_back(entry);
    const std::string json = da::import_result_to_json(result);
    CHECK(json.find("\"node_count\": 5") != std::string::npos);
    CHECK(json.find("\"confidence_score\": null") != std::string::npos);
    CHECK(json.find("nan") == std::string::npos && json.find("inf") == std::string::npos);
}

void test_asset_dir_collects_local_assets_without_failing() {
    da::ImportInput input;
    input.source = da::DesignSource::figma_plugin;
    input.payload = read_fixture("pulp-emitter-generic.input.json");
    input.asset_dir = VELLUM_DESIGN_FIXTURE_DIR;
    da::ImportResult result = da::import_design(input);
    CHECK(result.status != da::Status::failed);
    CHECK(result.ir.has_value());
}

void test_malformed_payload_fails_without_throwing() {
    const da::DesignSource json_sources[] = {da::DesignSource::figma_json, da::DesignSource::figma_plugin,
                                             da::DesignSource::pencil_json};
    for (da::DesignSource source : json_sources) {
        da::ImportInput input;
        input.source = source;
        input.payload = "{not json";
        da::ImportResult result;
        bool threw = false;
        try {
            result = da::import_design(input);
        } catch (...) {
            threw = true;
        }
        CHECK(!threw);
        CHECK(result.status == da::Status::failed);
        CHECK(!result.ir.has_value());
        CHECK(has_code(result.diagnostics, "import.parser-exception"));
        CHECK(result.diagnostics.size() == 1 && result.diagnostics[0].severity == da::Severity::error);
        CHECK(da::import_result_to_json(result).find("\"status\": \"failed\"") != std::string::npos);
    }
}

void test_empty_payload_and_unknown_source_fail() {
    da::ImportInput input;
    input.payload = " \n\t";
    da::ImportResult empty = da::import_design(input);
    CHECK(empty.status == da::Status::failed);
    CHECK(has_code(empty.diagnostics, "import.empty-payload"));

    input.payload = "{}";
    input.source = static_cast<da::DesignSource>(99);
    da::ImportResult unknown = da::import_design(input);
    CHECK(unknown.status == da::Status::failed);
    CHECK(has_code(unknown.diagnostics, "import.unknown-source"));
}

void test_unsupported_feature_is_partial_with_a_loss() {
    da::ImportInput input;
    input.source = da::DesignSource::stitch_html;
    input.payload = "<<<<";
    da::ImportResult result = da::import_design(input);
    CHECK(result.status == da::Status::partial);
    CHECK(result.ir.has_value());
    CHECK(!result.losses.empty());
    CHECK(result.loss_summary.unsupported >= 1);
    CHECK(result.loss_summary.total == static_cast<int>(result.losses.size()));
    CHECK(!result.losses.empty() && result.losses[0].code == "loss.fallback-used");
    CHECK(has_code(result.diagnostics, "ir.fallback-used"));
    const std::string json = da::import_result_to_json(result);
    CHECK(json.find("\"status\": \"partial\"") != std::string::npos);
    CHECK(json.find("\"code\": \"loss.fallback-used\"") != std::string::npos);
}

void test_designmd_diagnostics_are_mapped() {
    da::ImportInput input;
    input.source = da::DesignSource::designmd;
    input.payload = "no frontmatter here\n";
    da::ImportResult result = da::import_design(input);
    CHECK(result.ir.has_value());
    CHECK(has_code(result.diagnostics, "designmd.no-frontmatter"));
}

}  // namespace

int main() {
    test_api_version();
    test_import_matches_parser();
    test_emit_golden();
    test_emit_options_pass_through();
    test_emit_unknown_target_fails();
    test_json_is_stable_and_versioned();
    test_json_escapes_text();
    test_json_counts_alternate_frames_and_keeps_numbers_finite();
    test_asset_dir_collects_local_assets_without_failing();
    test_malformed_payload_fails_without_throwing();
    test_empty_payload_and_unknown_source_fail();
    test_unsupported_feature_is_partial_with_a_loss();
    test_designmd_diagnostics_are_mapped();
    if (failures != 0)
        std::cerr << failures << " check(s) failed\n";
    else
        std::cout << "design pipeline: all checks passed\n";
    return failures == 0 ? 0 : 1;
}
