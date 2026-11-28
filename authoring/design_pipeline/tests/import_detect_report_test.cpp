// Scoring and new-format report checks for the design-import source detector,
// on inline manifests: all-of and minimum-confidence gating, tie-breaking,
// distinct clause kinds, and the report's additions and removals for Tailwind
// tokens, script srcs and script types.

#include "import_detect_test_support.hpp"

#include <vellum/authoring/import_detect_report.hpp>

#include <string>
#include <vector>

namespace det = vellum::authoring::import_detect;

namespace {

det::FingerprintClause clause(det::FingerprintClause::Kind kind, const char* raw) {
    det::FingerprintClause c;
    c.kind = kind;
    c.raw_kind = raw;
    return c;
}

det::FingerprintClause type_clause(const char* value) {
    auto c = clause(det::FingerprintClause::Kind::html_script_type, "html-script-type");
    c.value = value;
    return c;
}

det::FingerprintClause src_clause(const char* regex) {
    auto c = clause(det::FingerprintClause::Kind::html_script_src, "html-script-src");
    c.regex = regex;
    return c;
}

det::FingerprintClause token_clause(std::vector<std::string> tokens) {
    auto c = clause(det::FingerprintClause::Kind::tailwind_config_token, "tailwind-config-token");
    c.any_of = std::move(tokens);
    return c;
}

det::FormatEntry format(const char* version, std::vector<det::FingerprintClause> clauses) {
    det::FormatEntry f;
    f.format_version = version;
    f.parser_version = "1.0";
    f.fingerprint = std::move(clauses);
    return f;
}

det::ImportsManifest manifest(std::vector<det::SourceEntry> sources) {
    det::ImportsManifest m;
    m.compat_schema_version = "0.3";
    m.sources = std::move(sources);
    return m;
}

det::SourceEntry source(const char* name, std::vector<det::FormatEntry> formats) {
    det::SourceEntry s;
    s.source = name;
    s.parser_version = "1.0";
    s.formats = std::move(formats);
    return s;
}

}  // namespace

TEST_CASE("all-of and minimum confidence gate a format", "[import-detect]") {
    auto strict = format("1", {type_clause("module"), type_clause("text/babel")});
    strict.match = "all-of";
    det::InputSnapshot snap;
    snap.script_types = {"module"};
    CHECK(det::detect(manifest({source("strict", {strict})}), snap).source.empty());
    snap.script_types = {"module", "text/babel"};
    CHECK(det::detect(manifest({source("strict", {strict})}), snap).source == "strict");

    auto floor = format("1", {type_clause("module"), type_clause("text/babel"), type_clause("x")});
    floor.min_confidence_pct = 70;
    snap.script_types = {"module", "text/babel"};
    CHECK(det::detect(manifest({source("floor", {floor})}), snap).source.empty());
    snap.script_types = {"module", "text/babel", "x"};
    auto r = det::detect(manifest({source("floor", {floor})}), snap);
    CHECK(r.source == "floor");
    CHECK(r.confidence_pct == 100);
}

TEST_CASE("ties prefer higher confidence, then manifest order", "[import-detect]") {
    det::InputSnapshot snap;
    snap.script_types = {"module"};
    // One match each: the one-clause format is the more confident.
    auto wide = format("wide", {type_clause("module"), type_clause("other")});
    auto narrow = format("narrow", {type_clause("module")});
    auto r = det::detect(manifest({source("a", {wide}), source("b", {narrow})}), snap);
    CHECK(r.source == "b");
    CHECK(r.confidence_pct == 100);
    // Equal matches and confidence: the earlier source wins.
    r = det::detect(manifest({source("first", {narrow}), source("second", {narrow})}), snap);
    CHECK(r.source == "first");
    r = det::detect(manifest({source("second", {narrow}), source("first", {narrow})}), snap);
    CHECK(r.source == "second");
    // No match at all is ok with an empty source.
    det::InputSnapshot none;
    r = det::detect(manifest({source("a", {narrow})}), none);
    CHECK(r.ok);
    CHECK(r.source.empty());
}

TEST_CASE("matched and unmatched kinds are distinct", "[import-detect]") {
    det::InputSnapshot snap;
    snap.script_types = {"module", "text/babel"};
    auto f = format("1", {type_clause("module"), type_clause("text/babel"),
                          type_clause("missing-a"), type_clause("missing-b")});
    auto r = det::detect(manifest({source("s", {f})}), snap);
    CHECK(r.matched_clauses == 2);
    CHECK(r.total_clauses == 4);
    CHECK(r.matched_kinds == std::vector<std::string>{"html-script-type"});
    CHECK(r.unmatched_kinds == std::vector<std::string>{"html-script-type"});
}

TEST_CASE("the report diffs Tailwind tokens both ways", "[import-detect]") {
    auto m = manifest({source("stitch", {format("2025.04", {
        src_clause("cdn\\.tailwindcss\\.com"), token_clause({"surface-container", "on-primary"})})})});
    det::InputSnapshot snap;
    snap.script_srcs = {"https://cdn.tailwindcss.com?plugins=forms"};
    snap.tailwind_tokens = {"surface-container", "brand-glow"};
    auto closest = det::detect(m, snap);
    REQUIRE(closest.source == "stitch");
    auto report = det::build_new_format_report(m, snap, closest);
    CHECK(report.candidate_format_version == "2025.04+next");
    CHECK(report.additions == std::vector<std::string>{"brand-glow"});
    CHECK(report.removals == std::vector<std::string>{"on-primary"});
    CHECK(report.script_additions.empty());
    CHECK(report.script_removals.empty());
}

TEST_CASE("the report diffs script srcs and script types both ways", "[import-detect]") {
    auto m = manifest({source("tool", {format("1", {
        src_clause("cdn\\.example\\.com/app"), type_clause("module"), type_clause("text/babel")})})});
    det::InputSnapshot snap;
    snap.script_srcs = {"https://cdn.other.net/app.js?v=2"};
    snap.script_types = {"MODULE", "importmap"};
    auto closest = det::detect(m, snap);
    REQUIRE(closest.source == "tool");
    auto report = det::build_new_format_report(m, snap, closest);
    CHECK(report.script_removals == std::vector<std::string>{"cdn\\.example\\.com/app"});
    CHECK(report.script_additions == std::vector<std::string>{"https://cdn.other.net/app.js?v=2"});
    CHECK(report.type_removals == std::vector<std::string>{"text/babel"});
    CHECK(report.type_additions == std::vector<std::string>{"importmap"});

    const auto json = det::render_new_format_json(report);
    CHECK(json.find(R"({"kind": "html-script-src", "regex": "https:\\/\\/cdn\\.other\\.net\\/app\\.js\\?v=2"})")
          != std::string::npos);
    CHECK(json.find(R"({"kind": "html-script-src", "regex": "cdn\\.example\\.com/app"})")
          != std::string::npos);
    CHECK(json.find(R"({"kind": "html-script-type", "value": "importmap"})") != std::string::npos);
    CHECK(json.find(R"({"kind": "html-script-type", "value": "text/babel"})") != std::string::npos);
    // The rendered report is one JSON document.
    CHECK(json.front() == '{');
    CHECK(json.find("\"fingerprint-removals\": [\n") != std::string::npos);
}

TEST_CASE("the report compares script types the way the matcher does", "[import-detect]") {
    // match_clause trims and lowercases html-script-type values, so a padded
    // type it matched is not a change, on either the manifest or input side.
    auto m = manifest({source("tool", {format("1", {
        type_clause(" Text/Babel "), type_clause("module")})})});
    det::InputSnapshot snap;
    snap.script_types = {" module ", "text/babel", "  "};
    auto closest = det::detect(m, snap);
    REQUIRE(closest.source == "tool");
    auto report = det::build_new_format_report(m, snap, closest);
    CHECK(report.type_additions.empty());
    CHECK(report.type_removals.empty());
}

TEST_CASE("a category the closest format does not use is still reported", "[import-detect]") {
    // The closest format fingerprints only script types; the input's Tailwind
    // tokens and script srcs are all new and must all be listed.
    auto m = manifest({source("tool", {format("1", {type_clause("module")})})});
    det::InputSnapshot snap;
    snap.script_types = {"module"};
    snap.tailwind_tokens = {"new-a", "new-b"};
    snap.script_srcs = {"https://cdn.example.com/a|b.js"};
    auto closest = det::detect(m, snap);
    REQUIRE(closest.source == "tool");
    auto report = det::build_new_format_report(m, snap, closest);
    CHECK(report.additions == std::vector<std::string>{"new-a", "new-b"});
    CHECK(report.script_additions == std::vector<std::string>{"https://cdn.example.com/a|b.js"});
    CHECK(report.removals.empty());
    // A src containing `|` is rendered as a literal regex, not an alternation.
    const auto json = det::render_new_format_json(report);
    CHECK(json.find(R"("regex": "https:\\/\\/cdn\\.example\\.com\\/a\\|b\\.js")") != std::string::npos);
}

TEST_CASE("an unmatched input drafts nothing from guessed data", "[import-detect]") {
    auto m = manifest({source("tool", {format("1", {type_clause("module")})})});
    det::InputSnapshot snap;
    snap.script_types = {"importmap"};
    auto closest = det::detect(m, snap);
    REQUIRE(closest.source.empty());
    auto report = det::build_new_format_report(m, snap, closest);
    CHECK(report.candidate_source.empty());
    CHECK(report.candidate_format_version.empty());
    CHECK(report.additions.empty());
    CHECK(report.removals.empty());
    CHECK(report.type_additions.empty());
    const auto json = det::render_new_format_json(report);
    CHECK(json.find("\"fingerprint-additions\": [],") != std::string::npos);
    CHECK(json.find("\"fingerprint-removals\": [],") != std::string::npos);
}

int main() { return import_detect_test::run_all(); }
