// SPDX-License-Identifier: MIT
//
// Implementation of the versioned import detector.

#include <vellum/authoring/import_detect.hpp>

#include "strict_json.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <string_view>
#include <utility>

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

// One <script> element: its attributes (lowercased names, first occurrence
// wins as in HTML) and the byte range of its body.
struct ScriptElement {
    std::vector<std::pair<std::string, std::string>> attrs;
    size_t body_begin = 0;
    size_t body_end = 0;
};

bool is_html_space(char ch) {
    return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' || ch == '\f';
}

std::string ascii_lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

// Tokenizes every <script> start tag in `html`. Hand-rolled to avoid pulling
// a full HTML parser into the CLI build, but it follows the attribute syntax:
// optional whitespace around '=', quoted values may contain '>' or text that
// looks like another attribute, and unquoted values end at whitespace or '>'.
// Script bodies and HTML comments are skipped, so markup inside a script or a
// commented-out <script> is not read as a tag.
std::vector<ScriptElement> scan_script_elements(const std::string& html) {
    std::vector<ScriptElement> out;
    const std::string html_lower = ascii_lower(html);
    const size_t n = html.size();
    size_t i = 0;
    while (i < n) {
        auto open = html_lower.find("<script", i);
        if (open == std::string::npos) break;
        // Markup inside an HTML comment is inert: skip a comment that starts
        // before this candidate (an unterminated comment runs to the end).
        auto comment = html_lower.find("<!--", i);
        if (comment != std::string::npos && comment < open) {
            auto comment_end = html_lower.find("-->", comment + 4);
            if (comment_end == std::string::npos) break;
            i = comment_end + 3;
            continue;
        }
        size_t p = open + 7;
        if (p < n && !(html[p] == '>' || html[p] == '/' || is_html_space(html[p]))) {
            i = p;  // <scriptish>, not a script element
            continue;
        }
        ScriptElement el;
        bool closed = false;
        while (p < n) {
            while (p < n && (is_html_space(html[p]) || html[p] == '/')) ++p;
            if (p >= n) break;
            if (html[p] == '>') {
                ++p;
                closed = true;
                break;
            }
            const size_t name_begin = p;
            while (p < n && !is_html_space(html[p]) && html[p] != '=' && html[p] != '>' &&
                   html[p] != '/')
                ++p;
            if (p == name_begin) {  // a stray '=': skip it
                ++p;
                continue;
            }
            std::string name = html_lower.substr(name_begin, p - name_begin);
            size_t q = p;
            while (q < n && is_html_space(html[q])) ++q;
            if (q >= n || html[q] != '=') {
                // A value-less attribute (`async`); it carries no value.
                continue;
            }
            p = q + 1;
            while (p < n && is_html_space(html[p])) ++p;
            std::string value;
            if (p < n && (html[p] == '"' || html[p] == '\'')) {
                const char quote = html[p++];
                auto end = html.find(quote, p);
                if (end == std::string::npos) end = n;
                value = html.substr(p, end - p);
                p = end == n ? n : end + 1;
            } else {
                const size_t value_begin = p;
                while (p < n && !is_html_space(html[p]) && html[p] != '>') ++p;
                value = html.substr(value_begin, p - value_begin);
                if (value.empty()) continue;  // `src= >`: no value
            }
            bool seen = false;
            for (const auto& kv : el.attrs) seen = seen || kv.first == name;
            if (!seen) el.attrs.emplace_back(std::move(name), std::move(value));
        }
        if (!closed) break;  // unterminated start tag
        el.body_begin = p;
        auto body_end = html_lower.find("</script", p);
        el.body_end = body_end == std::string::npos ? n : body_end;
        i = el.body_end;
        out.push_back(std::move(el));
    }
    return out;
}

// Extract all <script ...> attribute values for `attr` from `html`.
std::vector<std::string> scrape_script_attr(const std::vector<ScriptElement>& scripts,
                                            const std::string& attr) {
    std::vector<std::string> out;
    const std::string needle = ascii_lower(attr);
    for (const auto& el : scripts)
        for (const auto& kv : el.attrs)
            if (kv.first == needle) {
                out.push_back(kv.second);
                break;
            }
    return out;
}

// Greedy scrape of identifiers from a tailwind.config inline script. The
// detector matches against any-of clauses, so this just needs to surface
// representative tokens; we don't reconstruct a real tree.
//
// Only inline scripts that mention tailwind are read, so CSS (`{color:red}`),
// JSON-LD and other scripts do not contribute tokens. A tailwind.config object
// is JavaScript, so a key may be double-quoted, single-quoted (Material 3
// hyphenated tokens like 'surface-container') or a bare identifier; a bare key
// must follow '{' or ',' (object-literal position).
std::vector<std::string> scrape_tailwind_tokens(const std::string& html,
                                                const std::vector<ScriptElement>& scripts) {
    std::set<std::string> seen;
    static const std::regex double_quoted_re(
        R"REX("([a-zA-Z][a-zA-Z0-9_-]{1,40})"\s*:)REX", std::regex::ECMAScript);
    static const std::regex single_quoted_re(
        R"REX('([a-zA-Z][a-zA-Z0-9_-]{1,40})'\s*:)REX", std::regex::ECMAScript);
    static const std::regex bare_key_re(
        R"REX([{,]\s*([a-zA-Z_$][a-zA-Z0-9_$]{1,40})\s*:)REX", std::regex::ECMAScript);
    for (const auto& el : scripts) {
        const std::string body = html.substr(el.body_begin, el.body_end - el.body_begin);
        if (ascii_lower(body).find("tailwind") == std::string::npos) continue;
        for (const auto* re : {&double_quoted_re, &single_quoted_re, &bare_key_re}) {
            auto begin = std::sregex_iterator(body.begin(), body.end(), *re);
            for (auto it = begin; it != std::sregex_iterator{}; ++it)
                seen.insert((*it)[1].str());
        }
    }
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
                    // A percentage outside [0, 100] (or a non-finite float)
                    // is malformed: ignore it like a non-numeric value rather
                    // than narrowing it, which is undefined for out-of-range
                    // floats and silently wraps large integers.
                    if (cv->isInt()) {
                        const auto v = cv->getInt64();
                        if (v >= 0 && v <= 100) e.min_confidence_pct = static_cast<int>(v);
                    } else if (cv->isFloat()) {
                        const double v = cv->getFloat64();
                        if (std::isfinite(v) && v >= 0.0 && v <= 100.0)
                            e.min_confidence_pct = static_cast<int>(v);
                    }
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
        if (fs::exists(candidate)) return candidate;
        if (!cur.has_parent_path() || cur.parent_path() == cur) break;
        cur = cur.parent_path();
    }
    return {};
}

// ── Fingerprinting ─────────────────────────────────────────────────────

InputSnapshot snapshot_input(const fs::path& input) {
    InputSnapshot snap;
    snap.root = input;
    if (!fs::exists(input)) return snap;

    if (fs::is_directory(input)) {
        snap.is_directory = true;
        std::error_code ec;
        std::vector<fs::path> html_candidates;
        for (auto& entry : fs::directory_iterator(input, ec)) {
            if (ec) break;
            auto path = entry.path();
            snap.directory_basenames.push_back(path.filename().string());
            std::error_code file_ec;
            if (entry.is_regular_file(file_ec)) {
                auto ext = lower_trim(path.extension().string());
                if (ext == ".html" || ext == ".htm")
                    html_candidates.push_back(path);
            }
        }
        std::sort(snap.directory_basenames.begin(), snap.directory_basenames.end());
        std::sort(html_candidates.begin(), html_candidates.end());

        // Prefer code.html (Stitch-style), then index.html.
        for (auto candidate : {"code.html", "index.html"}) {
            auto p = input / candidate;
            if (fs::exists(p)) {
                snap.html_text = read_text_file(p);
                break;
            }
        }
        if (snap.html_text.empty() && !html_candidates.empty())
            snap.html_text = read_text_file(html_candidates.front());
    } else {
        snap.filename = input.filename().string();
        snap.directory_basenames.push_back(snap.filename);
        snap.html_text = read_text_file(input);
    }

    if (!snap.html_text.empty()) {
        const auto scripts = scan_script_elements(snap.html_text);
        snap.script_srcs = scrape_script_attr(scripts, "src");
        snap.script_types = scrape_script_attr(scripts, "type");
        snap.tailwind_tokens = scrape_tailwind_tokens(snap.html_text, scripts);
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
