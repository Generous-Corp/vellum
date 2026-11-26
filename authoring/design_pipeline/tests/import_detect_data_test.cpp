// Data-driven checks for the design-import source detector: the shipped
// detection manifest parses, synthetic snapshots resolve to the right source,
// and every fixture under fixtures/source-detection matches its expected.json.

#include "import_detect_test_support.hpp"

#include <vellum/authoring/detail/json_parser.hpp>
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

// The detection manifest and fixtures the build points the test at.
fs::path locate_compat_json() {
    return fs::path(VELLUM_SOURCE_DETECTION_JSON);
}

fs::path fixtures_root() {
    return fs::path(VELLUM_SOURCE_DETECTION_FIXTURES);
}

vellum::authoring::json::JsonValue parse_expected(const fs::path& p) {
    auto text = slurp(p);
    vellum::authoring::json::JsonParser parser{text, 0};
    return parser.parse();
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
    if (v.type != vellum::authoring::json::JsonValue::Object) return e;
    if (auto* s = v.get("source"); s) e.source = s->as_string();
    if (auto* fv = v.get("format-version"); fv) e.format_version = fv->as_string();
    if (auto* pv = v.get("parser-version"); pv) e.parser_version = pv->as_string();
    if (auto* m = v.get("matched-clauses"); m) e.matched_clauses = m->as_int();
    if (auto* t = v.get("total-clauses"); t) e.total_clauses = t->as_int();
    if (auto* c = v.get("min-confidence-pct"); c) e.min_confidence_pct = c->as_int();
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
            if (entry.is_regular_file() && (ext == ".html" || ext == ".htm"))
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


int main() { return import_detect_test::run_all(); }
