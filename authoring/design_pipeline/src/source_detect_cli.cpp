#include <vellum/authoring/source_detect_cli.hpp>

#include <vellum/authoring/import_detect.hpp>

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <ostream>
#include <sstream>
#include <string>
#include <string_view>

namespace vellum::authoring {

namespace {

namespace fs = std::filesystem;
namespace det = import_detect;

std::string read_file(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), {}};
}

std::string json_string(std::string_view text) {
    std::string out = "\"";
    for (char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buffer[8];
                    std::snprintf(buffer, sizeof buffer, "\\u%04x", static_cast<unsigned>(static_cast<unsigned char>(c)));
                    out += buffer;
                } else {
                    out += c;
                }
        }
    }
    return out + "\"";
}

void write_json(std::ostream& out, const det::DetectionResult& result,
                const fs::path& compat_path, const std::string& schema) {
    out << "{\"source\":" << json_string(result.source)
        << ",\"format_version\":" << json_string(result.format_version)
        << ",\"parser_version\":" << json_string(result.parser_version)
        << ",\"matched_clauses\":" << result.matched_clauses
        << ",\"total_clauses\":" << result.total_clauses
        << ",\"confidence_pct\":" << result.confidence_pct
        << ",\"matched_kinds\":[";
    for (std::size_t i = 0; i < result.matched_kinds.size(); ++i)
        out << (i ? "," : "") << json_string(result.matched_kinds[i]);
    out << "],\"unmatched_kinds\":[";
    for (std::size_t i = 0; i < result.unmatched_kinds.size(); ++i)
        out << (i ? "," : "") << json_string(result.unmatched_kinds[i]);
    out << "],\"compat\":" << json_string(compat_path.string())
        << ",\"compat_schema_version\":" << json_string(schema) << "}\n";
}

} // namespace

int run_source_detect(int argc, const char* const* argv, std::ostream& out, std::ostream& err) {
    std::string compat_override;
    std::string compat_default;
    std::string scan_path;
    bool report_new_format = false;
    bool json = false;
    for (int i = 0; i < argc; ++i) {
        const std::string_view arg = argv[i];
        if (arg == "--report-new-format") {
            report_new_format = true;
        } else if (arg == "--json") {
            json = true;
        } else if ((arg == "--compat" || arg == "--default-compat") && i + 1 < argc) {
            (arg == "--compat" ? compat_override : compat_default) = argv[++i];
        } else if (!arg.empty() && arg.front() != '-' && scan_path.empty()) {
            scan_path = std::string(arg);
        } else {
            err << "Error: unexpected detect argument: " << arg << "\n";
            return 1;
        }
    }
    if (scan_path.empty()) {
        err << "Error: detect requires an input file or directory\n";
        return 1;
    }
    std::error_code ec;
    if (!fs::exists(scan_path, ec)) {
        err << "Error: path does not exist: " << scan_path << "\n";
        return 1;
    }

    // Resolve the manifest: explicit override > walk parents > cwd > default.
    fs::path compat_path;
    if (!compat_override.empty()) {
        compat_path = compat_override;
    } else {
        fs::path start = fs::is_directory(scan_path) ? fs::path(scan_path)
                                                     : fs::path(scan_path).parent_path();
        if (start.empty()) start = fs::current_path();
        compat_path = det::find_compat_json(start);
        if (compat_path.empty()) compat_path = det::find_compat_json(fs::current_path());
        if (compat_path.empty() && !compat_default.empty()) compat_path = compat_default;
    }
    if (compat_path.empty() || !fs::exists(compat_path, ec)) {
        err << "Error: compat.json not found (pass --compat <path>)\n";
        return 1;
    }
    auto manifest = det::parse_compat_json(read_file(compat_path));
    if (!manifest) {
        err << "Error: malformed compat.json at " << compat_path << "\n";
        return 1;
    }

    auto snap = det::snapshot_input(scan_path);
    auto result = det::detect(*manifest, snap);

    if (report_new_format) {
        out << det::render_new_format_json(det::build_new_format_report(*manifest, snap, result));
        return 0;
    }
    if (json) {
        write_json(out, result, compat_path, manifest->compat_schema_version);
        return result.source.empty() ? 2 : 0;
    }
    if (result.source.empty()) {
        out << "no detected source for " << scan_path << "\n";
        out << "  compat.json: " << compat_path.string() << " (schema "
            << manifest->compat_schema_version << ")\n";
        return 2;
    }
    out << "detected source: " << result.source << "\n";
    out << "  format-version: " << result.format_version << "\n";
    out << "  parser-version: " << result.parser_version << "\n";
    out << "  fingerprint match: " << result.matched_clauses << "/" << result.total_clauses;
    if (!result.matched_kinds.empty()) {
        out << " (";
        for (std::size_t i = 0; i < result.matched_kinds.size(); ++i)
            out << (i ? ", " : "") << result.matched_kinds[i];
        out << ")";
    }
    out << "\n";
    out << "  confidence: " << result.confidence_pct << "%\n";
    if (result.confidence_pct < 80) {
        out << "warning: confidence below 80%; this export may be a newer\n"
            << "         format-version than Vellum recognizes. Vellum will use\n"
            << "         the most-recent matching parser; gaps surface in\n"
            << "         the import report. To file a new format detector:\n"
            << "  vellum import --detect-only " << scan_path << " --report-new-format\n";
    }
    return 0;
}

} // namespace vellum::authoring
