// Behavioral checks for the design-import source detector: all-of and
// minimum-confidence gating, tie-breaking, partial-match reporting, input
// snapshot edge cases, and the new-format report. Most use inline manifests;
// the snapshot and report cases also read the shipped manifest and fixtures.

#include "import_detect_test_support.hpp"

#include <vellum/authoring/import_detect.hpp>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace det = vellum::authoring::import_detect;

namespace {

std::string slurp(const fs::path& p) {
    std::ifstream f(p);
    if (!f.is_open()) return {};
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

struct ScratchDir {
    fs::path path;

    explicit ScratchDir(const char* stem) {
        path = import_detect_test::make_unique_temp_dir("vellum-import-detect-" + std::string(stem));
    }

    ~ScratchDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }

    ScratchDir(const ScratchDir&) = delete;
    ScratchDir& operator=(const ScratchDir&) = delete;
};

void write_text(const fs::path& p, const std::string& text) {
    fs::create_directories(p.parent_path());
    std::ofstream out(p, std::ios::binary);
    REQUIRE(out.good());
    out << text;
}

// The detection manifest and fixtures the build points the test at.
fs::path locate_compat_json() {
    return fs::path(VELLUM_SOURCE_DETECTION_JSON);
}

fs::path fixtures_root() {
    return fs::path(VELLUM_SOURCE_DETECTION_FIXTURES);
}

}  // namespace

TEST_CASE("detect enforces all-of formats before considering a match",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "design-md";
    source.parser_version = "1.0";

    det::FormatEntry strict;
    strict.format_version = "2026.05";
    strict.parser_version = source.parser_version;
    strict.match = "all-of";

    det::FingerprintClause filename;
    filename.kind = det::FingerprintClause::Kind::filename;
    filename.raw_kind = "filename";
    filename.regex = "(?i)^design\\.md$";
    strict.fingerprint.push_back(filename);

    det::FingerprintClause frontmatter;
    frontmatter.kind = det::FingerprintClause::Kind::frontmatter_key;
    frontmatter.raw_kind = "frontmatter-key";
    frontmatter.required = "name";
    strict.fingerprint.push_back(frontmatter);

    source.formats.push_back(strict);
    manifest.sources.push_back(source);

    det::InputSnapshot snap;
    snap.filename = "DESIGN.md";
    auto partial = det::detect(manifest, snap);
    CHECK(partial.ok);
    CHECK(partial.source.empty());

    snap.has_frontmatter_fence = true;
    snap.frontmatter_keys = {"name"};
    auto full = det::detect(manifest, snap);
    CHECK(full.source == "design-md");
    CHECK(full.matched_clauses == 2);
}

TEST_CASE("detect enforces minimum confidence thresholds",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "threshold";
    source.parser_version = "1.0";

    det::FormatEntry fmt;
    fmt.format_version = "2026.05";
    fmt.parser_version = source.parser_version;
    fmt.min_confidence_pct = 75;

    det::FingerprintClause a;
    a.kind = det::FingerprintClause::Kind::directory_files;
    a.raw_kind = "directory-files";
    a.files = {"code.html"};
    fmt.fingerprint.push_back(a);

    det::FingerprintClause b = a;
    b.files = {"DESIGN.md"};
    fmt.fingerprint.push_back(b);

    det::FingerprintClause c = a;
    c.files = {"screen.png"};
    fmt.fingerprint.push_back(c);

    det::FingerprintClause d = a;
    d.files = {"tokens.json"};
    fmt.fingerprint.push_back(d);

    source.formats.push_back(fmt);
    manifest.sources.push_back(source);

    det::InputSnapshot snap;
    snap.is_directory = true;
    snap.directory_basenames = {"code.html", "DESIGN.md"};
    CHECK(det::detect(manifest, snap).source.empty());

    snap.directory_basenames.push_back("screen.png");
    auto accepted = det::detect(manifest, snap);
    CHECK(accepted.source == "threshold");
    CHECK(accepted.confidence_pct == 75);
}

TEST_CASE("detect prefers higher confidence when match counts tie",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "tie";
    source.parser_version = "1.0";

    det::FingerprintClause code;
    code.kind = det::FingerprintClause::Kind::directory_files;
    code.raw_kind = "directory-files";
    code.files = {"code.html"};

    det::FormatEntry broad;
    broad.format_version = "broad";
    broad.parser_version = source.parser_version;
    broad.fingerprint = {code};
    broad.fingerprint.push_back(code);
    broad.fingerprint.back().files = {"missing-one"};

    det::FormatEntry narrow;
    narrow.format_version = "narrow";
    narrow.parser_version = source.parser_version;
    narrow.fingerprint = {code};

    source.formats = {broad, narrow};
    manifest.sources.push_back(source);

    det::InputSnapshot snap;
    snap.is_directory = true;
    snap.directory_basenames = {"code.html"};
    auto result = det::detect(manifest, snap);
    CHECK(result.format_version == "narrow");
    CHECK(result.confidence_pct == 100);
}

TEST_CASE("snapshot_input extracts Markdown frontmatter only from markdown files",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-phase3-frontmatter";
    fs::remove_all(dir);
    fs::create_directories(dir);
    auto cleanup = [&]() { fs::remove_all(dir); };

    auto md = dir / "DESIGN.md";
    {
        std::ofstream f(md);
        f << "---\nname: Demo\ncolors:\n  primary: blue\n---\n# Body\n";
    }
    auto snap = det::snapshot_input(md);
    CHECK(snap.has_frontmatter_fence);
    CHECK(snap.frontmatter_keys == std::vector<std::string>{"name", "colors"});

    auto html = dir / "DESIGN.html";
    {
        std::ofstream f(html);
        f << "---\nname: Demo\n---\n";
    }
    auto html_snap = det::snapshot_input(html);
    CHECK_FALSE(html_snap.has_frontmatter_fence);
    cleanup();
}

TEST_CASE("snapshot_input accepts CRLF frontmatter and filters invalid top-level keys",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-frontmatter-crlf";
    fs::remove_all(dir);
    fs::create_directories(dir);

    auto md = dir / "design.md";
    {
        std::ofstream f(md, std::ios::binary);
        f << "---\r\n"
          << "name: Demo\r\n"
          << "_private: ok\r\n"
          << "color-set: ok\r\n"
          << "2bad: ignored\r\n"
          << "nested:\r\n"
          << "  child: ignored\r\n"
          << "# comment: ignored\r\n"
          << "---   \r\n"
          << "body: not-frontmatter\r\n";
    }

    auto snap = det::snapshot_input(md);
    CHECK(snap.has_frontmatter_fence);
    CHECK(snap.frontmatter_keys == std::vector<std::string>{"name", "_private", "color-set", "nested"});

    det::FingerprintClause required;
    required.kind = det::FingerprintClause::Kind::frontmatter_key;
    required.required = "color-set";
    CHECK(det::match_clause(required, snap));

    required.required = "child";
    CHECK_FALSE(det::match_clause(required, snap));

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input rejects incomplete frontmatter fences",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-frontmatter-reject";
    fs::remove_all(dir);
    fs::create_directories(dir);

    auto no_newline = dir / "no-newline.md";
    {
        std::ofstream f(no_newline);
        f << "---name: Demo\n---\n";
    }
    auto no_newline_snap = det::snapshot_input(no_newline);
    CHECK_FALSE(no_newline_snap.has_frontmatter_fence);
    CHECK(no_newline_snap.frontmatter_keys.empty());

    auto no_close = dir / "no-close.md";
    {
        std::ofstream f(no_close);
        f << "---\nname: Demo\n";
    }
    auto no_close_snap = det::snapshot_input(no_close);
    CHECK_FALSE(no_close_snap.has_frontmatter_fence);
    CHECK(no_close_snap.frontmatter_keys.empty());

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input prefers index html when code html is absent",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-index-html";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "z-last.html");
        f << "<script src=\"https://example.invalid/last.js\"></script>";
    }
    {
        std::ofstream f(dir / "index.html");
        f << "<script src=https://example.invalid/index.js></script>"
          << "<script type=module></script>";
    }

    auto snap = det::snapshot_input(dir);
    CHECK(snap.is_directory);
    REQUIRE(snap.script_srcs.size() == 1);
    CHECK(snap.script_srcs[0].find("index.js") != std::string::npos);
    REQUIRE(snap.script_types.size() == 1);
    CHECK(snap.script_types[0] == "module");

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input scrapes script attributes without case or prefix traps",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-script-attrs";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "code.html");
        f << "<SCRIPT DATA-SRC=\"ignored.js\" SRC='https://cdn.example/app.js' "
          << "TYPE=\" Module \"></SCRIPT>\n"
          << "<script data-type=\"ignored/module\" type=__bundler/template></script>\n"
          << "<scripture src=\"not-a-script.js\"></scripture>\n"
          << "<script src=https://cdn.example/extra.js></script>\n";
    }

    auto snap = det::snapshot_input(dir);
    CHECK(snap.is_directory);
    CHECK_FALSE(snap.html_text.empty());
    CHECK(snap.directory_basenames == std::vector<std::string>{"code.html"});
    REQUIRE(snap.script_srcs.size() == 2);
    CHECK(snap.script_srcs[0] == "https://cdn.example/app.js");
    CHECK(snap.script_srcs[1] == "https://cdn.example/extra.js");
    REQUIRE(snap.script_types.size() == 2);
    CHECK(snap.script_types[0] == " Module ");
    CHECK(snap.script_types[1] == "__bundler/template");

    det::FingerprintClause src;
    src.kind = det::FingerprintClause::Kind::html_script_src;
    src.regex = R"(cdn\.example/app\.js)";
    CHECK(det::match_clause(src, snap));
    src.regex = "ignored\\.js";
    CHECK_FALSE(det::match_clause(src, snap));
    src.regex = "not-a-script";
    CHECK_FALSE(det::match_clause(src, snap));

    det::FingerprintClause type;
    type.kind = det::FingerprintClause::Kind::html_script_type;
    type.value = "module";
    CHECK(det::match_clause(type, snap));
    type.value = "__BUNDLER/TEMPLATE";
    CHECK(det::match_clause(type, snap));
    type.value = "ignored/module";
    CHECK_FALSE(det::match_clause(type, snap));

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input ignores script-like tags and prefixed attributes",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-script-boundaries";
    fs::remove_all(dir);
    fs::create_directories(dir);
    {
        std::ofstream f(dir / "code.html");
        f << R"HTML(
<html>
  <description src="not-a-script.js"></description>
  <scriptish src="also-not-a-script.js"></scriptish>
  <script data-src="not-src.js" type="ignored-prefixed"></script>
  <script src=unquoted.js type=module></script>
  <SCRIPT SRC='quoted.js' TYPE='application/json'></SCRIPT>
  <script>
    window.tailwind = { theme: { extend: { colors: {
      "surface-container": "#111",
      "brand-primary": "#222"
    }}}};
  </script>
</html>
)HTML";
    }

    auto snap = det::snapshot_input(dir);

    REQUIRE(snap.is_directory);
    REQUIRE(snap.html_text.find("<scriptish") != std::string::npos);
    REQUIRE(snap.script_srcs.size() == 2);
    REQUIRE(snap.script_srcs[0] == "unquoted.js");
    REQUIRE(snap.script_srcs[1] == "quoted.js");
    REQUIRE(std::find(snap.script_srcs.begin(), snap.script_srcs.end(),
                      "not-a-script.js") == snap.script_srcs.end());
    REQUIRE(std::find(snap.script_srcs.begin(), snap.script_srcs.end(),
                      "also-not-a-script.js") == snap.script_srcs.end());
    REQUIRE(std::find(snap.script_srcs.begin(), snap.script_srcs.end(),
                      "not-src.js") == snap.script_srcs.end());
    REQUIRE(snap.script_types.size() == 3);
    REQUIRE(snap.script_types[0] == "ignored-prefixed");
    REQUIRE(snap.script_types[1] == "module");
    REQUIRE(snap.script_types[2] == "application/json");
    REQUIRE(std::find(snap.tailwind_tokens.begin(), snap.tailwind_tokens.end(),
                      "surface-container") != snap.tailwind_tokens.end());
    REQUIRE(std::find(snap.tailwind_tokens.begin(), snap.tailwind_tokens.end(),
                      "brand-primary") != snap.tailwind_tokens.end());

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input accepts script tag and attribute whitespace boundaries",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-script-whitespace-boundaries";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "code.html");
        f << "<script></script>\n"
          << "<script/>\n"
          << "<script\tsrc=\"tab.js\"></script>\n"
          << "<script\rsrc=\"cr.js\"></script>\n"
          << "<script\nsrc=\"nl.js\"></script>\n"
          << "<script data-src=\"ignored.js\" src=\"space.js\"></script>\n"
          << "<script data-type=\"ignored/module\" type=\"space/module\"></script>\n"
          << "<script\ttype=\"tab/module\"></script>\n"
          << "<script\rtype=\"cr/module\"></script>\n"
          << "<script\ntype=\"nl/module\"></script>\n"
          << "<scripture src=\"not-a-script.js\"></scripture>\n";
    }

    auto snap = det::snapshot_input(dir);
    REQUIRE(snap.script_srcs.size() == 4);
    CHECK(snap.script_srcs[0] == "tab.js");
    CHECK(snap.script_srcs[1] == "cr.js");
    CHECK(snap.script_srcs[2] == "nl.js");
    CHECK(snap.script_srcs[3] == "space.js");
    CHECK(std::find(snap.script_srcs.begin(), snap.script_srcs.end(), "ignored.js") ==
          snap.script_srcs.end());
    CHECK(std::find(snap.script_srcs.begin(), snap.script_srcs.end(), "not-a-script.js") ==
          snap.script_srcs.end());

    REQUIRE(snap.script_types.size() == 4);
    CHECK(snap.script_types[0] == "space/module");
    CHECK(snap.script_types[1] == "tab/module");
    CHECK(snap.script_types[2] == "cr/module");
    CHECK(snap.script_types[3] == "nl/module");
    CHECK(std::find(snap.script_types.begin(), snap.script_types.end(), "ignored/module") ==
          snap.script_types.end());

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input falls back to sorted html candidates with attribute forms",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-script-fallback";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "b-last.HTML");
        f << "<script src=\"https://cdn.example/last.js\"></script>";
    }
    {
        std::ofstream f(dir / "a-first.htm");
        f << "<ScRiPt type='application/json' src=/first.js></ScRiPt>";
    }

    auto snap = det::snapshot_input(dir);
    CHECK(snap.is_directory);
    CHECK(snap.directory_basenames == std::vector<std::string>{"a-first.htm", "b-last.HTML"});
    REQUIRE(snap.script_srcs.size() == 1);
    CHECK(snap.script_srcs[0] == "/first.js");
    REQUIRE(snap.script_types.size() == 1);
    CHECK(snap.script_types[0] == "application/json");

    auto file_snap = det::snapshot_input(dir / "b-last.HTML");
    CHECK_FALSE(file_snap.is_directory);
    CHECK(file_snap.filename == "b-last.HTML");
    CHECK(file_snap.directory_basenames == std::vector<std::string>{"b-last.HTML"});
    REQUIRE(file_snap.script_srcs.size() == 1);
    CHECK(file_snap.script_srcs[0] == "https://cdn.example/last.js");
    CHECK(file_snap.script_types.empty());

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input captures unquoted script attributes and sorted directory names",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-unquoted-script-attrs";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "index.html");
        f << "<script src=plain.js type=module></script>\n"
          << "<script src='single.js' type='application/json'></script>\n"
          << "<script data-src=ignored.js></script>\n";
    }
    {
        std::ofstream f(dir / "DESIGN.md");
        f << "---\nname: Demo\n---\n";
    }
    {
        std::ofstream f(dir / "z-last.txt");
        f << "fixture";
    }

    auto snap = det::snapshot_input(dir);
    REQUIRE(snap.is_directory);
    REQUIRE(snap.directory_basenames == std::vector<std::string>{"DESIGN.md", "index.html", "z-last.txt"});
    REQUIRE(snap.script_srcs == std::vector<std::string>{"plain.js", "single.js"});
    REQUIRE(snap.script_types == std::vector<std::string>{"module", "application/json"});
    REQUIRE(std::find(snap.script_srcs.begin(), snap.script_srcs.end(), "ignored.js")
            == snap.script_srcs.end());

    fs::remove_all(dir);
}

TEST_CASE("new-format reports cap unknown tailwind tokens and keep fallbacks stable",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "stitch";
    source.parser_version = "1.0";

    det::FormatEntry fmt;
    fmt.format_version = "2026.05";
    fmt.parser_version = source.parser_version;
    det::FingerprintClause token;
    token.kind = det::FingerprintClause::Kind::tailwind_config_token;
    token.any_of = {"known-token"};
    fmt.fingerprint.push_back(token);
    source.formats.push_back(fmt);
    manifest.sources.push_back(source);

    det::DetectionResult closest;
    closest.source = "stitch";
    closest.format_version = "2026.05";

    det::InputSnapshot snap;
    snap.tailwind_tokens.push_back("known-token");
    for (int i = 0; i < 25; ++i)
        snap.tailwind_tokens.push_back("unknown-" + std::to_string(i));

    auto report = det::build_new_format_report(manifest, snap, closest);
    CHECK(report.candidate_source == "stitch");
    CHECK(report.candidate_format_version == "2026.05+next");
    CHECK(report.based_on_source == "stitch");
    CHECK(report.based_on_format_version == "2026.05");
    REQUIRE(report.additions.size() == 20);
    CHECK(report.additions.front() == "unknown-0");
    CHECK(report.additions.back() == "unknown-19");
    CHECK(std::find(report.additions.begin(), report.additions.end(), "known-token") ==
          report.additions.end());

    auto json = det::render_new_format_json(report);
    CHECK(json.find("\"candidate-source\": \"stitch\"") != std::string::npos);
    CHECK(json.find("\"candidate-format-version\": \"2026.05+next\"") != std::string::npos);
    CHECK(json.find("unknown-19") != std::string::npos);
    CHECK(json.find("unknown-20") == std::string::npos);

    det::DetectionResult none;
    auto fallback = det::build_new_format_report(manifest, {}, none);
    CHECK(fallback.candidate_source.empty());
    CHECK(fallback.candidate_format_version.empty());
    CHECK(fallback.additions.empty());

    auto fallback_json = det::render_new_format_json(fallback);
    CHECK(fallback_json.find("\"candidate-source\": \"\"") != std::string::npos);
    CHECK(fallback_json.find("\"candidate-format-version\": \"\"") != std::string::npos);
    CHECK(fallback_json.find("TODO") == std::string::npos);
}

TEST_CASE("render_new_format_json includes removals and empty additions",
          "[import-detect]") {
    det::NewFormatReport report;
    report.candidate_source = "stitch";
    report.candidate_format_version = "2026.06";
    report.based_on_source = "stitch";
    report.based_on_format_version = "2026.05";
    report.removals = {"old-token", "legacy-script"};

    auto json = det::render_new_format_json(report);
    CHECK(json.find("\"candidate-source\": \"stitch\"") != std::string::npos);
    CHECK(json.find("\"candidate-format-version\": \"2026.06\"") != std::string::npos);
    CHECK(json.find("\"fingerprint-additions\": []") != std::string::npos);
    CHECK(json.find("\"fingerprint-removals\": [") != std::string::npos);
    CHECK(json.find("\"old-token\"") != std::string::npos);
    CHECK(json.find("\"legacy-script\"") != std::string::npos);
    CHECK(json.find("\"based-on\": {\"source\": \"stitch\", \"format-version\": \"2026.05\"}") !=
          std::string::npos);
}

TEST_CASE("render_new_format_json escapes generated string fields",
          "[import-detect]") {
    det::NewFormatReport report;
    report.candidate_source = "stitch\"beta";
    report.candidate_format_version = "2026.06\\next";
    report.based_on_source = "base\nsource";
    report.based_on_format_version = "2026.05\r\tlegacy";
    report.additions = {"brand\"primary", "path\\token"};
    report.removals = {std::string("drop") + static_cast<char>(0x1f)};

    auto json = det::render_new_format_json(report);
    CHECK(json.find("\"candidate-source\": \"stitch\\\"beta\"") != std::string::npos);
    CHECK(json.find("\"candidate-format-version\": \"2026.06\\\\next\"") != std::string::npos);
    CHECK(json.find("\"brand\\\"primary\"") != std::string::npos);
    CHECK(json.find("\"path\\\\token\"") != std::string::npos);
    CHECK(json.find("\"drop\\u001f\"") != std::string::npos);
    CHECK(json.find("\"source\": \"base\\nsource\"") != std::string::npos);
    CHECK(json.find("\"format-version\": \"2026.05\\r\\tlegacy\"") != std::string::npos);
}

TEST_CASE("detect preserves manifest order when matches and confidence tie",
          "[import-detect]") {
    det::ImportsManifest manifest;

    det::FingerprintClause script;
    script.kind = det::FingerprintClause::Kind::html_script_type;
    script.raw_kind = "html-script-type";
    script.value = "module";

    det::SourceEntry first;
    first.source = "first";
    first.parser_version = "1.0";
    det::FormatEntry first_format;
    first_format.format_version = "a";
    first_format.parser_version = first.parser_version;
    first_format.fingerprint = {script};
    first.formats.push_back(first_format);

    det::SourceEntry second;
    second.source = "second";
    second.parser_version = "2.0";
    det::FormatEntry second_format;
    second_format.format_version = "b";
    second_format.parser_version = second.parser_version;
    second_format.fingerprint = {script};
    second.formats.push_back(second_format);

    manifest.sources = {first, second};

    det::InputSnapshot snap;
    snap.script_types = {" Module "};
    auto result = det::detect(manifest, snap);

    CHECK(result.ok);
    CHECK(result.source == "first");
    CHECK(result.format_version == "a");
    CHECK(result.parser_version == "1.0");
    CHECK(result.matched_clauses == 1);
    CHECK(result.total_clauses == 1);
    REQUIRE(result.matched_kinds.size() == 1);
    CHECK(result.matched_kinds[0] == "html-script-type");
}

TEST_CASE("detect reports matched and unmatched clause kinds for partial matches",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "diagnostic";
    source.parser_version = "1.0";

    det::FormatEntry format;
    format.format_version = "2026.05";
    format.parser_version = source.parser_version;

    det::FingerprintClause files;
    files.kind = det::FingerprintClause::Kind::directory_files;
    files.raw_kind = "directory-files";
    files.files = {"code.html"};
    format.fingerprint.push_back(files);

    det::FingerprintClause token;
    token.kind = det::FingerprintClause::Kind::tailwind_config_token;
    token.raw_kind = "tailwind-config-token";
    token.any_of = {"surface"};
    format.fingerprint.push_back(token);

    det::FingerprintClause filename;
    filename.kind = det::FingerprintClause::Kind::filename;
    filename.raw_kind = "filename";
    filename.regex = "(?i)^design\\.md$";
    format.fingerprint.push_back(filename);

    source.formats.push_back(format);
    manifest.sources.push_back(source);

    det::InputSnapshot snap;
    snap.is_directory = true;
    snap.directory_basenames = {"code.html"};
    snap.tailwind_tokens = {"surface"};

    auto result = det::detect(manifest, snap);
    CHECK(result.source == "diagnostic");
    CHECK(result.matched_clauses == 2);
    CHECK(result.total_clauses == 3);
    CHECK(result.confidence_pct == 66);
    CHECK(result.matched_kinds == std::vector<std::string>{"directory-files",
                                                           "tailwind-config-token"});
    CHECK(result.unmatched_kinds == std::vector<std::string>{"filename"});
}

TEST_CASE("snapshot_input prefers code html over sorted html fallbacks",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-code-html-priority";
    fs::remove_all(dir);
    fs::create_directories(dir);

    {
        std::ofstream f(dir / "a-first.html");
        f << "<script src=\"first.js\"></script>";
    }
    {
        std::ofstream f(dir / "code.html");
        f << "<script src=\"code.js\"></script><script type=\"module\"></script>";
    }
    {
        std::ofstream f(dir / "index.html");
        f << "<script src=\"index.js\"></script>";
    }

    auto snap = det::snapshot_input(dir);
    CHECK(snap.is_directory);
    CHECK(snap.directory_basenames == std::vector<std::string>{"a-first.html",
                                                              "code.html",
                                                              "index.html"});
    REQUIRE(snap.script_srcs.size() == 1);
    CHECK(snap.script_srcs[0] == "code.js");
    REQUIRE(snap.script_types.size() == 1);
    CHECK(snap.script_types[0] == "module");

    fs::remove_all(dir);
}

TEST_CASE("new-format reports keep empty additions when no baseline tokens exist",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "claude";
    det::FormatEntry fmt;
    fmt.format_version = "2024.10";
    det::FingerprintClause script;
    script.kind = det::FingerprintClause::Kind::html_script_type;
    script.value = "__bundler/template";
    fmt.fingerprint.push_back(script);
    source.formats.push_back(fmt);
    manifest.sources.push_back(source);

    det::DetectionResult closest;
    closest.source = "claude";
    closest.format_version = "2024.10";

    det::InputSnapshot snap;
    snap.tailwind_tokens = {"unexpected-a", "unexpected-b"};

    auto report = det::build_new_format_report(manifest, snap, closest);
    CHECK(report.candidate_source == "claude");
    CHECK(report.candidate_format_version == "2024.10+next");
    CHECK(report.based_on_source == "claude");
    CHECK(report.based_on_format_version == "2024.10");
    CHECK(report.additions.empty());

    auto json = det::render_new_format_json(report);
    CHECK(json.find("\"fingerprint-additions\": []") != std::string::npos);
    CHECK(json.find("unexpected-a") == std::string::npos);
}


TEST_CASE("snapshot_input handles file and directory inputs", "[import-detect]") {
    auto root = fixtures_root();
    REQUIRE_FALSE(root.empty());
    REQUIRE(fs::exists(root));

    SECTION("directory snapshot collects basenames + html") {
        auto snap = det::snapshot_input(root / "stitch" / "2025.04");
        CHECK(snap.is_directory);
        CHECK_FALSE(snap.directory_basenames.empty());
        // code.html should be parsed → script_srcs populated.
        CHECK_FALSE(snap.script_srcs.empty());
        // tailwind-config-token tokens scraped from the inline config.
        bool saw_surface = false;
        for (const auto& t : snap.tailwind_tokens)
            if (t == "surface-container") saw_surface = true;
        CHECK(saw_surface);
    }

    SECTION("file snapshot uses the file as html_text") {
        auto snap = det::snapshot_input(root / "claude" / "2024.10" / "example.html");
        CHECK_FALSE(snap.is_directory);
        bool saw_bundler = false;
        for (const auto& t : snap.script_types)
            if (t == "__bundler/template") saw_bundler = true;
        CHECK(saw_bundler);
    }

    SECTION("missing path returns an empty snapshot, not a crash") {
        auto snap = det::snapshot_input(root / "does" / "not" / "exist");
        CHECK_FALSE(snap.is_directory);
        CHECK(snap.html_text.empty());
        CHECK(snap.directory_basenames.empty());
    }
}

TEST_CASE("snapshot_input chooses deterministic directory HTML candidates",
          "[import-detect]") {
    ScratchDir scratch("html-priority");
    write_text(scratch.path / "z-last.html",
               R"(<html><script src="/fallback.js"></script></html>)");
    write_text(scratch.path / "index.html",
               R"(<html><script src="/index.js"></script></html>)");
    write_text(scratch.path / "code.html",
               R"(<html><script src="/code.js"></script><script type="module"></script></html>)");
    write_text(scratch.path / "notes.txt", "not html");

    auto snap = det::snapshot_input(scratch.path);
    REQUIRE(snap.is_directory);
    REQUIRE(snap.directory_basenames.size() == 4);
    CHECK(snap.directory_basenames
          == std::vector<std::string>{"code.html", "index.html", "notes.txt", "z-last.html"});
    REQUIRE(snap.html_text.find("/code.js") != std::string::npos);
    CHECK(snap.html_text.find("/index.js") == std::string::npos);
    CHECK(snap.html_text.find("/fallback.js") == std::string::npos);
    REQUIRE(snap.script_srcs == std::vector<std::string>{"/code.js"});
    REQUIRE(snap.script_types == std::vector<std::string>{"module"});
}

TEST_CASE("snapshot_input falls back to the sorted first HTML candidate",
          "[import-detect]") {
    ScratchDir scratch("html-fallback");
    write_text(scratch.path / "b-page.htm",
               R"(<html><script src="/b.js"></script></html>)");
    write_text(scratch.path / "a-page.html",
               R"(<html><script src="/a.js"></script></html>)");

    auto snap = det::snapshot_input(scratch.path);
    REQUIRE(snap.is_directory);
    REQUIRE(snap.html_text.find("/a.js") != std::string::npos);
    CHECK(snap.html_text.find("/b.js") == std::string::npos);
    REQUIRE(snap.script_srcs == std::vector<std::string>{"/a.js"});
}

TEST_CASE("snapshot_input frontmatter probe records only top-level valid keys",
          "[import-detect]") {
    ScratchDir scratch("frontmatter-keys");
    const auto path = scratch.path / "DESIGN.md";
    write_text(path,
               "---\r\n"
               "name: Demo\r\n"
               "_private: yes\r\n"
               "token-group: yes\r\n"
               " nested: ignored\r\n"
               "# comment: ignored\r\n"
               "9bad: ignored\r\n"
               "---   \r\n"
               "# Body\n");

    auto snap = det::snapshot_input(path);
    REQUIRE_FALSE(snap.is_directory);
    CHECK(snap.filename == "DESIGN.md");
    REQUIRE(snap.has_frontmatter_fence);
    CHECK(snap.frontmatter_keys
          == std::vector<std::string>{"name", "_private", "token-group"});

    det::FingerprintClause required_name;
    required_name.kind = det::FingerprintClause::Kind::frontmatter_key;
    required_name.required = "name";
    CHECK(det::match_clause(required_name, snap));

    det::FingerprintClause nested_key = required_name;
    nested_key.required = "nested";
    CHECK_FALSE(det::match_clause(nested_key, snap));

    det::FingerprintClause any_token;
    any_token.kind = det::FingerprintClause::Kind::frontmatter_key;
    any_token.any_of = {"spacing", "token-group"};
    CHECK(det::match_clause(any_token, snap));
}

TEST_CASE("report-new-format diffs unknown tokens against the closest match",
          "[import-detect]") {
    auto compat = locate_compat_json();
    REQUIRE_FALSE(compat.empty());
    auto manifest = det::parse_compat_json(slurp(compat));
    REQUIRE(manifest.has_value());

    // Synthesize a "newer" Stitch export: matches the html-script-src
    // clause, ships unknown tokens.
    det::InputSnapshot snap;
    snap.is_directory = true;
    snap.directory_basenames = {"code.html", "DESIGN.md", "screen.png"};
    snap.script_srcs = {"https://cdn.tailwindcss.com?plugins=forms,container-queries"};
    snap.tailwind_tokens = {"surface-container", "on-primary",
                            "rainbow-gradient", "pulse-spring"};

    auto closest = det::detect(*manifest, snap);
    REQUIRE(closest.source == "stitch");
    auto report = det::build_new_format_report(*manifest, snap, closest);

    CHECK(report.candidate_source == "stitch");
    CHECK(report.based_on_format_version == "2025.04");
    // The two new tokens should appear in additions; the known ones
    // should not.
    bool saw_rainbow = false;
    bool saw_pulse = false;
    bool saw_known = false;
    for (const auto& a : report.additions) {
        if (a == "rainbow-gradient") saw_rainbow = true;
        if (a == "pulse-spring") saw_pulse = true;
        if (a == "surface-container" || a == "on-primary") saw_known = true;
    }
    CHECK(saw_rainbow);
    CHECK(saw_pulse);
    CHECK_FALSE(saw_known);

    auto json = det::render_new_format_json(report);
    CHECK(json.find("\"candidate-source\": \"stitch\"") != std::string::npos);
    CHECK(json.find("rainbow-gradient") != std::string::npos);
    CHECK(json.find("\"based-on\":") != std::string::npos);
}

TEST_CASE("report-new-format caps token suggestions at twenty entries",
          "[import-detect]") {
    det::ImportsManifest manifest;
    det::SourceEntry source;
    source.source = "stitch";
    det::FormatEntry fmt;
    fmt.format_version = "2025.04";
    det::FingerprintClause known_tokens;
    known_tokens.kind = det::FingerprintClause::Kind::tailwind_config_token;
    known_tokens.any_of = {"known-token"};
    fmt.fingerprint.push_back(known_tokens);
    source.formats.push_back(fmt);
    manifest.sources.push_back(source);

    det::DetectionResult closest;
    closest.source = "stitch";
    closest.format_version = "2025.04";

    det::InputSnapshot snap;
    for (int i = 0; i < 25; ++i)
        snap.tailwind_tokens.push_back("token-" + std::to_string(i));

    auto report = det::build_new_format_report(manifest, snap, closest);
    CHECK(report.additions.size() == 20);
}

int main() { return import_detect_test::run_all(); }
