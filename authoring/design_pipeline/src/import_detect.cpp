// SPDX-License-Identifier: MIT
//
// Implementation of the versioned import detector.

#include <vellum/authoring/import_detect.hpp>

#include "strict_json.hpp"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <fstream>
#include <optional>
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

bool is_html_space(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f';
}

// Whether `lower` opens the element `name` at `pos`: "<name" followed by a
// tag delimiter or the end of the text, so "<scriptish" is not "<script".
bool opens_element(const std::string& lower, size_t pos, std::string_view name) {
    if (lower.size() - pos < name.size() + 1 || lower[pos] != '<' ||
        lower.compare(pos + 1, name.size(), name) != 0)
        return false;
    const size_t after = pos + 1 + name.size();
    return after == lower.size() || is_html_space(lower[after]) ||
           lower[after] == '>' || lower[after] == '/';
}

// Reads the attributes of a start tag from `pos` (just past the tag name) to
// its '>', following HTML attribute syntax: whitespace may surround '=', a
// quoted value may hold '>', and an unquoted value ends at whitespace or '>'.
// Stores the first value of attribute `needle` (lowercase) in `value`, and
// whether the tag ends in "/>" in `self_closing`. Returns the index of the
// closing '>', or npos for an unterminated tag.
size_t read_start_tag(const std::string& html, const std::string& lower, size_t pos,
                      std::string_view needle, std::optional<std::string>& value,
                      bool& self_closing) {
    const size_t n = html.size();
    size_t i = pos;
    while (i < n) {
        bool slash = false;
        while (i < n && (is_html_space(lower[i]) || lower[i] == '/')) slash = lower[i++] == '/';
        if (i >= n) break;
        if (lower[i] == '>') {
            self_closing = slash;
            return i;
        }
        const size_t name_start = i;
        // A leading '=' belongs to the attribute name in HTML.
        ++i;
        while (i < n && !is_html_space(lower[i]) && lower[i] != '/' &&
               lower[i] != '>' && lower[i] != '=')
            ++i;
        const std::string_view name(lower.data() + name_start, i - name_start);
        size_t j = i;
        while (j < n && is_html_space(lower[j])) ++j;
        if (j >= n || lower[j] != '=') {
            // An attribute with no value is present with an empty one.
            if (!value && name == needle) value = std::string();
            i = j;
            continue;
        }
        ++j;
        while (j < n && is_html_space(lower[j])) ++j;
        if (j >= n) break;
        std::string attr_value;
        if (html[j] == '"' || html[j] == '\'') {
            const char quote = html[j++];
            const size_t close = html.find(quote, j);
            if (close == std::string::npos) break;
            attr_value = html.substr(j, close - j);
            i = close + 1;
        } else {
            size_t close = j;
            while (close < n && !is_html_space(lower[close]) && lower[close] != '>') ++close;
            attr_value = html.substr(j, close - j);
            i = close;
        }
        if (!value && name == needle) value = std::move(attr_value);
    }
    return std::string::npos;
}

bool is_ascii_alpha(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

// The index of the end tag "</name" at or after `from` whose name is not just
// a prefix of a longer one ("</scripture" does not close a script), or npos.
size_t find_end_tag(const std::string& lower, size_t from, std::string_view name) {
    const std::string needle = "</" + std::string(name);
    for (auto at = lower.find(needle, from); at != std::string::npos;
         at = lower.find(needle, at + 1)) {
        const size_t after = at + needle.size();
        if (after == lower.size() || is_html_space(lower[after]) ||
            lower[after] == '>' || lower[after] == '/')
            return at;
    }
    return std::string::npos;
}

std::string ascii_lower(std::string text) {
    for (auto& c : text) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return text;
}

// A real <script> element found by find_script_elements: its attributes
// start at `attrs_begin`, and its text is [content_begin, content_end).
struct ScriptElement {
    size_t attrs_begin = 0;
    size_t content_begin = 0;
    size_t content_end = 0;
};

// The <script> elements of `html` (`lower` is its lowercase copy), found with
// a hand-rolled scan to avoid pulling a full HTML parser into the CLI build.
// Only real elements count: comments, the contents of raw-text and escapable
// raw-text elements (style, textarea, title, iframe, xmp, noembed, noframes,
// and script itself) and all text after <plaintext> are skipped, so
// commented-out or quoted markup cannot select an import source. Other tags
// are read past quote-aware, and an end tag must name its element exactly
// ("</stylesheet>" does not close a style element). A start tag ending in
// "/>" is taken as an empty element, as XHTML writes it.
std::vector<ScriptElement> find_script_elements(const std::string& html, const std::string& lower) {
    static constexpr std::string_view kRawTextElements[] = {
        "script", "style", "textarea", "title", "iframe", "xmp", "noembed", "noframes"};
    std::vector<ScriptElement> out;
    std::optional<std::string> ignored;
    size_t i = 0;
    while (i < html.size()) {
        const auto open = lower.find('<', i);
        if (open == std::string::npos) break;
        if (lower.compare(open, 4, "<!--") == 0) {
            const auto end = lower.find("-->", open + 4);
            if (end == std::string::npos) break;
            i = end + 3;
            continue;
        }
        // Everything after a plaintext start tag is text.
        if (opens_element(lower, open, "plaintext")) break;
        i = open + 1;
        bool raw_text = false;
        for (const auto element : kRawTextElements) {
            if (!opens_element(lower, open, element)) continue;
            raw_text = true;
            const size_t attrs_begin = open + 1 + element.size();
            bool self_closing = false;
            const auto tag_end =
                read_start_tag(html, lower, attrs_begin, std::string_view(), ignored, self_closing);
            if (tag_end == std::string::npos) return out;
            // Exports written as XHTML close an empty element with "/>".
            const auto close = self_closing ? std::string::npos
                                            : find_end_tag(lower, tag_end + 1, element);
            if (element == "script") {
                const size_t content_end =
                    self_closing ? tag_end + 1 : (close == std::string::npos ? html.size() : close);
                out.push_back({attrs_begin, tag_end + 1, content_end});
            }
            if (self_closing) {
                i = tag_end + 1;
                break;
            }
            if (close == std::string::npos) return out;
            // Read the whole end tag quote-aware: HTML consumes attributes on
            // an end tag too, so markup quoted there is never an element.
            const auto end_tag_end = read_start_tag(html, lower, close + 2 + element.size(),
                                                    std::string_view(), ignored, self_closing);
            if (end_tag_end == std::string::npos) return out;
            i = end_tag_end + 1;
            break;
        }
        if (raw_text || open + 1 >= html.size()) continue;
        const char next = lower[open + 1];
        const bool end_tag = next == '/' && open + 2 < html.size() && is_ascii_alpha(lower[open + 2]);
        if (is_ascii_alpha(next) || end_tag) {
            // Any other start or end tag: read past it quote-aware, so markup
            // quoted in one of its attribute values is never taken for an
            // element (HTML consumes attributes on end tags too).
            size_t name_end = open + (end_tag ? 3 : 2);
            while (name_end < html.size() && !is_html_space(lower[name_end]) &&
                   lower[name_end] != '/' && lower[name_end] != '>')
                ++name_end;
            bool self_closing = false;
            const auto tag_end =
                read_start_tag(html, lower, name_end, std::string_view(), ignored, self_closing);
            if (tag_end == std::string::npos) return out;
            i = tag_end + 1;
        } else if (next == '/' || next == '!' || next == '?') {
            // Doctypes and bogus comments run to the next '>'.
            const auto tag_end = lower.find('>', open + 2);
            if (tag_end == std::string::npos) return out;
            i = tag_end + 1;
        }
    }
    return out;
}

// The first value of attribute `attr` on the script element `element`.
std::optional<std::string> script_attr(const std::string& html, const std::string& lower,
                                       const ScriptElement& element, const std::string& attr) {
    std::optional<std::string> value;
    bool self_closing = false;
    read_start_tag(html, lower, element.attrs_begin, ascii_lower(attr), value, self_closing);
    return value;
}

// Extract the `attr` values of every real <script> element in `html`.
std::vector<std::string> scrape_script_attr(const std::string& html, const std::string& attr) {
    const std::string lower = ascii_lower(html);
    std::vector<std::string> out;
    for (const auto& element : find_script_elements(html, lower))
        if (auto value = script_attr(html, lower, element, attr)) out.push_back(std::move(*value));
    return out;
}

// Whether a script `type` value makes a classic or module JavaScript script,
// per the HTML JavaScript MIME type essences; any other type (JSON, a
// template, a bundler payload) is data the browser never runs.
bool is_javascript_type(const std::optional<std::string>& type) {
    static constexpr std::string_view kJavaScriptTypes[] = {
        "", "module", "text/javascript", "application/javascript", "application/x-javascript",
        "application/ecmascript", "application/x-ecmascript", "text/ecmascript",
        "text/javascript1.0", "text/javascript1.1", "text/javascript1.2", "text/javascript1.3",
        "text/javascript1.4", "text/javascript1.5", "text/jscript", "text/livescript",
        "text/x-ecmascript", "text/x-javascript"};
    if (!type) return true;
    const auto value = lower_trim(*type);
    return std::find(std::begin(kJavaScriptTypes), std::end(kJavaScriptTypes), value) !=
           std::end(kJavaScriptTypes);
}

bool is_js_identifier_char(char c) {
    return std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '$';
}

// Whether a '/' that follows the JavaScript already copied to `out` starts a
// regular expression literal rather than a division: it does at the start,
// after a punctuator that ends no operand, and after a keyword that takes an
// expression; after an identifier, number, ')' or ']' it divides.
bool slash_starts_regex(const std::string& out) {
    static constexpr std::string_view kKeywords[] = {
        "await", "case", "delete", "do", "else", "in", "instanceof", "new",
        "of", "return", "throw", "typeof", "void", "yield"};
    const auto last = out.find_last_not_of(" \t\r\n\f\v");
    if (last == std::string::npos) return true;
    if (is_js_identifier_char(out[last])) {
        size_t first = last;
        while (first > 0 && is_js_identifier_char(out[first - 1])) --first;
        const std::string_view word(out.data() + first, last - first + 1);
        return std::find(std::begin(kKeywords), std::end(kKeywords), word) != std::end(kKeywords);
    }
    return std::string_view("(,=:[!&|?{};+-*%<>~^").find(out[last]) != std::string_view::npos;
}

// `code` with its JavaScript comments removed. String, template and regular
// expression literals are kept whole, so "//" inside a URL string or a
// regex such as /\/\// is not taken for a comment.
std::string strip_js_comments(std::string_view code) {
    std::string out;
    out.reserve(code.size());
    size_t i = 0;
    while (i < code.size()) {
        const char c = code[i];
        if (c == '"' || c == '\'' || c == '`') {
            const size_t start = i++;
            while (i < code.size() && code[i] != c) i += code[i] == '\\' ? 2 : 1;
            i = std::min(i + 1, code.size());
            out.append(code.substr(start, i - start));
        } else if (code.compare(i, 2, "//") == 0) {
            // A line comment ends at any JavaScript line terminator: LF, CR,
            // or U+2028 / U+2029 (UTF-8 E2 80 A8 / E2 80 A9).
            while (i < code.size() && code[i] != '\n' && code[i] != '\r' &&
                   !(code.compare(i, 3, "\xE2\x80\xA8") == 0 ||
                     code.compare(i, 3, "\xE2\x80\xA9") == 0))
                ++i;
        } else if (code.compare(i, 2, "/*") == 0) {
            const auto end = code.find("*/", i + 2);
            i = end == std::string_view::npos ? code.size() : end + 2;
            out.push_back(' ');
        } else if (c == '/' && slash_starts_regex(out)) {
            // A regex literal runs to the next unescaped '/' outside a
            // [...] class, then its flags.
            const size_t start = i++;
            bool in_class = false;
            while (i < code.size() && code[i] != '\n' && code[i] != '\r') {
                const char r = code[i];
                if (r == '\\') {
                    i += 2;
                    continue;
                }
                ++i;
                if (r == '[') in_class = true;
                else if (r == ']') in_class = false;
                else if (r == '/' && !in_class) break;
            }
            while (i < code.size() && is_js_identifier_char(code[i])) ++i;
            i = std::min(i, code.size());
            out.append(code.substr(start, i - start));
        } else {
            out.push_back(c);
            ++i;
        }
    }
    return out;
}

// Greedy scrape of identifiers from a tailwind.config inline script.
// We grep for sequences inside theme.extend.colors / .spacing / similar
// keys. The detector matches against any-of clauses, so this just needs
// to surface representative tokens; we don't reconstruct a real tree.
// Only the code of an inline JavaScript <script> that configures Tailwind
// (its code, comments removed, names `tailwind`) is read. A script with a
// src runs the file, not its text, and JSON or template scripts are data,
// so tokens in comments, other markup, unrelated scripts or page text never
// count.
std::vector<std::string> scrape_tailwind_tokens(const std::string& html) {
    std::set<std::string> seen;
    // Match identifier-like keys "<ident>" or '<ident>': before a colon in
    // a JS/JSON-ish context. Strict enough to skip noise but loose
    // enough to handle Material 3 hyphenated tokens like
    // "surface-container".
    static const std::regex token_re(
        R"REX((["'])([a-zA-Z][a-zA-Z0-9_-]{1,40})\1\s*:)REX",
        std::regex::ECMAScript);
    const std::string lower = ascii_lower(html);
    for (const auto& element : find_script_elements(html, lower)) {
        if (script_attr(html, lower, element, "src") ||
            !is_javascript_type(script_attr(html, lower, element, "type")))
            continue;
        const std::string code = strip_js_comments(std::string_view(html).substr(
            element.content_begin, element.content_end - element.content_begin));
        if (ascii_lower(code).find("tailwind") == std::string::npos) continue;
        for (auto it = std::sregex_iterator(code.begin(), code.end(), token_re);
             it != std::sregex_iterator{}; ++it)
            seen.insert((*it)[2].str());
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
