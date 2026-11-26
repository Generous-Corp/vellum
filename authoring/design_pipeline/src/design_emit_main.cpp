// vellum-design-emit: lower a design to a baked C++ or SwiftUI view artifact.
//
// The CLI reaches this through `vellum import ... --mode baked --emit cpp|swiftui`.
// It imports the source through the stable design pipeline (or reads a
// DesignIR file as is), lowers it with emit_design(), and writes the emitted
// files next to the requested output. Output naming, the baked-mode rule, the
// DESIGN.md refusal and the SwiftUI strict-fidelity gate follow the importer
// this command replaces, so existing build scripts keep their file names.
//
// Exit codes: 0 written (or printed with --dry-run), 1 input or write failure,
// 2 usage or a refused source, 4 strict-fidelity failure.

#include "design_emit_assets.hpp"

#include <vellum/authoring/design_pipeline.hpp>
#include <vellum/view/design_ir_io.hpp>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace {

namespace fs = std::filesystem;
namespace da = vellum::authoring;

struct Options {
    std::string source_type;
    fs::path input;
    std::string emit;
    std::string output;
    bool dry_run = false;
    bool strict_fidelity = false;
    bool include_comments = true;
    bool include_tokens = true;
    bool json = false;
};

struct CppOutputPaths {
    fs::path source;
    fs::path header;
    fs::path binding_manifest;
    std::string include_name;
};

// A directory or extensionless output gets imported_ui.{cpp,hpp} inside it; a
// header path names the header and derives the source; anything else names the
// source and derives the header. The binding manifest sits beside the source.
CppOutputPaths resolve_cpp_output_paths(const std::string& output_file) {
    fs::path requested(output_file.empty() ? "imported_ui.cpp" : output_file);
    std::error_code ec;
    const bool existing_dir = fs::is_directory(requested, ec);
    const auto ext = requested.extension().string();
    CppOutputPaths paths;
    if (existing_dir || ext.empty()) {
        paths.source = requested / "imported_ui.cpp";
        paths.header = requested / "imported_ui.hpp";
    } else if (ext == ".hpp" || ext == ".hh" || ext == ".h") {
        paths.header = requested;
        paths.source = requested;
        paths.source.replace_extension(".cpp");
    } else {
        paths.source = requested;
        paths.header = requested;
        paths.header.replace_extension(".hpp");
    }
    paths.binding_manifest = paths.source;
    paths.binding_manifest.replace_extension(".bindings.json");
    paths.include_name = paths.header.filename().string();
    return paths;
}

struct SwiftOutputPaths {
    fs::path view;
    fs::path theme;
    fs::path binding_manifest;
    std::string root_view_name;
    std::string theme_type_name;
};

// A directory or extensionless output gets ImportedPulpView.swift inside it.
// The theme type and file are derived from the view name, so two imports in one
// Swift target never declare the same theme symbol or overwrite one file.
SwiftOutputPaths resolve_swift_output_paths(const std::string& output_file) {
    fs::path requested(output_file.empty() ? "ImportedPulpView.swift" : output_file);
    std::error_code ec;
    const bool existing_dir = fs::is_directory(requested, ec);
    const auto ext = requested.extension().string();
    SwiftOutputPaths paths;
    paths.view = (existing_dir || ext.empty()) ? requested / "ImportedPulpView.swift" : requested;
    paths.root_view_name = paths.view.stem().string();
    if (paths.root_view_name.empty()) paths.root_view_name = "ImportedPulpView";
    paths.theme_type_name = paths.root_view_name + "Theme";
    paths.theme = paths.view.parent_path() / (paths.root_view_name + "Theme.swift");
    paths.binding_manifest = paths.view;
    paths.binding_manifest.replace_extension(".bindings.json");
    return paths;
}

void usage(std::ostream& out) {
    out << "usage: vellum-design-emit --source-type <design-ir|figma|figma-plugin|pencil|v0|designmd|claude-design>\n"
           "                          --input <file> --emit <cpp|swiftui> [--output <path>]\n"
           "                          [--dry-run] [--strict-fidelity] [--no-comments]\n"
           "                          [--no-tokens] [--json]\n";
}

std::optional<std::string> read_file(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return std::nullopt;
    return std::string(std::istreambuf_iterator<char>(in), {});
}

bool write_file(const fs::path& path, const std::string& content) {
    std::error_code ec;
    if (path.has_parent_path()) fs::create_directories(path.parent_path(), ec);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out << content;
    return static_cast<bool>(out);
}

std::string json_string(std::string_view text) {
    std::string out = "\"";
    for (const char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
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
    return out + "\"";
}

struct Written {
    std::string role;
    fs::path path;
    // Set only for --dry-run, so a JSON caller receives what would be written.
    std::optional<std::string> content;
};

int report(const Options& options, int code, std::string_view status, std::string_view message,
           const std::vector<Written>& files, const std::vector<da::LossEntry>& losses) {
    if (options.json) {
        std::ostringstream out;
        out << "{\"ok\":" << (code == 0 ? "true" : "false") << ",\"status\":" << json_string(status)
            << ",\"message\":" << json_string(message) << ",\"emit\":" << json_string(options.emit)
            << ",\"files\":[";
        for (std::size_t i = 0; i < files.size(); ++i) {
            out << (i ? "," : "") << "{\"role\":" << json_string(files[i].role)
                << ",\"path\":" << json_string(files[i].path.string());
            if (files[i].content) out << ",\"content\":" << json_string(*files[i].content);
            out << "}";
        }
        out << "],\"losses\":[";
        for (std::size_t i = 0; i < losses.size(); ++i) {
            out << (i ? "," : "") << "{\"code\":" << json_string(losses[i].code)
                << ",\"node_id\":" << json_string(losses[i].node_id)
                << ",\"detail\":" << json_string(losses[i].detail) << "}";
        }
        out << "]}\n";
        std::cout << out.str();
    } else if (code == 0) {
        std::cout << message << "\n";
    } else {
        std::cerr << "vellum-design-emit: " << message << "\n";
    }
    return code;
}

std::optional<da::DesignSource> pipeline_source(std::string_view name) {
    if (name == "figma") return da::DesignSource::figma_json;
    if (name == "figma-plugin") return da::DesignSource::figma_plugin;
    if (name == "pencil") return da::DesignSource::pencil_json;
    if (name == "v0") return da::DesignSource::v0_tsx;
    if (name == "designmd") return da::DesignSource::designmd;
    if (name == "claude-design") return da::DesignSource::claude_html;
    return std::nullopt;
}

bool has_blocking_asset_diagnostic(const vellum::view::IRAssetManifest& manifest) {
    for (const auto& asset : manifest.assets) {
        for (const auto& diagnostic : asset.diagnostics) {
            if (diagnostic.severity == vellum::view::ImportDiagnosticSeverity::error ||
                diagnostic.code == "asset-network-fetch-disabled")
                return true;
        }
    }
    return false;
}

int run(const Options& options) {
    const bool swift = options.emit == "swiftui";
    if (options.emit != "cpp" && !swift)
        return report(options, 2, "invalid_arguments", "--emit must be cpp or swiftui", {}, {});
    if (options.source_type == "designmd") {
        return report(options, 2, "source_refused",
                      std::string("DESIGN.md is a token spec and cannot emit a baked ") +
                          (swift ? "SwiftUI view" : "C++ view"),
                      {}, {});
    }
    const auto payload = read_file(options.input);
    if (!payload)
        return report(options, 1, "source_not_found", "cannot read " + options.input.string(), {}, {});

    vellum::view::DesignIR ir;
    if (options.source_type == "design-ir") {
        try {
            ir = vellum::view::parse_design_ir_json(*payload);
        } catch (const std::exception& error) {
            return report(options, 1, "import_failed",
                          std::string("cannot parse DesignIR: ") + error.what(), {}, {});
        }
    } else {
        const auto source = pipeline_source(options.source_type);
        if (!source) {
            return report(options, 2, "invalid_arguments",
                          "--source-type " + options.source_type + " cannot emit a baked view", {}, {});
        }
        da::ImportInput input;
        input.source = *source;
        input.payload = *payload;
        input.asset_dir = fs::absolute(options.input).parent_path();
        auto imported = da::import_design(input);
        if (imported.status == da::Status::failed || !imported.ir) {
            std::string message = "import failed";
            for (const auto& d : imported.diagnostics) {
                if (d.severity == da::Severity::error) message += ": " + d.code + " " + d.message;
            }
            return report(options, 1, "import_failed", message, {}, {});
        }
        ir = std::move(*imported.ir);
    }

    if (has_blocking_asset_diagnostic(ir.asset_manifest))
        return report(options, 1, "asset_blocked", "an asset the design references could not be resolved", {}, {});
    da::EmitOptions emit_options;
    std::vector<Written> targets;
    if (swift) {
        const auto paths = resolve_swift_output_paths(options.output);
        emit_options.swift.root_view_name = paths.root_view_name;
        emit_options.swift.theme_type_name = paths.theme_type_name;
        emit_options.swift.include_comments = options.include_comments;
        emit_options.swift.emit_theme = options.include_tokens;
        emit_options.swift.emit_binding_manifest = true;
        targets = {{"swift.view", paths.view}, {"swift.theme", paths.theme},
                   {"swift.binding_manifest", paths.binding_manifest}};
    } else {
        const auto paths = resolve_cpp_output_paths(options.output);
        // Baked C++ embeds asset paths, so absolute ones are copied beside the
        // generated source first.
        std::string localization_error;
        if (!options.dry_run &&
            !da::localize_ir_assets(ir, paths.source.string(), &localization_error))
            return report(options, 1, "asset_localization_failed", localization_error, {}, {});
        emit_options.cpp.header_filename = paths.include_name;
        emit_options.cpp.include_comments = options.include_comments;
        emit_options.cpp.emit_named_tokens = options.include_tokens;
        emit_options.cpp.emit_asset_constants = true;
        targets = {{"cpp.header", paths.header}, {"cpp.source", paths.source},
                   {"cpp.binding_manifest", paths.binding_manifest}};
    }

    emit_options.asset_manifest = ir.asset_manifest;
    const auto emitted = da::emit_design(ir, swift ? da::EmitTarget::swift : da::EmitTarget::cpp, emit_options);
    if (emitted.status == da::Status::failed) {
        std::string message = "emit failed";
        for (const auto& d : emitted.diagnostics) message += ": " + d.code + " " + d.message;
        return report(options, 1, "emit_failed", message, {}, emitted.losses);
    }
    for (const auto& loss : emitted.losses) {
        std::cerr << "fidelity: [" << loss.code << "] " << loss.node_id << ": " << loss.detail << "\n";
    }
    const bool fidelity_failed = swift && options.strict_fidelity && !emitted.losses.empty();
    if (fidelity_failed) {
        std::cerr << "fidelity: " << emitted.losses.size() << " issue(s); failing due to --strict-fidelity\n";
    }

    std::vector<Written> written;
    std::ostringstream printed;
    for (const auto& target : targets) {
        const da::EmittedFile* file = nullptr;
        for (const auto& candidate : emitted.files) {
            if (candidate.role == target.role) file = &candidate;
        }
        if (file == nullptr) {
            return report(options, 1, "emit_failed", "the emitter produced no " + target.role, {},
                          emitted.losses);
        }
        // The SwiftUI theme partition is empty when tokens are not emitted.
        if (file->content.empty() && target.role == "swift.theme") continue;
        if (options.dry_run) {
            printed << "=== " << target.role << " (" << target.path.string() << ") ===\n\n"
                    << file->content << "\n";
        } else if (!fidelity_failed && !write_file(target.path, file->content)) {
            return report(options, 1, "write_failed", "cannot write " + target.path.string(), written,
                          emitted.losses);
        }
        written.push_back(target);
        if (options.dry_run) written.back().content = file->content;
    }
    if (options.dry_run && !options.json) std::cout << printed.str();
    if (fidelity_failed)
        return report(options, 4, "fidelity_failed", "strict fidelity failed", {}, emitted.losses);
    if (options.dry_run && !options.json) return 0;
    std::string message = options.dry_run ? "Would write " : "Wrote ";
    for (std::size_t i = 0; i < written.size(); ++i) {
        message += (i ? ", " : "") + written[i].path.string();
    }
    return report(options, 0, options.dry_run ? "dry_run" : "emitted", message, written, emitted.losses);
}

}  // namespace

int main(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string_view arg = argv[i];
        auto value = [&](std::string& slot) {
            if (i + 1 >= argc) return false;
            slot = argv[++i];
            return true;
        };
        std::string input;
        bool ok = true;
        if (arg == "--source-type") ok = value(options.source_type);
        else if (arg == "--input") { ok = value(input); options.input = input; }
        else if (arg == "--emit") ok = value(options.emit);
        else if (arg == "--output") ok = value(options.output);
        else if (arg == "--dry-run") options.dry_run = true;
        else if (arg == "--strict-fidelity") options.strict_fidelity = true;
        else if (arg == "--no-comments") options.include_comments = false;
        else if (arg == "--no-tokens") options.include_tokens = false;
        else if (arg == "--json") options.json = true;
        else if (arg == "-h" || arg == "--help") { usage(std::cout); return 0; }
        else ok = false;
        if (!ok) {
            usage(std::cerr);
            return 2;
        }
    }
    if (options.source_type.empty() || options.input.empty() || options.emit.empty()) {
        usage(std::cerr);
        return 2;
    }
    return run(options);
}
