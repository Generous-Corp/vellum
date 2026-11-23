#include <vellum/authoring/design_pipeline.hpp>

#include <vellum/view/design_ir_io.hpp>
#include <vellum/view/design_sources.hpp>

#include <cmath>
#include <cstdio>
#include <exception>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace vellum::authoring {

namespace {

constexpr int kApiVersion = 1;
constexpr int kJsonSchemaVersion = 1;

namespace view = vellum::view;

Severity map_severity(view::ImportDiagnosticSeverity severity) {
    switch (severity) {
        case view::ImportDiagnosticSeverity::info: return Severity::info;
        case view::ImportDiagnosticSeverity::warning: return Severity::warning;
        case view::ImportDiagnosticSeverity::error: return Severity::error;
    }
    return Severity::error;
}

Severity map_severity(view::DesignMdSeverity severity) {
    switch (severity) {
        case view::DesignMdSeverity::info: return Severity::info;
        case view::DesignMdSeverity::warning: return Severity::warning;
        case view::DesignMdSeverity::error: return Severity::error;
    }
    return Severity::error;
}

const char* kind_code(view::ImportDiagnosticKind kind) {
    switch (kind) {
        case view::ImportDiagnosticKind::unsupported_property: return "unsupported-property";
        case view::ImportDiagnosticKind::unsupported_node: return "unsupported-node";
        case view::ImportDiagnosticKind::unresolved_asset: return "unresolved-asset";
        case view::ImportDiagnosticKind::capture_partial: return "capture-partial";
        case view::ImportDiagnosticKind::fallback_used: return "fallback-used";
        default: return nullptr;
    }
}

Diagnostic make_diagnostic(std::string code, Severity severity, std::string message,
                           std::string ir_path = {}, std::string node_id = {}) {
    Diagnostic d;
    d.code = std::move(code);
    d.severity = severity;
    d.message = std::move(message);
    d.ir_path = std::move(ir_path);
    d.node_id = std::move(node_id);
    return d;
}

void index_nodes(const view::IRNode& node, const std::string& path,
                 std::unordered_map<std::string, std::string>& by_id) {
    if (node.source_node_id && !node.source_node_id->empty())
        by_id.emplace(*node.source_node_id, path);
    for (std::size_t i = 0; i < node.children.size(); ++i)
        index_nodes(node.children[i], path + "/children[" + std::to_string(i) + "]", by_id);
    for (std::size_t i = 0; i < node.alternate_frames.size(); ++i) {
        index_nodes(node.alternate_frames[i], path + "/alternate_frames[" + std::to_string(i) + "]",
                    by_id);
    }
}

std::size_t count_nodes(const view::IRNode& node) {
    std::size_t total = 1;
    for (const auto& child : node.children)
        total += count_nodes(child);
    for (const auto& frame : node.alternate_frames)
        total += count_nodes(frame);
    return total;
}

view::DesignIR parse_source(const ImportInput& input, std::vector<Diagnostic>& diagnostics) {
    switch (input.source) {
        case DesignSource::figma_json: return view::parse_figma_json(input.payload);
        case DesignSource::figma_plugin: return view::parse_figma_plugin_json(input.payload);
        case DesignSource::stitch_html: return view::parse_stitch_html(input.payload);
        case DesignSource::v0_tsx: return view::parse_v0_tsx(input.payload);
        case DesignSource::pencil_json: return view::parse_pencil_json(input.payload);
        case DesignSource::claude_html: return view::parse_claude_html(input.payload);
        case DesignSource::designmd: {
            view::DesignMdParseResult parsed = view::parse_designmd(input.payload);
            for (const auto& d : parsed.diagnostics) {
                diagnostics.push_back(make_diagnostic("designmd." + d.code, map_severity(d.severity),
                                                      d.message, d.path));
            }
            return std::move(parsed.ir);
        }
    }
    throw std::invalid_argument("unknown design source");
}

bool is_known_source(DesignSource source) {
    switch (source) {
        case DesignSource::figma_json:
        case DesignSource::figma_plugin:
        case DesignSource::stitch_html:
        case DesignSource::v0_tsx:
        case DesignSource::pencil_json:
        case DesignSource::claude_html:
        case DesignSource::designmd: return true;
    }
    return false;
}

bool blank(const std::string& text) {
    return text.find_first_not_of(" \t\r\n") == std::string::npos;
}

void add_loss(ImportResult& result, std::string code, std::string path, std::string node_id,
              std::string detail) {
    LossEntry entry;
    entry.code = std::move(code);
    entry.ir_path = std::move(path);
    entry.node_id = std::move(node_id);
    entry.detail = std::move(detail);
    result.losses.push_back(std::move(entry));
}

void collect_losses(ImportResult& result, float threshold) {
    std::unordered_map<std::string, std::string> paths;
    index_nodes(result.ir->root, "root", paths);
    auto path_of = [&](const std::string& id) {
        auto it = paths.find(id);
        return it == paths.end() ? std::string() : it->second;
    };
    result.report = view::collect_import_report(result.ir->root, threshold);
    for (const auto& control : result.report.controls) {
        const std::string path = path_of(control.source_node_id);
        if (!control.conflict_signals.empty()) {
            std::string detail = control.kind + ":";
            for (const auto& signal : control.conflict_signals)
                detail += " " + signal;
            add_loss(result, "report.conflicted", path, control.source_node_id, std::move(detail));
        }
        if (control.confidence_score < threshold) {
            add_loss(result, "report.low-confidence", path, control.source_node_id,
                     control.kind + " resolved below the confidence threshold");
        }
        if (control.resolution_rung == 5) {
            add_loss(result, "report.unresolved", path, control.source_node_id,
                     control.kind + " resolved only as an inert element");
        }
    }
    result.loss_summary.conflicted = result.report.conflicted;
    result.loss_summary.low_confidence = result.report.low_confidence;
    result.loss_summary.unresolved = result.report.unresolved;
    for (const auto& d : result.ir->diagnostics) {
        const char* kind = kind_code(d.kind);
        if (!kind || d.severity == view::ImportDiagnosticSeverity::info)
            continue;
        add_loss(result, std::string("loss.") + kind, d.path, d.anchor_id.value_or(""),
                 d.message);
        ++result.loss_summary.unsupported;
    }
    result.loss_summary.total = static_cast<int>(result.losses.size());
}

void fail(ImportResult& result, const char* code, const char* message) noexcept {
    result.status = Status::failed;
    result.ir.reset();
    result.report = {};
    result.losses.clear();
    result.loss_summary = {};
    try {
        result.diagnostics.clear();
        result.diagnostics.push_back(make_diagnostic(code, Severity::error, message));
    } catch (...) {
        result.diagnostics.clear();
    }
}

std::string escape(std::string_view text) {
    std::string out;
    out.reserve(text.size() + 2);
    out += '"';
    for (unsigned char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buffer[8];
                    std::snprintf(buffer, sizeof buffer, "\\u%04x", c);
                    out += buffer;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
    return out;
}

std::string number(double value) {
    if (!std::isfinite(value))
        return "null";
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream.setf(std::ios::fixed);
    stream.precision(4);
    stream << value;
    return stream.str();
}

void append_diagnostics(std::string& out, const std::vector<Diagnostic>& diagnostics) {
    out += "[";
    for (std::size_t i = 0; i < diagnostics.size(); ++i) {
        const auto& d = diagnostics[i];
        out += i ? ",\n    {" : "\n    {";
        out += "\"code\": " + escape(d.code);
        out += ", \"severity\": " + escape(severity_name(d.severity));
        out += ", \"ir_path\": " + escape(d.ir_path);
        out += ", \"node_id\": " + escape(d.node_id);
        out += ", \"message\": " + escape(d.message) + "}";
    }
    out += diagnostics.empty() ? "]" : "\n  ]";
}

void append_losses(std::string& out, const std::vector<LossEntry>& losses) {
    out += "[";
    for (std::size_t i = 0; i < losses.size(); ++i) {
        const auto& l = losses[i];
        out += i ? ",\n    {" : "\n    {";
        out += "\"code\": " + escape(l.code);
        out += ", \"ir_path\": " + escape(l.ir_path);
        out += ", \"node_id\": " + escape(l.node_id);
        out += ", \"detail\": " + escape(l.detail) + "}";
    }
    out += losses.empty() ? "]" : "\n  ]";
}

void fail_emit(EmitResult& result, const char* message) noexcept {
    result.files.clear();
    result.losses.clear();
    result.status = Status::failed;
    try {
        result.diagnostics.clear();
        result.diagnostics.push_back(make_diagnostic("emit.generator-exception", Severity::error, message));
    } catch (...) {
        result.diagnostics.clear();
    }
}

void fidelity_to_losses(const std::vector<view::FidelityIssue>& issues, EmitResult& result) {
    for (const auto& issue : issues) {
        if (issue.informational) {
            result.diagnostics.push_back(make_diagnostic("fidelity." + issue.kind, Severity::info,
                                                         issue.detail, {}, issue.node_id));
            continue;
        }
        LossEntry loss;
        loss.code = "fidelity." + issue.kind;
        loss.node_id = issue.node_id;
        loss.detail = issue.detail;
        result.losses.push_back(std::move(loss));
    }
}

}  // namespace

int design_pipeline_api_version() noexcept { return kApiVersion; }

const char* design_source_name(DesignSource source) noexcept {
    switch (source) {
        case DesignSource::figma_json: return "figma_json";
        case DesignSource::figma_plugin: return "figma_plugin";
        case DesignSource::stitch_html: return "stitch_html";
        case DesignSource::v0_tsx: return "v0_tsx";
        case DesignSource::pencil_json: return "pencil_json";
        case DesignSource::claude_html: return "claude_html";
        case DesignSource::designmd: return "designmd";
    }
    return "unknown";
}

const char* status_name(Status status) noexcept {
    switch (status) {
        case Status::ok: return "ok";
        case Status::partial: return "partial";
        case Status::failed: return "failed";
    }
    return "unknown";
}

const char* severity_name(Severity severity) noexcept {
    switch (severity) {
        case Severity::info: return "info";
        case Severity::warning: return "warning";
        case Severity::error: return "error";
    }
    return "unknown";
}

const char* emit_target_name(EmitTarget target) noexcept {
    switch (target) {
        case EmitTarget::js: return "js";
        case EmitTarget::cpp: return "cpp";
        case EmitTarget::swift: return "swift";
    }
    return "unknown";
}

ImportResult import_design(const ImportInput& input) noexcept {
    ImportResult result;
    result.source = input.source;
    try {
        if (!is_known_source(input.source)) {
            fail(result, "import.unknown-source", "the design source is not one this version supports");
            return result;
        }
        if (blank(input.payload)) {
            fail(result, "import.empty-payload", "the design payload is empty");
            return result;
        }
        std::vector<Diagnostic> parser_diagnostics;
        result.ir = parse_source(input, parser_diagnostics);
        if (!input.asset_dir.empty()) {
            view::DesignIrAssetOptions assets;
            assets.base_directory = input.asset_dir;
            view::refresh_design_ir_asset_manifest(*result.ir, assets);
        }
        result.diagnostics = std::move(parser_diagnostics);
        for (const auto& d : result.ir->diagnostics) {
            result.diagnostics.push_back(make_diagnostic("ir." + d.code, map_severity(d.severity),
                                                         d.message, d.path,
                                                         d.anchor_id.value_or("")));
        }
        collect_losses(result, input.low_confidence_threshold);
        bool lossy = !result.losses.empty();
        for (const auto& d : result.diagnostics)
            lossy = lossy || d.severity != Severity::info;
        result.status = lossy ? Status::partial : Status::ok;
    } catch (const std::exception& error) {
        fail(result, "import.parser-exception", error.what());
    } catch (...) {
        fail(result, "import.parser-exception", "the parser threw a non-standard exception");
    }
    return result;
}

EmitResult emit_design(const view::DesignIR& ir, EmitTarget target, const EmitOptions& options) noexcept {
    EmitResult result;
    result.target = target;
    try {
        std::vector<view::FidelityIssue> issues;
        switch (target) {
            case EmitTarget::js: {
                view::CodeGenOptions js = options.js;
                js.fidelity_report = &issues;
                result.files.push_back({"js", view::generate_pulp_js(ir, js)});
                break;
            }
            case EmitTarget::cpp: {
                view::CppExportOptions cpp = options.cpp;
                cpp.fidelity_report = &issues;
                view::CppExportResult out = view::generate_pulp_cpp(ir, options.asset_manifest, cpp);
                result.files.push_back({"cpp.header", std::move(out.header)});
                result.files.push_back({"cpp.source", std::move(out.source)});
                result.files.push_back({"cpp.binding_manifest", std::move(out.binding_manifest)});
                break;
            }
            case EmitTarget::swift: {
                view::SwiftExportOptions swift = options.swift;
                swift.fidelity_report = &issues;
                view::SwiftExportResult out =
                    view::generate_pulp_swift(ir, options.asset_manifest, swift);
                result.files.push_back({"swift.view", std::move(out.view_source)});
                result.files.push_back({"swift.theme", std::move(out.theme_source)});
                result.files.push_back({"swift.binding_manifest", std::move(out.binding_manifest)});
                break;
            }
            default:
                result.diagnostics.push_back(
                    make_diagnostic("emit.unknown-target", Severity::error, "the emit target is not supported"));
                return result;
        }
        fidelity_to_losses(issues, result);
        result.status = result.losses.empty() ? Status::ok : Status::partial;
    } catch (const std::exception& error) {
        fail_emit(result, error.what());
    } catch (...) {
        fail_emit(result, "the generator threw a non-standard exception");
    }
    return result;
}

std::string import_result_to_json(const ImportResult& result) {
    std::string out = "{\n";
    out += "  \"schema_version\": " + std::to_string(kJsonSchemaVersion) + ",\n";
    out += "  \"api_version\": " + std::to_string(kApiVersion) + ",\n";
    out += "  \"status\": " + escape(status_name(result.status)) + ",\n";
    out += "  \"source\": " + escape(design_source_name(result.source)) + ",\n";
    out += "  \"ir\": {\"present\": ";
    out += result.ir ? "true" : "false";
    if (result.ir) {
        out += ", \"version\": " + std::to_string(result.ir->version);
        out += ", \"node_count\": " + std::to_string(count_nodes(result.ir->root));
    }
    out += "},\n";
    out += "  \"diagnostics\": ";
    append_diagnostics(out, result.diagnostics);
    out += ",\n  \"losses\": ";
    append_losses(out, result.losses);
    out += ",\n  \"loss_summary\": {";
    out += "\"conflicted\": " + std::to_string(result.loss_summary.conflicted);
    out += ", \"low_confidence\": " + std::to_string(result.loss_summary.low_confidence);
    out += ", \"unresolved\": " + std::to_string(result.loss_summary.unresolved);
    out += ", \"unsupported\": " + std::to_string(result.loss_summary.unsupported);
    out += ", \"total\": " + std::to_string(result.loss_summary.total) + "},\n";
    out += "  \"report\": {\"ok\": ";
    out += result.report.ok() ? "true" : "false";
    out += ", \"controls\": [";
    for (std::size_t i = 0; i < result.report.controls.size(); ++i) {
        const auto& c = result.report.controls[i];
        out += i ? ",\n    {" : "\n    {";
        out += "\"source_node_id\": " + escape(c.source_node_id);
        out += ", \"kind\": " + escape(c.kind);
        out += ", \"resolution_rung\": " + std::to_string(c.resolution_rung);
        out += ", \"confidence_score\": " + number(c.confidence_score);
        out += ", \"conflict_signals\": [";
        for (std::size_t j = 0; j < c.conflict_signals.size(); ++j)
            out += (j ? ", " : "") + escape(c.conflict_signals[j]);
        out += "], \"verification_pass\": ";
        out += c.verification_pass ? "true}" : "false}";
    }
    out += result.report.controls.empty() ? "]}\n}\n" : "\n  ]}\n}\n";
    return out;
}

}  // namespace vellum::authoring
