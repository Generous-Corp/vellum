// Unit checks for the design-import source detector: manifest parsing, the
// manifest search, input snapshots, and each fingerprint clause kind. These
// use inline manifests and scratch files, so they need no detection data.

#include "import_detect_test_support.hpp"

#include <vellum/authoring/import_detect.hpp>

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

[[maybe_unused]] std::string slurp(const fs::path& p) {
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

}  // namespace

TEST_CASE("parse_compat_json rejects malformed input", "[import-detect]") {
    CHECK_FALSE(det::parse_compat_json("").has_value());
    CHECK_FALSE(det::parse_compat_json("not json").has_value());
    // Missing `imports` is a hard error: the manifest is meaningless
    // to the detector without it.
    CHECK_FALSE(det::parse_compat_json("{\"compat-schema-version\":\"0.2\"}").has_value());
}

TEST_CASE("parse_compat_json preserves sources with empty detected formats",
          "[import-detect]") {
    auto manifest = det::parse_compat_json(R"({
  "compat-schema-version": "1.0",
  "imports": {
    "empty": {"parser-version": "1.0", "detected-formats": []},
    "bad": {"parser-version": "1.0", "detected-formats": [42, []]},
    "good": {
      "parser-version": "1.0",
      "detected-formats": [{
        "format-version": "2026.05",
        "fingerprint": [{"kind": "filename", "regex": ".*\\.html"}]
      }]
    }
  }
})");

    REQUIRE(manifest.has_value());
    REQUIRE(manifest->sources.size() == 3);
    REQUIRE(manifest->sources[0].source == "empty");
    REQUIRE(manifest->sources[0].formats.empty());
    REQUIRE(manifest->sources[1].source == "bad");
    REQUIRE(manifest->sources[1].formats.empty());
    REQUIRE(manifest->sources[2].source == "good");
    REQUIRE(manifest->sources[2].formats.size() == 1);
}

TEST_CASE("parse_compat_json keeps optional format metadata and skips bad shapes",
          "[import-detect]") {
    auto manifest = det::parse_compat_json(R"({
  "compat-schema-version": "0.9",
  "imports": {
    "bad-source": [],
    "demo": {
      "parser-version": "3.1",
      "detected-formats": [
        [],
        {
          "format-version": "2026.05",
          "introduced": "2026-05-01",
          "deprecated": "2026-12-31",
          "notes": "fixture notes",
          "match": "all-of",
          "min-confidence-pct": 80,
          "fingerprint": [
            [],
            {"kind": "filename", "regex": "(?i)^design\\.md$"}
          ]
        }
      ]
    }
  }
})");

    REQUIRE(manifest.has_value());
    REQUIRE(manifest->sources.size() == 1);
    const auto& source = manifest->sources.front();
    REQUIRE(source.source == "demo");
    REQUIRE(source.formats.size() == 1);
    const auto& fmt = source.formats.front();
    CHECK(fmt.parser_version == "3.1");
    CHECK(fmt.format_version == "2026.05");
    CHECK(fmt.introduced == "2026-05-01");
    CHECK(fmt.deprecated == "2026-12-31");
    CHECK(fmt.notes == "fixture notes");
    CHECK(fmt.match == "all-of");
    CHECK(fmt.min_confidence_pct == 80);
    REQUIRE(fmt.fingerprint.size() == 2);
    CHECK(fmt.fingerprint[0].kind == det::FingerprintClause::Kind::unknown);
    CHECK(fmt.fingerprint[1].kind == det::FingerprintClause::Kind::filename);
}

TEST_CASE("parse_compat_json ignores malformed optional fields without dropping formats",
          "[import-detect]") {
    auto manifest = det::parse_compat_json(R"({
  "compat-schema-version": 3,
  "imports": {
    "demo": {
      "parser-version": 7,
      "detected-formats": [
        {
          "format-version": 2026,
          "introduced": false,
          "deprecated": null,
          "notes": [],
          "match": {},
          "min-confidence-pct": "90",
          "fingerprint": [
            {"kind": "frontmatter-key", "required": "name"},
            {"kind": "tailwind-config-token", "any-of": ["surface", 4]},
            {"kind": "new-future-kind", "value": "x"}
          ]
        }
      ]
    }
  }
})");

    REQUIRE(manifest.has_value());
    CHECK(manifest->compat_schema_version.empty());
    REQUIRE(manifest->sources.size() == 1);
    const auto& source = manifest->sources.front();
    CHECK(source.source == "demo");
    CHECK(source.parser_version.empty());
    REQUIRE(source.formats.size() == 1);
    const auto& fmt = source.formats.front();
    CHECK(fmt.parser_version.empty());
    CHECK(fmt.format_version.empty());
    CHECK(fmt.introduced.empty());
    CHECK(fmt.deprecated.empty());
    CHECK(fmt.notes.empty());
    CHECK(fmt.match.empty());
    CHECK(fmt.min_confidence_pct == 0);
    REQUIRE(fmt.fingerprint.size() == 3);
    CHECK(fmt.fingerprint[0].kind == det::FingerprintClause::Kind::frontmatter_key);
    CHECK(fmt.fingerprint[0].required == "name");
    CHECK(fmt.fingerprint[1].any_of == std::vector<std::string>{"surface"});
    CHECK(fmt.fingerprint[2].kind == det::FingerprintClause::Kind::unknown);
    CHECK(fmt.fingerprint[2].raw_kind == "new-future-kind");
}

TEST_CASE("find_compat_json walks parents and reports absence",
          "[import-detect]") {
    auto dir = fs::temp_directory_path() / "vellum-import-detect-compat-walk";
    fs::remove_all(dir);
    fs::create_directories(dir / "repo" / "nested" / "child");

    const auto compat = dir / "repo" / "compat.json";
    {
        std::ofstream f(compat);
        f << "{\"imports\":{}}";
    }

    CHECK(det::find_compat_json(dir / "repo" / "nested" / "child") == compat);
    CHECK(det::find_compat_json(dir / "repo") == compat);

    fs::remove(compat);
    CHECK(det::find_compat_json(dir / "repo" / "nested" / "child").empty());

    fs::remove_all(dir);
}

TEST_CASE("snapshot_input scrapes script attributes and frontmatter edge cases",
          "[import-detect]") {
    ScratchDir scratch("snapshot-edges");

    write_text(scratch.path / "case.html", R"HTML(
<!doctype html>
<SCRIPT SRC='/assets/app.js' TYPE='module'></SCRIPT>
<script data-src="/ignored.js" src=/assets/inline.js></script>
<script src = "/spaced-equals.js"></script>
<scriptish src="/not-a-script.js"></scriptish>
<script type=text/babel></script>
<script>
  window.tailwind = { theme: { extend: { colors: {
    "surface-container": "#fff",
    "accent": "#000"
  }}}}
</script>
)HTML");

    auto html = det::snapshot_input(scratch.path / "case.html");
    REQUIRE_FALSE(html.is_directory);
    REQUIRE(html.filename == "case.html");
    REQUIRE(html.script_srcs.size() == 3);
    REQUIRE(html.script_srcs[0] == "/assets/app.js");
    REQUIRE(html.script_srcs[1] == "/assets/inline.js");
    REQUIRE(html.script_srcs[2] == "/spaced-equals.js");
    REQUIRE(html.script_types.size() == 2);
    REQUIRE(html.script_types[0] == "module");
    REQUIRE(html.script_types[1] == "text/babel");
    REQUIRE(std::find(html.tailwind_tokens.begin(),
                      html.tailwind_tokens.end(),
                      "surface-container") != html.tailwind_tokens.end());
    REQUIRE(std::find(html.tailwind_tokens.begin(),
                      html.tailwind_tokens.end(),
                      "accent") != html.tailwind_tokens.end());

    write_text(scratch.path / "DESIGN.md",
               "---\r\n"
               "name: Demo\r\n"
               "colors:\r\n"
               "  primary: '#fff'\r\n"
               "# comment: ignored\r\n"
               "---\r\n"
               "# Body\r\n");

    auto design = det::snapshot_input(scratch.path / "DESIGN.md");
    REQUIRE(design.has_frontmatter_fence);
    REQUIRE(design.frontmatter_keys == std::vector<std::string>{"name", "colors"});

    det::FingerprintClause type_clause;
    type_clause.kind = det::FingerprintClause::Kind::html_script_type;
    type_clause.value = " MODULE ";
    REQUIRE(det::match_clause(type_clause, html));

    det::FingerprintClause key_clause;
    key_clause.kind = det::FingerprintClause::Kind::frontmatter_key;
    key_clause.required = "colors";
    REQUIRE(det::match_clause(key_clause, design));
}

TEST_CASE("clause matcher honors the fingerprint vocabulary", "[import-detect]") {
    det::InputSnapshot snap;
    snap.is_directory = true;
    snap.directory_basenames = {"code.html", "DESIGN.md", "screen.png", "extras"};
    snap.script_srcs = {"https://cdn.tailwindcss.com?plugins=forms,container-queries"};
    snap.script_types = {"module", "__bundler/template"};
    snap.tailwind_tokens = {"primary", "on-primary", "surface-container"};

    SECTION("directory-files clause requires every listed file") {
        det::FingerprintClause c;
        c.kind = det::FingerprintClause::Kind::directory_files;
        c.files = {"code.html", "DESIGN.md", "screen.png"};
        CHECK(det::match_clause(c, snap));

        c.files.push_back("does-not-exist");
        CHECK_FALSE(det::match_clause(c, snap));
    }

    SECTION("html-script-src clause uses ECMAScript regex semantics") {
        det::FingerprintClause c;
        c.kind = det::FingerprintClause::Kind::html_script_src;
        c.regex = R"(cdn\.tailwindcss\.com\?plugins=forms,container-queries)";
        CHECK(det::match_clause(c, snap));

        c.regex = "no-such-cdn";
        CHECK_FALSE(det::match_clause(c, snap));
    }

    SECTION("html-script-type clause is case-insensitive trim") {
        det::FingerprintClause c;
        c.kind = det::FingerprintClause::Kind::html_script_type;
        c.value = "__bundler/template";
        CHECK(det::match_clause(c, snap));

        c.value = "missing/type";
        CHECK_FALSE(det::match_clause(c, snap));
    }

    SECTION("tailwind-config-token clause matches if any-of token is present") {
        det::FingerprintClause c;
        c.kind = det::FingerprintClause::Kind::tailwind_config_token;
        c.any_of = {"surface-container", "rainbow-gradient"};
        CHECK(det::match_clause(c, snap));

        c.any_of = {"never-emitted-token"};
        CHECK_FALSE(det::match_clause(c, snap));
    }

    SECTION("unknown clause kinds never match") {
        det::FingerprintClause c;
        c.kind = det::FingerprintClause::Kind::unknown;
        CHECK_FALSE(det::match_clause(c, snap));
    }
}

TEST_CASE("clause matcher handles filename regex flags and invalid patterns",
          "[import-detect]") {
    det::InputSnapshot snap;
    snap.filename = "DESIGN.md";
    snap.directory_basenames = {"DESIGN.md"};

    det::FingerprintClause c;
    c.kind = det::FingerprintClause::Kind::filename;
    c.regex = "(?i)^design\\.md$";
    CHECK(det::match_clause(c, snap));

    c.regex = "^design\\.md$";
    CHECK_FALSE(det::match_clause(c, snap));

    c.regex = "[unterminated";
    CHECK_FALSE(det::match_clause(c, snap));
}

TEST_CASE("clause matcher rejects empty filename and script clauses",
          "[import-detect]") {
    det::InputSnapshot snap;
    det::FingerprintClause c;

    c.kind = det::FingerprintClause::Kind::filename;
    c.regex = ".*";
    CHECK_FALSE(det::match_clause(c, snap));

    c.kind = det::FingerprintClause::Kind::html_script_src;
    c.regex = "cdn";
    CHECK_FALSE(det::match_clause(c, snap));

    c.kind = det::FingerprintClause::Kind::html_script_type;
    c.value = "module";
    CHECK_FALSE(det::match_clause(c, snap));
}

TEST_CASE("clause matcher covers frontmatter fence and required key clauses",
          "[import-detect]") {
    det::InputSnapshot snap;
    snap.has_frontmatter_fence = true;
    snap.frontmatter_keys = {"name", "colors", "typography"};

    det::FingerprintClause fence;
    fence.kind = det::FingerprintClause::Kind::frontmatter_fence;
    CHECK(det::match_clause(fence, snap));

    det::FingerprintClause required;
    required.kind = det::FingerprintClause::Kind::frontmatter_key;
    required.required = "name";
    CHECK(det::match_clause(required, snap));

    required.required = "layout";
    CHECK_FALSE(det::match_clause(required, snap));
}

TEST_CASE("parse_compat_json rejects malformed JSON instead of reading a prefix",
          "[import-detect]") {
    const std::string valid =
        R"({"compat-schema-version": "0.3", "imports": {"x": {"parser-version": "1.0", "detected-formats": []}}})";
    REQUIRE(det::parse_compat_json(valid).has_value());
    // An unterminated string.
    CHECK_FALSE(det::parse_compat_json(
        R"({"compat-schema-version": "0.3", "imports": {}, "note": "unterminated)").has_value());
    // A missing separator between members, and between array elements.
    CHECK_FALSE(det::parse_compat_json(
        R"({"compat-schema-version": "0.3" "imports": {}})").has_value());
    CHECK_FALSE(det::parse_compat_json(
        R"({"compat-schema-version": "0.3", "imports": {"x": {"detected-formats": [{} {}]}}})").has_value());
    // Trailing non-whitespace after a complete document.
    CHECK_FALSE(det::parse_compat_json(valid + " trailing").has_value());
    CHECK_FALSE(det::parse_compat_json(valid + "}").has_value());
    // Trailing whitespace is fine.
    CHECK(det::parse_compat_json(valid + " \n\t").has_value());
}

TEST_CASE("json-key-equals ignores malformed JSON inputs", "[import-detect]") {
    det::FingerprintClause c;
    c.kind = det::FingerprintClause::Kind::json_key_equals;
    c.path = "provenance.adapter";
    c.value = "figma-plugin";
    det::InputSnapshot snap;
    snap.html_text = R"({"provenance": {"adapter": "figma-plugin"}})";
    CHECK(det::match_clause(c, snap));
    snap.html_text = R"({"provenance": {"adapter": "figma-plugin"}} trailing)";
    CHECK_FALSE(det::match_clause(c, snap));
    snap.html_text = R"({"provenance": {"adapter": "figma-plugin)";
    CHECK_FALSE(det::match_clause(c, snap));
}

TEST_CASE("the strict JSON wrapper finds the end of the top-level value",
          "[import-detect]") {
    namespace sj = vellum::authoring::strict_json;
    CHECK(sj::end_of_top_level_value(R"(  {"a": "}"} x)") == 12);
    CHECK(sj::end_of_top_level_value(R"({"a": "say \"}\" now"})") == 22);
    CHECK(sj::end_of_top_level_value(R"([1, [2, {"b": []}]])") == 19);
    CHECK(sj::end_of_top_level_value(R"({"a": [1, 2})") == std::string_view::npos);
    CHECK(sj::end_of_top_level_value(R"("bare string")") == std::string_view::npos);
    CHECK(sj::end_of_top_level_value("") == std::string_view::npos);
    CHECK(sj::parse_document(R"({"a": "}"} )").has_value());
    CHECK_FALSE(sj::parse_document(R"({"a": "}"} x)").has_value());
    CHECK_FALSE(sj::parse_document(R"({"a": 1,})").has_value());
    CHECK_FALSE(sj::parse_document(R"([1 2])").has_value());
}

TEST_CASE("directory-files never matches a single file", "[import-detect]") {
    ScratchDir scratch("directory-files-on-file");
    write_text(scratch.path / "code.html", "<html></html>");
    det::FingerprintClause c;
    c.kind = det::FingerprintClause::Kind::directory_files;
    c.files = {"code.html"};
    CHECK(det::match_clause(c, det::snapshot_input(scratch.path)));
    auto file = det::snapshot_input(scratch.path / "code.html");
    REQUIRE_FALSE(file.is_directory);
    // A file snapshot that somehow carries member names still does not match.
    file.directory_basenames = {"code.html"};
    CHECK_FALSE(det::match_clause(c, file));
}

TEST_CASE("frontmatter-fence honors its value", "[import-detect]") {
    det::InputSnapshot snap;
    snap.has_frontmatter_fence = true;
    det::FingerprintClause c;
    c.kind = det::FingerprintClause::Kind::frontmatter_fence;
    CHECK(det::match_clause(c, snap));
    c.value = "---";
    CHECK(det::match_clause(c, snap));
    c.value = "+++";
    CHECK_FALSE(det::match_clause(c, snap));
    snap.has_frontmatter_fence = false;
    c.value = "---";
    CHECK_FALSE(det::match_clause(c, snap));
}

TEST_CASE("json-key-equals follows dotted paths like the figma-plugin clauses",
          "[import-detect]") {
    det::FingerprintClause adapter;
    adapter.kind = det::FingerprintClause::Kind::json_key_equals;
    adapter.path = "provenance.adapter";
    adapter.value = "figma-plugin";
    det::FingerprintClause version = adapter;
    version.path = "format_version";
    version.value = "2026.05-figma-plugin-v1";

    det::InputSnapshot snap;
    snap.html_text = R"({"format_version": "2026.05-figma-plugin-v1",
                        "provenance": {"adapter": "figma-plugin", "version": "0.2.0"}})";
    CHECK(det::match_clause(adapter, snap));
    CHECK(det::match_clause(version, snap));

    snap.html_text = R"({"format_version": "2025.01", "provenance": {"adapter": "pencil"}})";
    CHECK_FALSE(det::match_clause(adapter, snap));
    CHECK_FALSE(det::match_clause(version, snap));
    // A path through a non-object, a missing key, and a non-string leaf.
    snap.html_text = R"({"provenance": "figma-plugin", "format_version": 2026})";
    CHECK_FALSE(det::match_clause(adapter, snap));
    CHECK_FALSE(det::match_clause(version, snap));
    snap.html_text = R"({"provenance": {}})";
    CHECK_FALSE(det::match_clause(adapter, snap));
    // An empty path or value never matches.
    det::FingerprintClause empty = adapter;
    empty.path.clear();
    snap.html_text = R"({"provenance": {"adapter": "figma-plugin"}})";
    CHECK_FALSE(det::match_clause(empty, snap));
}

TEST_CASE("tailwind tokens come only from a live Tailwind config script",
          "[import-detect]") {
    ScratchDir scratch("tailwind-scope");
    auto has = [](const det::InputSnapshot& snap, const char* token) {
        return std::find(snap.tailwind_tokens.begin(), snap.tailwind_tokens.end(),
                         token) != snap.tailwind_tokens.end();
    };

    // Plain JSON carries the same key shape but is not a Tailwind config.
    write_text(scratch.path / "plain.json", R"JSON({"on-primary": "#fff"})JSON");
    REQUIRE(det::snapshot_input(scratch.path / "plain.json").tailwind_tokens.empty());

    // Keys in an unrelated script, an HTML comment or a commented-out
    // config are not configuration either.
    write_text(scratch.path / "inert.html", R"HTML(
<!-- tailwind.config = { "surface-container": "#fff" } -->
<script>const data = { "on-primary": 1 };</script>
<script>
  // tailwind.config = { theme: { colors: { "primary": "#000" } } };
  /* tailwind.config = { "surface": "#111" }; */
</script>
)HTML");
    REQUIRE(det::snapshot_input(scratch.path / "inert.html").tailwind_tokens.empty());

    // A live config still yields its keys, a string value holding "//" does
    // not hide the keys after it, and comments inside it are skipped.
    write_text(scratch.path / "live.html", R"HTML(
<script id="tailwind-config">
  tailwind.config = { theme: { extend: { colors: {
    "logo": "https://example.com/a.svg", "on-primary": "#fff",
    // "commented-token": "#000",
    "surface-container": "#eee"
  }}}};
</script>
)HTML");
    auto live = det::snapshot_input(scratch.path / "live.html");
    REQUIRE(has(live, "on-primary"));
    REQUIRE(has(live, "surface-container"));
    REQUIRE_FALSE(has(live, "commented-token"));
}

TEST_CASE("script attributes are read outside other attributes' quoted values",
          "[import-detect]") {
    ScratchDir scratch("script-attr-quotes");
    write_text(scratch.path / "quoted.html", R"HTML(
<script data-example=' type="__bundler/template" src="/fake.js"'></script>
<script data-gt="a > b" type="module" src='/real.js'></script>
<script type="text/plain" type="__bundler/template"></script>
)HTML");
    auto snap = det::snapshot_input(scratch.path / "quoted.html");
    REQUIRE(snap.script_types == std::vector<std::string>{"module", "text/plain"});
    REQUIRE(snap.script_srcs == std::vector<std::string>{"/real.js"});
}

int main() { return import_detect_test::run_all(); }
