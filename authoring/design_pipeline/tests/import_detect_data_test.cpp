// Data-driven checks for the design-import source detector: the shipped
// detection manifest parses, synthetic snapshots resolve to the right source,
// and every fixture under fixtures/source-detection matches its expected.json.

#include "import_detect_test_support.hpp"

#include <vellum/authoring/import_detect_report.hpp>

#include "strict_json.hpp"

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

// The detection manifest and fixtures the build points the test at.
fs::path locate_compat_json() {
    return fs::path(VELLUM_SOURCE_DETECTION_JSON);
}

fs::path fixtures_root() {
    return fs::path(VELLUM_SOURCE_DETECTION_FIXTURES);
}

choc::value::Value parse_expected(const fs::path& p) {
    auto document = vellum::authoring::strict_json::parse_document(slurp(p));
    REQUIRE(document.has_value());
    return *document;
}

std::string text_member(const choc::value::ValueView& v, const char* name) {
    return v.isObject() && v.hasObjectMember(name) && v[name].isString()
        ? std::string(v[name].getString()) : std::string();
}

int int_member(const choc::value::ValueView& v, const char* name) {
    return v.isObject() && v.hasObjectMember(name) && v[name].isInt()
        ? static_cast<int>(v[name].getInt64()) : 0;
}

}  // namespace

TEST_CASE("the detection manifest parses cleanly with the imports section", "[import-detect]") {
    auto compat = locate_compat_json();
    REQUIRE_FALSE(compat.empty());
    auto manifest = det::parse_compat_json(slurp(compat));
    REQUIRE(manifest.has_value());
    CHECK(manifest->compat_schema_version == "0.3");
    CHECK_FALSE(manifest->sources.empty());

    bool saw_stitch = false;
    bool saw_claude = false;
    for (const auto& src : manifest->sources) {
        if (src.source == "stitch") {
            saw_stitch = true;
            CHECK(src.parser_version == "1.0");
            REQUIRE_FALSE(src.formats.empty());
            CHECK(src.formats.front().format_version == "2025.04");
            // Stitch 2025.04 has 3 fingerprint clauses.
            CHECK(src.formats.front().fingerprint.size() == 3);
        } else if (src.source == "claude") {
            saw_claude = true;
            CHECK(src.parser_version == "2.1");
            REQUIRE_FALSE(src.formats.empty());
            CHECK(src.formats.front().format_version == "2024.10");
        }
    }
    CHECK(saw_stitch);
    CHECK(saw_claude);
}

TEST_CASE("detect picks the highest-confidence format", "[import-detect]") {
    auto compat = locate_compat_json();
    REQUIRE_FALSE(compat.empty());
    auto manifest = det::parse_compat_json(slurp(compat));
    REQUIRE(manifest.has_value());

    SECTION("empty snapshot produces no match") {
        det::InputSnapshot snap;
        auto r = det::detect(*manifest, snap);
        CHECK(r.ok);
        CHECK(r.source.empty());
        CHECK(r.matched_clauses == 0);
    }

    SECTION("synthetic stitch snapshot matches all 3 clauses") {
        det::InputSnapshot snap;
        snap.is_directory = true;
        snap.directory_basenames = {"code.html", "DESIGN.md", "screen.png"};
        snap.script_srcs = {"https://cdn.tailwindcss.com?plugins=forms,container-queries"};
        snap.tailwind_tokens = {"surface-container", "on-primary"};
        auto r = det::detect(*manifest, snap);
        CHECK(r.source == "stitch");
        CHECK(r.format_version == "2025.04");
        CHECK(r.parser_version == "1.0");
        CHECK(r.matched_clauses == 3);
        CHECK(r.total_clauses == 3);
        CHECK(r.confidence_pct == 100);
    }

    SECTION("synthetic claude snapshot matches the bundler-template clause") {
        det::InputSnapshot snap;
        snap.script_types = {"__bundler/template"};
        auto r = det::detect(*manifest, snap);
        CHECK(r.source == "claude");
        CHECK(r.format_version == "2024.10");
        CHECK(r.parser_version == "2.1");
        CHECK(r.matched_clauses == 1);
        CHECK(r.confidence_pct == 100);
    }
}

TEST_CASE("detect reads the Claude bundler tag as HTML does", "[import-detect]") {
    auto manifest = det::parse_compat_json(slurp(locate_compat_json()));
    REQUIRE(manifest.has_value());
    auto dir = fs::temp_directory_path() / "vellum-import-detect-claude-markup";
    fs::remove_all(dir);
    fs::create_directories(dir);
    auto detect_page = [&](const char* name, const std::string& html) {
        std::ofstream(dir / name) << html;
        return det::detect(*manifest, det::snapshot_input(dir / name));
    };

    // Positive control: the active tag in its plain form is detected.
    CHECK(detect_page("plain.html",
                      "<script type=\"__bundler/template\"></script>\n").source == "claude");
    // HTML allows whitespace around the equals sign.
    CHECK(detect_page("spaced.html",
                      "<script type = \"__bundler/template\"></script>\n").source == "claude");
    CHECK(detect_page("spaced-after.html",
                      "<script type= \"__bundler/template\"></script>\n").source == "claude");
    // A commented-out tag is not a fingerprint.
    CHECK(detect_page("commented.html",
                      "<!-- <script type=\"__bundler/template\"></script> -->\n"
                      "<p>unrelated</p>\n").source.empty());

    fs::remove_all(dir);
}

TEST_CASE("clause matcher covers frontmatter any-of keys",
          "[import-detect]") {
    det::InputSnapshot snap;
    snap.has_frontmatter_fence = true;
    snap.frontmatter_keys = {"tokens", "spacing"};

    det::FingerprintClause c;
    c.kind = det::FingerprintClause::Kind::frontmatter_key;
    c.any_of = {"colors", "spacing"};
    CHECK(det::match_clause(c, snap));

    c.any_of = {"colors", "typography"};
    CHECK_FALSE(det::match_clause(c, snap));
}

// Fixture-driven gate.

namespace {

struct Expected {
    std::string source;
    std::string format_version;
    std::string parser_version;
    int matched_clauses = 0;
    int total_clauses = 0;
    int min_confidence_pct = 0;
};

Expected load_expected(const fs::path& p) {
    Expected e;
    auto v = parse_expected(p);
    e.source = text_member(v, "source");
    e.format_version = text_member(v, "format-version");
    e.parser_version = text_member(v, "parser-version");
    e.matched_clauses = int_member(v, "matched-clauses");
    e.total_clauses = int_member(v, "total-clauses");
    e.min_confidence_pct = int_member(v, "min-confidence-pct");
    return e;
}

void run_fixture(const fs::path& fixture_dir,
                 const det::ImportsManifest& manifest) {
    INFO("fixture: " << fixture_dir);

    auto expected_path = fixture_dir / "expected.json";
    REQUIRE(fs::exists(expected_path));
    auto exp = load_expected(expected_path);

    // Choose the input target deterministically. Prefer directory scans
    // for fixtures that model directory exports; otherwise scan the
    // first HTML payload by sorted path so Linux/macOS directory order
    // cannot change the expected detector result. Markdown fixtures
    // (e.g., DESIGN.md) have no HTML payload, fall through to a
    // canonical filename match (DESIGN.md or design.md) so the
    // detector's frontmatter probe runs against the expected file.
    fs::path scan_target = fixture_dir;
    if (!fs::exists(fixture_dir / "code.html")) {
        std::vector<fs::path> html_payloads;
        for (auto& entry : fs::directory_iterator(fixture_dir)) {
            auto fname = entry.path().filename().string();
            if (fname == "expected.json") continue;
            auto ext = entry.path().extension().string();
            std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char c) {
                return static_cast<char>(std::tolower(c));
            });
            // A JSON export (a Figma plug-in scene) is scanned like an HTML
            // payload; json-key-equals clauses read the file's text.
            if (entry.is_regular_file() && (ext == ".html" || ext == ".htm" || ext == ".json"))
                html_payloads.push_back(entry.path());
        }
        std::sort(html_payloads.begin(), html_payloads.end());
        if (!html_payloads.empty()) {
            scan_target = html_payloads.front();
        } else {
            for (auto candidate : {"DESIGN.md", "design.md"}) {
                auto p = fixture_dir / candidate;
                if (fs::exists(p)) { scan_target = p; break; }
            }
        }
    }

    auto snap = det::snapshot_input(scan_target);
    auto result = det::detect(manifest, snap);

    CHECK(result.ok);
    CHECK(result.source == exp.source);
    CHECK(result.format_version == exp.format_version);
    CHECK(result.parser_version == exp.parser_version);
    CHECK(result.matched_clauses == exp.matched_clauses);
    CHECK(result.total_clauses == exp.total_clauses);
    CHECK(result.confidence_pct >= exp.min_confidence_pct);
}

}  // namespace

TEST_CASE("import-detect fixtures match expected.json sidecars", "[import-detect][fixtures]") {
    auto compat = locate_compat_json();
    REQUIRE_FALSE(compat.empty());
    auto manifest = det::parse_compat_json(slurp(compat));
    REQUIRE(manifest.has_value());

    auto root = fixtures_root();
    REQUIRE_FALSE(root.empty());
    REQUIRE(fs::exists(root));

    int fixture_count = 0;
    for (auto& source_dir : fs::directory_iterator(root)) {
        if (!source_dir.is_directory()) continue;
        auto source_name = source_dir.path().filename().string();

        // The `_unknown/` fixture is a flat directory (no per-version
        // subdirs), run it directly.
        if (source_name == "_unknown") {
            run_fixture(source_dir.path(), *manifest);
            ++fixture_count;
            continue;
        }

        for (auto& version_dir : fs::directory_iterator(source_dir.path())) {
            if (!version_dir.is_directory()) continue;
            run_fixture(version_dir.path(), *manifest);
            ++fixture_count;
        }
    }
    CHECK(fixture_count >= 3);  // stitch + claude + _unknown
}


TEST_CASE("figma-plugin manifest clauses tell plug-in exports apart", "[import-detect]") {
    auto manifest = det::parse_compat_json(slurp(locate_compat_json()));
    REQUIRE(manifest.has_value());
    auto scene = det::snapshot_input(fixtures_root() / "figma-plugin" / "2026.05" / "scene.pulp.json");
    auto r = det::detect(*manifest, scene);
    CHECK(r.source == "figma-plugin");
    CHECK(r.matched_clauses == 2);
    CHECK(r.matched_kinds == std::vector<std::string>{"json-key-equals"});

    // The same scene from another adapter, or another format version, is
    // rejected by the all-of gate rather than half-matched.
    for (const auto& [from, to] : std::vector<std::pair<std::string, std::string>>{
             {"\"adapter\": \"figma-plugin\"", "\"adapter\": \"pencil\""},
             {"2026.05-figma-plugin-v1", "2025.01-figma-plugin-v0"}}) {
        auto other = scene;
        auto at = other.html_text.find(from);
        REQUIRE(at != std::string::npos);
        other.html_text.replace(at, from.size(), to);
        CHECK(det::detect(*manifest, other).source.empty());
    }
}

int main() { return import_detect_test::run_all(); }
