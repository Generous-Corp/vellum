// SPDX-License-Identifier: MIT
//
// Implementation of the versioned import detector.

#include <vellum/authoring/import_detect.hpp>

#include "strict_json.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <string_view>

namespace fs = std::filesystem;

namespace vellum::authoring::import_detect {

namespace {

using JsonView = choc::value::ValueView;

// The named member of a JSON object, or nullopt when `v` is not an object or
// has no such member.
std::optional<JsonView> member(const JsonView& v, std::string_view name) {
    if (!v.isObject()) return std::nullopt;
    for (uint32_t i = 0; i < v.size(); ++i) {
        auto m = v.getObjectMemberAt(i);
        if (std::string_view(m.name) == name) return m.value;
    }
    return std::nullopt;
}

std::optional<std::string> member_string(const JsonView& v, std::string_view name) {
    auto m = member(v, name);
    if (!m || !m->isString()) return std::nullopt;
    return std::string(m->getString());
}

// The string elements of an array member, in order; other elements are skipped.
std::vector<std::string> member_strings(const JsonView& v, std::string_view name) {
    std::vector<std::string> out;
    auto m = member(v, name);
    if (!m || !m->isArray()) return out;
    for (uint32_t i = 0; i < m->size(); ++i) {
        auto e = (*m)[i];
        if (e.isString()) out.emplace_back(e.getString());
    }
    return out;
}

std::string read_text_file(const fs::path& p) {
    std::ifstream f(p);
    if (!f.is_open()) return {};
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

FingerprintClause parse_clause(const JsonView& v) {
    FingerprintClause c;
    if (!v.isObject()) return c;
    if (auto kind = member_string(v, "kind")) {
        c.raw_kind = *kind;
        if (*kind == "directory-files")
            c.kind = FingerprintClause::Kind::directory_files;
        else if (*kind == "html-script-src")
            c.kind = FingerprintClause::Kind::html_script_src;
        else if (*kind == "html-script-type")
            c.kind = FingerprintClause::Kind::html_script_type;
        else if (*kind == "tailwind-config-token")
            c.kind = FingerprintClause::Kind::tailwind_config_token;
        else if (*kind == "filename")
            c.kind = FingerprintClause::Kind::filename;
        else if (*kind == "frontmatter-fence")
            c.kind = FingerprintClause::Kind::frontmatter_fence;
        else if (*kind == "frontmatter-key")
            c.kind = FingerprintClause::Kind::frontmatter_key;
        else if (*kind == "json-key-equals")
            c.kind = FingerprintClause::Kind::json_key_equals;
    }
    c.files = member_strings(v, "files");
    if (auto re = member_string(v, "regex")) c.regex = *re;
    if (auto value = member_string(v, "value")) c.value = *value;
    c.any_of = member_strings(v, "any-of");
    if (auto required = member_string(v, "required")) c.required = *required;
    if (auto path = member_string(v, "path")) c.path = *path;
    return c;
}

// Lowercase + trim helper for tag-attr scrapes.
std::string lower_trim(std::string s) {
    auto first = s.find_first_not_of(" \t\r\n");
    auto last = s.find_last_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    s = s.substr(first, last - first + 1);
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

// The attributes of one opening tag, read with HTML's quoting rules so text
// inside one attribute's quoted value never counts as another attribute.
// `name_end` is the index just past the tag name. `end` is the index just
// past the closing '>', or npos when the tag never closes.
struct OpenTag {
    std::vector<std::pair<std::string, std::string>> attrs;  // lowercase name, raw value
    size_t end = std::string::npos;
};

OpenTag parse_open_tag(const std::string& html, size_t name_end) {
    OpenTag tag;
    auto is_space = [](char c) {
        return std::isspace(static_cast<unsigned char>(c)) != 0;
    };
    size_t i = name_end;
    while (i < html.size()) {
        while (i < html.size() && (is_space(html[i]) || html[i] == '/')) ++i;
        if (i >= html.size()) break;
        if (html[i] == '>') {
            tag.end = i + 1;
            return tag;
        }
        size_t name_start = i;
        while (i < html.size() && !is_space(html[i]) && html[i] != '=' &&
               html[i] != '>' && html[i] != '/')
            ++i;
        if (i == name_start) {  // a stray '=' with no name
            ++i;
            continue;
        }
        std::string name = lower_trim(html.substr(name_start, i - name_start));
        size_t after_name = i;
        while (i < html.size() && is_space(html[i])) ++i;
        if (i >= html.size() || html[i] != '=') {
            i = after_name;  // a bare attribute has no value
            continue;
        }
        ++i;
        while (i < html.size() && is_space(html[i])) ++i;
        if (i >= html.size()) break;
        std::string value;
        if (html[i] == '"' || html[i] == '\'') {
            char q = html[i++];
            auto close = html.find(q, i);
            if (close == std::string::npos) return tag;  // unterminated value
            value = html.substr(i, close - i);
            i = close + 1;
        } else {
            size_t v = i;
            while (i < html.size() && !is_space(html[i]) && html[i] != '>') ++i;
            value = html.substr(v, i - v);
        }
        tag.attrs.emplace_back(std::move(name), std::move(value));
    }
    return tag;
}

// Visit each <script> element outside HTML comments, passing its opening
// tag and its body. Hand-rolled to keep a full HTML parser out of the CLI.
template <typename Visit>
void for_each_script(const std::string& html, Visit&& visit) {
    std::string lower = html;
    for (auto& c : lower)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    size_t i = 0;
    while (i < html.size()) {
        // A commented example is not a script element. Skip the whole comment
        // whenever it appears before the next candidate tag.
        auto comment = lower.find("<!--", i);
        auto open = lower.find("<script", i);
        if (comment != std::string::npos &&
            (open == std::string::npos || comment < open)) {
            auto comment_end = lower.find("-->", comment + 4);
            if (comment_end == std::string::npos) break;
            i = comment_end + 3;
            continue;
        }
        if (open == std::string::npos) break;
        const auto name_end = open + 7;
        if (name_end < lower.size()) {
            const char next = lower[name_end];
            if (!(next == '>' || next == '/' ||
                  std::isspace(static_cast<unsigned char>(next)))) {
                i = name_end;
                continue;
            }
        }
        auto tag = parse_open_tag(html, name_end);
        if (tag.end == std::string::npos) break;
        auto body_end = lower.find("</script", tag.end);
        if (body_end == std::string::npos) body_end = html.size();
        visit(tag, std::string_view(html).substr(tag.end, body_end - tag.end));
        // Resume after the opening tag, not the body: `<script/>` has no
        // closing tag, and skipping to the next one would hide a script.
        i = tag.end;
    }
}

// Extract the value of `attr` from every <script> element in `html`.
std::vector<std::string> scrape_script_attr(const std::string& html, const std::string& attr) {
    std::vector<std::string> out;
    const std::string needle = lower_trim(attr);
    for_each_script(html, [&](const OpenTag& tag, std::string_view) {
        for (const auto& [name, value] : tag.attrs) {
            if (name == needle) {  // HTML keeps the first of duplicate attributes
                out.push_back(value);
                break;
            }
        }
    });
    return out;
}

// Remove `//` and `/* */` comments from script text, leaving string and
// template literals intact so a value such as "https://..." does not hide
// the keys that follow it on the same line.
std::string strip_js_comments(const std::string& code) {
    std::string out;
    out.reserve(code.size());
    for (size_t i = 0; i < code.size();) {
        char c = code[i];
        if (c == '"' || c == '\'' || c == '`') {
            size_t j = i + 1;
            while (j < code.size() && code[j] != c) {
                if (code[j] == '\\' && j + 1 < code.size()) ++j;
                ++j;
            }
            j = std::min(j + 1, code.size());
            out.append(code, i, j - i);
            i = j;
        } else if (c == '/' && i + 1 < code.size() && code[i + 1] == '/') {
            i = code.find('\n', i);
            if (i == std::string::npos) break;
        } else if (c == '/' && i + 1 < code.size() && code[i + 1] == '*') {
            auto close = code.find("*/", i + 2);
            if (close == std::string::npos) break;
            out.push_back(' ');
            i = close + 2;
        } else {
            out.push_back(c);
            ++i;
        }
    }
    return out;
}

// Scrape identifiers from the inline scripts that assign a Tailwind config
// (`tailwind.config = {...}` or `window.tailwind = {...}`). Only those
// script bodies count, with HTML and JS comments removed: plain JSON,
// unrelated scripts and commented-out configs carry the same key shape but
// are not a Tailwind fingerprint. The detector matches against any-of
// clauses, so this surfaces representative keys; it does not rebuild a tree.
std::vector<std::string> scrape_tailwind_tokens(const std::string& html) {
    std::set<std::string> seen;
    // Match identifier-like keys "<ident>": before a colon, loose enough to
    // handle Material 3 hyphenated tokens like "surface-container".
    static const std::regex token_re(
        R"REX("([a-zA-Z][a-zA-Z0-9_-]{1,40})"\s*:)REX",
        std::regex::ECMAScript);
    static const std::regex config_re(
        R"REX((^|[^A-Za-z0-9_$])tailwind(\.config)?\s*=[^=])REX",
        std::regex::ECMAScript);
    for_each_script(html, [&](const OpenTag&, std::string_view raw) {
        auto body = strip_js_comments(std::string(raw));
        if (!std::regex_search(body, config_re)) return;
        auto begin = std::sregex_iterator(body.begin(), body.end(), token_re);
        for (auto it = begin; it != std::sregex_iterator{}; ++it)
            seen.insert((*it)[1].str());
    });
    return {seen.begin(), seen.end()};
}

}  // namespace

// ── Manifest parsing ───────────────────────────────────────────────────

std::optional<ImportsManifest> parse_compat_json(const std::string& text) {
    if (text.empty()) return std::nullopt;
    auto document = strict_json::parse_document(text);
    if (!document || !document->isObject()) return std::nullopt;
    const JsonView root = *document;

    ImportsManifest m;
    if (auto sv = member_string(root, "compat-schema-version")) m.compat_schema_version = *sv;

    auto imports = member(root, "imports");
    if (!imports || !imports->isObject()) return std::nullopt;

    for (uint32_t i = 0; i < imports->size(); ++i) {
        auto src = imports->getObjectMemberAt(i);
        if (!src.value.isObject()) continue;
        SourceEntry s;
        s.source = src.name;
        if (auto pv = member_string(src.value, "parser-version")) s.parser_version = *pv;

        auto dfs = member(src.value, "detected-formats");
        if (dfs && dfs->isArray()) {
            for (uint32_t j = 0; j < dfs->size(); ++j) {
                const JsonView f = (*dfs)[j];
                if (!f.isObject()) continue;
                FormatEntry e;
                e.parser_version = s.parser_version;
                if (auto fv = member_string(f, "format-version")) e.format_version = *fv;
                if (auto iv = member_string(f, "introduced")) e.introduced = *iv;
                if (auto dv = member_string(f, "deprecated")) e.deprecated = *dv;
                if (auto nv = member_string(f, "notes")) e.notes = *nv;
                if (auto mv = member_string(f, "match")) e.match = *mv;
                if (auto cv = member(f, "min-confidence-pct")) {
                    if (cv->isInt()) e.min_confidence_pct = static_cast<int>(cv->getInt64());
                    else if (cv->isFloat()) e.min_confidence_pct = static_cast<int>(cv->getFloat64());
                }
                auto fp = member(f, "fingerprint");
                if (fp && fp->isArray()) {
                    for (uint32_t k = 0; k < fp->size(); ++k)
                        e.fingerprint.push_back(parse_clause((*fp)[k]));
                }
                s.formats.push_back(std::move(e));
            }
        }
        m.sources.push_back(std::move(s));
    }
    return m;
}

fs::path find_compat_json(const fs::path& start_dir) {
    fs::path cur = fs::absolute(start_dir);
    for (int i = 0; i < 32; ++i) {
        auto candidate = cur / "compat.json";
        std::error_code ec;
        if (fs::exists(candidate, ec) && !ec) return candidate;
        if (!cur.has_parent_path() || cur.parent_path() == cur) break;
        cur = cur.parent_path();
    }
    return {};
}

// ── Fingerprinting ─────────────────────────────────────────────────────

InputSnapshot snapshot_input(const fs::path& input) {
    InputSnapshot snap;
    snap.root = input;
    std::error_code input_ec;
    if (!fs::exists(input, input_ec) || input_ec) return snap;

    const bool input_is_directory = fs::is_directory(input, input_ec);
    if (input_ec) return snap;
    if (input_is_directory) {
        snap.is_directory = true;
        std::error_code ec;
        std::vector<fs::path> html_candidates;
        std::vector<fs::path> markdown_candidates;
        for (auto& entry : fs::directory_iterator(input, ec)) {
            if (ec) break;
            auto path = entry.path();
            snap.directory_basenames.push_back(path.filename().string());
            std::error_code file_ec;
            if (entry.is_regular_file(file_ec)) {
                auto ext = lower_trim(path.extension().string());
                if (ext == ".html" || ext == ".htm")
                    html_candidates.push_back(path);
                else if (lower_trim(path.filename().string()) == "design.md")
                    markdown_candidates.push_back(path);
            }
        }
        std::sort(snap.directory_basenames.begin(), snap.directory_basenames.end());
        std::sort(html_candidates.begin(), html_candidates.end());

        // Prefer code.html (Stitch-style), then index.html.
        for (auto candidate : {"code.html", "index.html"}) {
            auto p = input / candidate;
            std::error_code candidate_ec;
            if (fs::exists(p, candidate_ec) && !candidate_ec) {
                snap.html_text = read_text_file(p);
                break;
            }
        }
        if (snap.html_text.empty() && !html_candidates.empty())
            snap.html_text = read_text_file(html_candidates.front());
        // A DESIGN.md-only export has no HTML primary. Promote its Markdown
        // member to the primary text so filename and frontmatter fingerprints
        // can detect the directory just like a direct DESIGN.md input.
        if (snap.html_text.empty() && !markdown_candidates.empty()) {
            std::sort(markdown_candidates.begin(), markdown_candidates.end());
            snap.filename = markdown_candidates.front().filename().string();
            snap.html_text = read_text_file(markdown_candidates.front());
        }
    } else {
        snap.filename = input.filename().string();
        snap.directory_basenames.push_back(snap.filename);
        snap.html_text = read_text_file(input);
    }

    if (!snap.html_text.empty()) {
        snap.script_srcs = scrape_script_attr(snap.html_text, "src");
        snap.script_types = scrape_script_attr(snap.html_text, "type");
        snap.tailwind_tokens = scrape_tailwind_tokens(snap.html_text);
    }

    // ── DESIGN.md frontmatter probe ────────────────────────────────────
    // Cheap leading-bytes check: only walk the file as frontmatter when it
    // starts with `---` followed by a newline. Then look for the closing
    // fence and collect top-level YAML keys. Hand-rolled rather than
    // pulling yaml-cpp into the detector library, which keeps the detector
    // unit tests fast and the detect-only path zero-cost on non-MD inputs.
    auto probe_frontmatter = [&snap](const std::string& text) {
        if (text.size() < 4 || text.substr(0, 3) != "---") return;
        if (text[3] != '\n' && text[3] != '\r') return;
        size_t i = (text[3] == '\r' && text.size() > 4 && text[4] == '\n') ? 5 : 4;
        // Find a line that is exactly "---" (optionally with trailing CR).
        size_t close = std::string::npos;
        for (size_t pos = i; pos < text.size(); ) {
            size_t eol = text.find('\n', pos);
            std::string line = text.substr(pos, (eol == std::string::npos ? text.size() : eol) - pos);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            // Trim trailing spaces.
            while (!line.empty() && (line.back() == ' ' || line.back() == '\t')) line.pop_back();
            if (line == "---") { close = pos; break; }
            if (eol == std::string::npos) break;
            pos = eol + 1;
        }
        if (close == std::string::npos) return;
        snap.has_frontmatter_fence = true;
        // Walk every top-level YAML key, defined as `^[A-Za-z_][A-Za-z0-9_-]*:`
        // with zero leading whitespace, between the opening and closing fences.
        for (size_t pos = i; pos < close; ) {
            size_t eol = text.find('\n', pos);
            size_t end = (eol == std::string::npos || eol > close) ? close : eol;
            std::string line = text.substr(pos, end - pos);
            if (eol == std::string::npos) pos = close;
            else pos = eol + 1;
            if (line.empty() || line[0] == ' ' || line[0] == '\t' || line[0] == '#') continue;
            // Strip CR.
            if (!line.empty() && line.back() == '\r') line.pop_back();
            auto colon = line.find(':');
            if (colon == std::string::npos) continue;
            std::string key = line.substr(0, colon);
            // Validate key shape.
            bool ok = !key.empty() && (std::isalpha(static_cast<unsigned char>(key[0])) || key[0] == '_');
            for (char c : key) {
                if (!ok) break;
                if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-')) ok = false;
            }
            if (ok) snap.frontmatter_keys.push_back(key);
        }
    };
    if (!snap.html_text.empty()) {
        // The `html_text` field is also the "primary text" for any input.
        // Probe only when the filename ends in `.md` to avoid wasting
        // cycles on non-Markdown inputs.
        std::string lower_name = snap.filename;
        std::transform(lower_name.begin(), lower_name.end(), lower_name.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (lower_name.size() >= 3 && lower_name.substr(lower_name.size() - 3) == ".md")
            probe_frontmatter(snap.html_text);
    }
    return snap;
}

bool match_clause(const FingerprintClause& clause, const InputSnapshot& snap) {
    switch (clause.kind) {
        case FingerprintClause::Kind::directory_files: {
            // Only a directory export has member files; a single file whose
            // name happens to match never satisfies this clause.
            if (!snap.is_directory || clause.files.empty()) return false;
            for (const auto& want : clause.files) {
                bool found = false;
                for (const auto& have : snap.directory_basenames) {
                    if (have == want) { found = true; break; }
                }
                if (!found) return false;
            }
            return true;
        }
        case FingerprintClause::Kind::html_script_src: {
            if (clause.regex.empty() || snap.script_srcs.empty()) return false;
            std::regex re;
            try { re = std::regex(clause.regex, std::regex::ECMAScript); }
            catch (...) { return false; }
            for (const auto& src : snap.script_srcs)
                if (std::regex_search(src, re)) return true;
            return false;
        }
        case FingerprintClause::Kind::html_script_type: {
            if (clause.value.empty() || snap.script_types.empty()) return false;
            auto want = lower_trim(clause.value);
            for (const auto& t : snap.script_types)
                if (lower_trim(t) == want) return true;
            return false;
        }
        case FingerprintClause::Kind::tailwind_config_token: {
            if (clause.any_of.empty() || snap.tailwind_tokens.empty()) return false;
            for (const auto& want : clause.any_of) {
                for (const auto& have : snap.tailwind_tokens)
                    if (have == want) return true;
            }
            return false;
        }
        case FingerprintClause::Kind::filename: {
            if (clause.regex.empty() || snap.filename.empty()) return false;
            // Honor leading `(?i)` as a case-insensitive flag rather than
            // an inline group: libc++'s ECMAScript engine handles
            // `(?i)` unevenly across versions, and this is the only
            // flag DESIGN.md's fingerprint needs.
            std::string pattern = clause.regex;
            auto flags = std::regex::ECMAScript;
            if (pattern.compare(0, 4, "(?i)") == 0) {
                pattern = pattern.substr(4);
                flags = flags | std::regex::icase;
            }
            std::regex re;
            try { re = std::regex(pattern, flags); }
            catch (...) { return false; }
            return std::regex_search(snap.filename, re);
        }
        case FingerprintClause::Kind::frontmatter_fence: {
            // The snapshot only recognizes a `---` fence, so a clause asking
            // for any other fence value cannot match. Empty means presence.
            if (!clause.value.empty() && clause.value != "---") return false;
            return snap.has_frontmatter_fence;
        }
        case FingerprintClause::Kind::frontmatter_key: {
            if (snap.frontmatter_keys.empty()) return false;
            if (!clause.required.empty()) {
                for (const auto& have : snap.frontmatter_keys)
                    if (have == clause.required) return true;
                return false;
            }
            for (const auto& want : clause.any_of) {
                for (const auto& have : snap.frontmatter_keys)
                    if (have == want) return true;
            }
            return false;
        }
        case FingerprintClause::Kind::json_key_equals: {
            // Parse snap.html_text as JSON (the field name is historical;
            // snapshot_input reads any text file into it). Walk the dotted
            // path and compare the resulting string value to clause.value.
            // Returns false on any parse failure / missing key / type
            // mismatch.
            if (clause.path.empty() || clause.value.empty() || snap.html_text.empty())
                return false;
            auto document = strict_json::parse_document(snap.html_text);
            if (!document) return false;
            std::optional<JsonView> cur = JsonView(*document);
            std::string remaining = clause.path;
            while (!remaining.empty()) {
                auto dot = remaining.find('.');
                std::string segment = (dot == std::string::npos) ? remaining
                                                                 : remaining.substr(0, dot);
                cur = member(*cur, segment);
                if (!cur) return false;
                if (dot == std::string::npos) break;
                remaining = remaining.substr(dot + 1);
            }
            return cur && cur->isString() && cur->getString() == clause.value;
        }
        case FingerprintClause::Kind::unknown:
        default:
            return false;
    }
}

}  // namespace vellum::authoring::import_detect
