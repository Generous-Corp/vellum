#include <pulp/view/design_import.hpp>
#include <pulp/view/anchor_strategy.hpp>
#include <pulp/view/input_events.hpp>
#include <pulp/runtime/base64.hpp>
#include <pulp/runtime/zip.hpp>
#include <pulp/view/script_engine.hpp>
#include <pulp/view/widget_bridge.hpp>
#include <pulp/view/view.hpp>
#include <pulp/state/store.hpp>
#include <choc/text/choc_JSON.h>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <functional>
#include <regex>
#include <cmath>
#include <map>
#include <unordered_set>

namespace pulp::view {

// ── Design source helpers ───────────────────────────────────────────────

std::optional<DesignSource> parse_design_source(const std::string& name) {
    if (name == "figma")    return DesignSource::figma;
    if (name == "stitch")   return DesignSource::stitch;
    if (name == "v0")       return DesignSource::v0;
    if (name == "pencil")   return DesignSource::pencil;
    if (name == "claude")   return DesignSource::claude;
    if (name == "designmd") return DesignSource::designmd;
    if (name == "jsx")      return DesignSource::jsx;
    return std::nullopt;
}

const char* design_source_name(DesignSource source) {
    switch (source) {
        case DesignSource::figma:    return "Figma";
        case DesignSource::stitch:   return "Stitch";
        case DesignSource::v0:       return "v0";
        case DesignSource::pencil:   return "Pencil";
        case DesignSource::claude:   return "Claude Design";
        case DesignSource::designmd: return "DESIGN.md";
        case DesignSource::jsx:      return "JSX instrument";
    }
    return "unknown";
}

/// Phase 9: motion provenance vendor key. Matches the `source` token
/// the import CLI accepts, lowercased + slash-friendly so the resulting
/// `source_id` (e.g. "figma:LevelMeter/Panel") is easy to grep through
/// fixtures. Stable across releases — fixtures depend on these strings.
const char* design_source_vendor_key(DesignSource source) {
    switch (source) {
        case DesignSource::figma:    return "figma";
        case DesignSource::stitch:   return "stitch";
        case DesignSource::v0:       return "v0";
        case DesignSource::pencil:   return "pencil";
        case DesignSource::claude:   return "claude";
        case DesignSource::designmd: return "designmd";
    }
    return "unknown";
}

DesignIR parse_claude_html(const std::string& html) {
    // Claude Design exports the same HTML+CSS shape as other web tools,
    // so parse with the existing Stitch HTML pipeline and re-tag the
    // source. Per pulp #468 (manual-export framing), users hand the
    // exported HTML over directly — no Anthropic API integration.
    auto ir = parse_stitch_html(html);
    ir.source = DesignSource::claude;
    // Phase 0a: re-tag provenance after the Stitch parser stamped it.
    // Anchors were already assigned by parse_stitch_html with the
    // content-hash strategy — same strategy claude-design-html uses
    // (see DEFAULT_ANCHOR_STRATEGY in anchors.ts), so we don't reassign.
    // parse_stitch_html always populates provenance on both its JSON and
    // regex-fallback paths, so the optional is always set here.
    if (ir.root.provenance) ir.root.provenance->adapter = "claude-design-html";
    return ir;
}

// ── Claude Design classname extraction (pulp #1035) ─────────────────────
//
// Mirrors Spectr's `tools/extract-html-bundle/extract.mjs` classname
// pass: pull every `<style>...</style>` block, parse the CSS rules, and
// emit `classname → { cssProp(camelCase): cssValue, ... }`. The
// `@pulp/css-adapt` layer downstream consumes this map to merge
// class-based styles into inline before forwarding to bridge calls.

namespace {

// Convert a CSS hyphen-cased property name (`font-family`) to a
// JS-friendly camelCase key (`fontFamily`). Mirrors Spectr's
// `parseDeclarationsToCamelCase` so the artifacts stay byte-compatible.
// Pure string-ops — no allocation beyond the result.
std::string css_prop_to_camel_case(const std::string& prop) {
    std::string out;
    out.reserve(prop.size());
    bool upper_next = false;
    for (char c : prop) {
        if (c == '-') {
            upper_next = true;
        } else if (upper_next) {
            out += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            upper_next = false;
        } else {
            out += c;
        }
    }
    return out;
}

// Trim ASCII whitespace from both ends of a string view in place.
std::string trim_ascii_ws(std::string_view sv) {
    size_t i = 0, j = sv.size();
    while (i < j && std::isspace(static_cast<unsigned char>(sv[i]))) ++i;
    while (j > i && std::isspace(static_cast<unsigned char>(sv[j - 1]))) --j;
    return std::string(sv.substr(i, j - i));
}

// Strip CSS `/* ... */` comments from a block. Multi-line safe. We
// don't need to be a full CSS tokenizer — Claude Design exports use
// vanilla rules, not CSS-in-JS or nested at-rules.
std::string strip_css_comments(const std::string& css) {
    std::string out;
    out.reserve(css.size());
    size_t i = 0;
    while (i < css.size()) {
        if (i + 1 < css.size() && css[i] == '/' && css[i + 1] == '*') {
            auto end = css.find("*/", i + 2);
            if (end == std::string::npos) break;  // unterminated — drop rest
            i = end + 2;
        } else {
            out += css[i++];
        }
    }
    return out;
}

// Parse a CSS declaration block body (the text between `{` and `}`)
// into camelCase prop → value pairs. Splits on `;`, then on the first
// `:` per declaration. Skips empty declarations and bare colons.
std::map<std::string, std::string> parse_css_declarations(const std::string& body) {
    std::map<std::string, std::string> out;
    size_t i = 0;
    while (i < body.size()) {
        auto semi = body.find(';', i);
        std::string decl = body.substr(i, (semi == std::string::npos ? body.size() : semi) - i);
        i = (semi == std::string::npos) ? body.size() : semi + 1;
        auto colon = decl.find(':');
        if (colon == std::string::npos) continue;
        auto prop = trim_ascii_ws(std::string_view(decl).substr(0, colon));
        auto value = trim_ascii_ws(std::string_view(decl).substr(colon + 1));
        if (prop.empty() || value.empty()) continue;
        out[css_prop_to_camel_case(prop)] = value;
    }
    return out;
}

// Walk a CSS source string (the inside of one `<style>` block) and
// merge every `.classname { ... }` rule into `into`. Skips at-rules
// (anything that begins with `@`), `:root` blocks, descendant /
// pseudo-class selectors (anything with whitespace, `:`, `>` or `,`
// between the dot and the `{`), and `.scheme-*` selectors (those are
// already handled as theme-mode token overrides upstream).
//
// We hand-walk character-by-character rather than regex because:
//   1. CSS values can contain `{` (e.g. `linear-gradient(...)`) — but
//      not at the top level of a declaration block.
//   2. The selector list can include commas, so we need to split on
//      `,` and apply the same body to every classname in the list.
void collect_classnames_from_css(const std::string& css_in,
                                 ClaudeClassNameRules& into) {
    auto css = strip_css_comments(css_in);
    size_t i = 0;
    while (i < css.size()) {
        // Skip whitespace.
        while (i < css.size() && std::isspace(static_cast<unsigned char>(css[i]))) ++i;
        if (i >= css.size()) break;

        // Find the next `{` that opens a rule body. Anything between
        // `i` and that brace is the selector list.
        auto open = css.find('{', i);
        if (open == std::string::npos) break;
        std::string selector_list = css.substr(i, open - i);

        // Find the matching `}`. Top-level only — declaration values
        // never embed `{...}` braces in well-formed CSS.
        auto close = css.find('}', open + 1);
        if (close == std::string::npos) break;
        std::string body = css.substr(open + 1, close - (open + 1));
        i = close + 1;

        // Skip at-rules (`@media`, `@font-face`, `@keyframes`, etc.).
        // The first non-whitespace char of the selector tells us.
        auto first_non_ws = selector_list.find_first_not_of(" \t\r\n");
        if (first_non_ws == std::string::npos) continue;
        if (selector_list[first_non_ws] == '@') continue;

        // Split selector_list on top-level commas.
        std::vector<std::string> selectors;
        size_t s_start = 0;
        for (size_t k = 0; k <= selector_list.size(); ++k) {
            if (k == selector_list.size() || selector_list[k] == ',') {
                selectors.push_back(trim_ascii_ws(
                    std::string_view(selector_list).substr(s_start, k - s_start)));
                s_start = k + 1;
            }
        }

        // Parse the body once — every matching selector references the
        // same map.
        std::optional<std::map<std::string, std::string>> decls;

        for (auto& sel : selectors) {
            // Only accept simple `.classname` selectors. The classname
            // grammar matches Spectr's regex: `[a-zA-Z][a-zA-Z0-9_-]*`.
            // A trailing chained selector (`.foo .bar`, `.foo > .bar`,
            // `.foo:hover`, `.foo[data-x]`) means this isn't a plain
            // classname rule — skip it.
            if (sel.empty() || sel[0] != '.') continue;
            std::string name;
            size_t k = 1;
            if (k >= sel.size() || !(std::isalpha(static_cast<unsigned char>(sel[k])) || sel[k] == '_'))
                continue;
            while (k < sel.size() &&
                   (std::isalnum(static_cast<unsigned char>(sel[k])) ||
                    sel[k] == '_' || sel[k] == '-')) {
                name += sel[k++];
            }
            // Anything left over → not a plain classname selector.
            if (k != sel.size()) continue;
            if (name.empty()) continue;
            // Theme-scope selectors are handled upstream as token
            // overrides, not classname rules.
            if (name.rfind("scheme-", 0) == 0) continue;

            if (!decls) decls = parse_css_declarations(body);
            if (decls->empty()) continue;

            // Cascade: later blocks override earlier ones for the same
            // classname. Per-prop merge keeps unrelated declarations
            // from being lost when two blocks define the same class.
            auto& existing = into[name];
            for (auto& [prop, val] : *decls) {
                existing[prop] = val;
            }
        }
    }
}

// Pull every `<style>...</style>` block out of an HTML string, in
// document order. Skips `<style>` blocks whose first 200 chars contain
// `font-face` (matches Spectr's filter — those carry only `@font-face`
// rules, no classnames). Returns the inner CSS bodies.
std::vector<std::string> extract_html_style_blocks(const std::string& html) {
    std::vector<std::string> blocks;
    static const std::regex style_re(
        R"RX(<style\b[^>]*>([\s\S]*?)</style>)RX",
        std::regex::icase);
    auto begin = std::sregex_iterator(html.begin(), html.end(), style_re);
    auto end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) {
        std::string body = (*it)[1].str();
        // Spectr's filter: skip blocks whose head looks like @font-face.
        // The head check (first 200 chars) keeps a normal classname
        // block that happens to mention `font-face` later from being
        // dropped.
        std::string head = body.substr(0, std::min<size_t>(body.size(), 200));
        std::transform(head.begin(), head.end(), head.begin(),
            [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (head.find("font-face") != std::string::npos) continue;
        blocks.push_back(std::move(body));
    }
    return blocks;
}

} // namespace

ClaudeClassNameRules extract_claude_classnames(const std::string& html) {
    ClaudeClassNameRules rules;

    // Walk the raw HTML's <style> blocks first. For non-bundled
    // exports (the `--no-bundle` flow, or hand-written test fixtures)
    // this is the only source.
    for (auto& css : extract_html_style_blocks(html)) {
        collect_classnames_from_css(css, rules);
    }

    // For self-bundled Claude Design exports, the actual app CSS lives
    // inside the `<script type="__bundler/template">` payload (a
    // JSON-encoded HTML string). Unwrap and walk its <style> blocks
    // too. parse_claude_bundle silently returns nullopt when the
    // envelope is missing — that's the expected branch for hand-rolled
    // fixtures, so we just fall through.
    if (auto bundle = parse_claude_bundle(html)) {
        for (auto& css : extract_html_style_blocks(bundle->template_html)) {
            collect_classnames_from_css(css, rules);
        }
    }

    return rules;
}

bool looks_like_bundler_entry(const std::string& html) {
    if (html.empty()) return false;

    auto contains = [&](const char* needle) {
        return html.find(needle) != std::string::npos;
    };

    // Standard mount points (React, Vue, Svelte, @pulp/react).
    const bool has_mount_root =
        contains("id=\"root\"")        || contains("id='root'") ||
        contains("id=\"app\"")         || contains("id='app'")  ||
        contains("id=\"__pulp_root\"") || contains("id='__pulp_root'");

    // Script tags that pull in a bundled JS entry. We don't try to
    // identify whether the script *is* a bundle — just that the page
    // is structured to load one.
    const bool has_script_src =
        contains("<script src=")                  ||
        contains("<script type=\"module\" src=")  ||
        contains("import(\"./")                   || contains("import('./");

    // Bundler-emitted markers (`__bundler_*`, "Unpacking..." status, the
    // @pulp/react runtime, React dev-tools hooks). These rarely show up
    // in hand-authored Claude Design HTML, so a single hit is enough.
    const bool has_bundler_hint =
        contains("__bundler")        || contains("Unpacking")     ||
        contains("data-reactroot")   || contains("@pulp/react");

    // Either (mount + script) — vanilla shell — or any unambiguous
    // bundler-specific marker.
    return (has_mount_root && has_script_src) || has_bundler_hint;
}

std::string serialize_claude_classnames(const ClaudeClassNameRules& rules) {
    // Use choc::value::createObject for stable, well-escaped JSON. The
    // outer map is a std::map so keys arrive in alphabetical order
    // already; per-class declaration maps are also std::map for the
    // same property-order guarantee. choc::json::toString preserves
    // insertion order, so the resulting JSON is deterministic.
    auto root = choc::value::createObject("");
    for (const auto& [name, decls] : rules) {
        auto obj = choc::value::createObject("");
        for (const auto& [prop, val] : decls) {
            obj.addMember(prop, val);
        }
        root.addMember(name, obj);
    }
    // Pretty-print with line breaks so the artifact is human-readable
    // and diff-friendly (matches Spectr's `JSON.stringify(_, null, 2)`
    // output shape for parity with the existing tooling).
    return choc::json::toString(root, /*useLineBreaks=*/true);
}

// Keyboard-shortcut extraction (extract_keyboard_shortcuts,
// serialize_detected_shortcuts, key_string_to_keycode,
// modifier_strings_to_mask) moved to design_import_shortcuts.cpp
// in the 2026-05 A3 refactor first cut. See planning/
// 2026-05-16-refactor-ops-high-leverage.md.



// ── Audio widget detection ──────────────────────────────────────────────

static std::string to_lower(const std::string& s) {
    std::string result = s;
    std::transform(result.begin(), result.end(), result.begin(),
        [](unsigned char c) { return std::tolower(c); });
    return result;
}

AudioWidgetType detect_audio_widget(const std::string& name) {
    auto lower = to_lower(name);
    if (lower.find("knob") != std::string::npos)      return AudioWidgetType::knob;
    if (lower.find("dial") != std::string::npos)       return AudioWidgetType::knob;
    if (lower.find("fader") != std::string::npos)      return AudioWidgetType::fader;
    if (lower.find("slider") != std::string::npos)     return AudioWidgetType::fader;
    if (lower.find("meter") != std::string::npos)      return AudioWidgetType::meter;
    if (lower.find("level") != std::string::npos)      return AudioWidgetType::meter;
    if (lower.find("vu") != std::string::npos)         return AudioWidgetType::meter;
    if (lower.find("xypad") != std::string::npos)      return AudioWidgetType::xy_pad;
    if (lower.find("xy_pad") != std::string::npos)     return AudioWidgetType::xy_pad;
    if (lower.find("xy pad") != std::string::npos)     return AudioWidgetType::xy_pad;
    if (lower.find("waveform") != std::string::npos)   return AudioWidgetType::waveform;
    if (lower.find("oscilloscope") != std::string::npos) return AudioWidgetType::waveform;
    if (lower.find("spectrum") != std::string::npos)   return AudioWidgetType::spectrum;
    if (lower.find("analyzer") != std::string::npos)   return AudioWidgetType::spectrum;
    if (lower.find("analyser") != std::string::npos)   return AudioWidgetType::spectrum;
    return AudioWidgetType::none;
}

// ── JSON parsing helpers ────────────────────────────────────────────────

static std::string get_string(const choc::value::ValueView& obj, const char* key, const char* def = "") {
    if (obj.hasObjectMember(key))
        return std::string(obj[key].toString());
    return def;
}

static float get_float(const choc::value::ValueView& obj, const char* key, float def = 0.0f) {
    if (obj.hasObjectMember(key))
        return static_cast<float>(obj[key].getWithDefault<double>(def));
    return def;
}

static bool get_bool(const choc::value::ValueView& obj, const char* key, bool def = false) {
    if (obj.hasObjectMember(key))
        return obj[key].getWithDefault<bool>(def);
    return def;
}

// ── IR from JSON ────────────────────────────────────────────────────────

static IRStyle parse_ir_style(const choc::value::ValueView& obj) {
    IRStyle s;
    if (!obj.isObject()) return s;

    auto set_opt_str = [&](const char* key, std::optional<std::string>& field) {
        if (obj.hasObjectMember(key)) field = std::string(obj[key].toString());
    };
    auto set_opt_float = [&](const char* key, std::optional<float>& field) {
        if (obj.hasObjectMember(key)) field = static_cast<float>(obj[key].getWithDefault<double>(0));
    };
    auto set_opt_int = [&](const char* key, std::optional<int>& field) {
        if (obj.hasObjectMember(key)) field = static_cast<int>(obj[key].getWithDefault<int64_t>(0));
    };

    set_opt_str("backgroundColor", s.background_color);
    set_opt_str("backgroundGradient", s.background_gradient);
    set_opt_str("color", s.color);
    set_opt_float("opacity", s.opacity);
    set_opt_float("borderRadius", s.border_radius);
    set_opt_str("border", s.border);
    set_opt_str("boxShadow", s.box_shadow);
    set_opt_str("filter", s.filter);
    set_opt_str("fontFamily", s.font_family);
    set_opt_float("fontSize", s.font_size);
    set_opt_int("fontWeight", s.font_weight);
    set_opt_str("fontStyle", s.font_style);
    set_opt_str("textAlign", s.text_align);
    set_opt_float("letterSpacing", s.letter_spacing);
    set_opt_float("lineHeight", s.line_height);
    set_opt_str("textTransform", s.text_transform);
    set_opt_str("overflow", s.overflow);
    set_opt_str("cursor", s.cursor);
    set_opt_str("position", s.position);
    set_opt_float("top", s.top);
    set_opt_float("left", s.left);
    set_opt_float("right", s.right);
    set_opt_float("bottom", s.bottom);
    set_opt_int("zIndex", s.z_index);
    set_opt_str("transform", s.transform);
    set_opt_float("width", s.width);
    set_opt_float("height", s.height);
    set_opt_float("minWidth", s.min_width);
    set_opt_float("minHeight", s.min_height);
    set_opt_float("maxWidth", s.max_width);
    set_opt_float("maxHeight", s.max_height);

    return s;
}

static IRLayout parse_ir_layout(const choc::value::ValueView& obj) {
    IRLayout l;
    if (!obj.isObject()) return l;

    auto dir = get_string(obj, "direction", "column");
    l.direction = (dir == "row") ? LayoutDirection::row : LayoutDirection::column;
    l.gap = get_float(obj, "gap");
    l.wrap = get_bool(obj, "wrap");

    // Padding — support uniform or per-side
    if (obj.hasObjectMember("padding")) {
        float p = get_float(obj, "padding");
        l.padding_top = l.padding_right = l.padding_bottom = l.padding_left = p;
    }
    if (obj.hasObjectMember("paddingTop"))    l.padding_top = get_float(obj, "paddingTop");
    if (obj.hasObjectMember("paddingRight"))  l.padding_right = get_float(obj, "paddingRight");
    if (obj.hasObjectMember("paddingBottom")) l.padding_bottom = get_float(obj, "paddingBottom");
    if (obj.hasObjectMember("paddingLeft"))   l.padding_left = get_float(obj, "paddingLeft");

    auto parse_align = [](const std::string& s) -> LayoutAlign {
        if (s == "center")        return LayoutAlign::center;
        if (s == "flex-end" || s == "end") return LayoutAlign::flex_end;
        if (s == "stretch")       return LayoutAlign::stretch;
        if (s == "space-between") return LayoutAlign::space_between;
        if (s == "space-around")  return LayoutAlign::space_around;
        return LayoutAlign::flex_start;
    };

    if (obj.hasObjectMember("justify"))
        l.justify = parse_align(get_string(obj, "justify"));
    if (obj.hasObjectMember("align"))
        l.align = parse_align(get_string(obj, "align"));

    auto parse_sizing = [](const std::string& s) -> SizingMode {
        if (s == "hug" || s == "auto") return SizingMode::hug;
        if (s == "fill")               return SizingMode::fill;
        return SizingMode::fixed;
    };

    if (obj.hasObjectMember("widthMode"))
        l.width_mode = parse_sizing(get_string(obj, "widthMode"));
    if (obj.hasObjectMember("heightMode"))
        l.height_mode = parse_sizing(get_string(obj, "heightMode"));

    return l;
}

static IRNode parse_ir_node(const choc::value::ValueView& obj) {
    IRNode node;
    node.type = get_string(obj, "type", "frame");
    node.name = get_string(obj, "name");
    node.text_content = get_string(obj, "content");

    // Phase 0a (planning/2026-05-18-inspector-direct-manipulation-roadmap.md):
    // capture the source-native ID so the `adapter` anchor strategy can use
    // it as its anchor. Figma + Pencil + Mitosis-style exports all carry an
    // ID under one of these field names; first non-empty wins. Sources
    // without native IDs (Stitch HTML, v0 TSX, Claude HTML) leave this
    // empty and fall through to the content-hash strategy.
    for (const char* k : {"id", "nodeId", "node_id", "source_node_id"}) {
        if (!obj.hasObjectMember(k)) continue;
        auto v = obj[k];
        if (!v.isString()) continue;
        auto s = std::string(v.toString());
        if (s.empty()) continue;
        node.source_node_id = std::move(s);
        break;
    }

    if (obj.hasObjectMember("style"))
        node.style = parse_ir_style(obj["style"]);
    if (obj.hasObjectMember("layout"))
        node.layout = parse_ir_layout(obj["layout"]);

    // Exact layout dimensions from snapshot_layout (injected by import skill)
    if (obj.hasObjectMember("_layoutHeight"))
        node.attributes["_layoutHeight"] = std::to_string(static_cast<int>(get_float(obj, "_layoutHeight", 0)));
    if (obj.hasObjectMember("_layoutWidth"))
        node.attributes["_layoutWidth"] = std::to_string(static_cast<int>(get_float(obj, "_layoutWidth", 0)));

    // Audio widget detection is deferred until after children are parsed
    // (see below) — containers with child frames shouldn't be widgets

    if (obj.hasObjectMember("label"))
        node.audio_label = get_string(obj, "label");
    if (obj.hasObjectMember("min"))
        node.audio_min = get_float(obj, "min", 0.0f);
    if (obj.hasObjectMember("max"))
        node.audio_max = get_float(obj, "max", 1.0f);
    if (obj.hasObjectMember("default"))
        node.audio_default = get_float(obj, "default", 0.5f);

    // Top-level properties (Pencil/Figma format puts these at node level, not in "style")
    // Override style values if they weren't set from the "style" sub-object
    // Handle Pencil sizing modes: fill_container → flex_grow, fit_content → auto
    if (obj.hasObjectMember("width")) {
        auto w_str = get_string(obj, "width", "");
        if (w_str == "fill_container") {
            node.layout.width_mode = SizingMode::fill;
        } else if (w_str == "fit_content" || w_str.find("fit_content") == 0) {
            node.layout.width_mode = SizingMode::hug;
        } else if (!node.style.width) {
            float w = get_float(obj, "width", 0);
            if (w > 0) node.style.width = w;
        }
    }
    if (obj.hasObjectMember("height")) {
        auto h_str = get_string(obj, "height", "");
        if (h_str == "fill_container") {
            node.layout.height_mode = SizingMode::fill;
        } else if (h_str == "fit_content" || h_str.find("fit_content") == 0) {
            node.layout.height_mode = SizingMode::hug;
        } else if (!node.style.height) {
            float h = get_float(obj, "height", 0);
            if (h > 0) node.style.height = h;
        }
    }
    // Top-level fill → backgroundColor
    if (!node.style.background_color && obj.hasObjectMember("fill")) {
        auto fill_val = obj["fill"];
        if (fill_val.isString()) {
            auto fill_str = std::string(fill_val.toString());
            if (!fill_str.empty() && fill_str[0] == '#')
                node.style.background_color = fill_str;
        }
    }
    // Top-level cornerRadius
    if (!node.style.border_radius && obj.hasObjectMember("cornerRadius")) {
        node.style.border_radius = get_float(obj, "cornerRadius", 0);
    }
    // Top-level fontSize, fontWeight, fontFamily for text nodes
    if (node.type == "text" || node.type == "label") {
        if (!node.style.font_size && obj.hasObjectMember("fontSize"))
            node.style.font_size = get_float(obj, "fontSize", 14);
        if (!node.style.font_weight && obj.hasObjectMember("fontWeight")) {
            auto fw = get_string(obj, "fontWeight", "");
            if (!fw.empty()) {
                try { node.style.font_weight = std::stoi(fw); }
                catch (...) { if (fw == "bold") node.style.font_weight = 700; }
            }
        }
        if (!node.style.color && obj.hasObjectMember("fill")) {
            auto fill_val = obj["fill"];
            if (fill_val.isString()) node.style.color = std::string(fill_val.toString());
        }
        if (!node.style.font_family && obj.hasObjectMember("fontFamily"))
            node.style.font_family = get_string(obj, "fontFamily", "");
        // Top-level content → text_content
        if (node.text_content.empty() && obj.hasObjectMember("content"))
            node.text_content = get_string(obj, "content", "");
    }
    // Top-level layout properties (Pencil uses "layout": "vertical"/"horizontal")
    // Pencil frames default to horizontal when no layout is specified
    if (obj.hasObjectMember("layout")) {
        auto layout_val = obj["layout"];
        if (layout_val.isString()) {
            auto dir = std::string(layout_val.toString());
            if (dir == "horizontal") node.layout.direction = LayoutDirection::row;
            else if (dir == "vertical") node.layout.direction = LayoutDirection::column;
            else if (dir == "none") {} // absolute positioning, keep default
        } else if (layout_val.isObject()) {
            // IR format: layout is an object with "direction" key
            // Already parsed above via parse_ir_layout
        }
    } else if (node.type == "frame" &&
               obj.hasObjectMember("children") && obj["children"].isArray() && obj["children"].size() > 0) {
        // Pencil frames default to horizontal (row) when layout is not specified
        node.layout.direction = LayoutDirection::row;
    }
    if (obj.hasObjectMember("gap"))
        node.layout.gap = get_float(obj, "gap", 0);
    if (obj.hasObjectMember("justifyContent")) {
        auto j = get_string(obj, "justifyContent", "");
        if (j == "center") node.layout.justify = LayoutAlign::center;
        else if (j == "space_between" || j == "space-between") node.layout.justify = LayoutAlign::space_between;
        else if (j == "space_around" || j == "space-around") node.layout.justify = LayoutAlign::space_around;
        else if (j == "end" || j == "flex-end") node.layout.justify = LayoutAlign::flex_end;
    }
    if (obj.hasObjectMember("alignItems")) {
        auto a = get_string(obj, "alignItems", "");
        if (a == "center") node.layout.align = LayoutAlign::center;
        else if (a == "end" || a == "flex-end") node.layout.align = LayoutAlign::flex_end;
    }
    // Top-level padding (Pencil uses array: [top, right] or [top, right, bottom, left])
    if (obj.hasObjectMember("padding")) {
        auto pad = obj["padding"];
        if (pad.isArray()) {
            if (pad.size() == 2) {
                float tb = static_cast<float>(pad[0].getWithDefault<double>(0));
                float lr = static_cast<float>(pad[1].getWithDefault<double>(0));
                node.layout.padding_top = node.layout.padding_bottom = tb;
                node.layout.padding_left = node.layout.padding_right = lr;
            } else if (pad.size() == 4) {
                node.layout.padding_top = static_cast<float>(pad[0].getWithDefault<double>(0));
                node.layout.padding_right = static_cast<float>(pad[1].getWithDefault<double>(0));
                node.layout.padding_bottom = static_cast<float>(pad[2].getWithDefault<double>(0));
                node.layout.padding_left = static_cast<float>(pad[3].getWithDefault<double>(0));
            }
        } else {
            float p = get_float(obj, "padding", 0);
            node.layout.padding_top = node.layout.padding_right =
                node.layout.padding_bottom = node.layout.padding_left = p;
        }
    }

    // Children
    if (obj.hasObjectMember("children") && obj["children"].isArray()) {
        auto children = obj["children"];
        for (uint32_t i = 0; i < children.size(); ++i)
            node.children.push_back(parse_ir_node(children[static_cast<int>(i)]));
    }

    // Audio widget detection (deferred until after children are parsed)
    // Rule: a node is an audio widget ONLY if:
    //   1. Its name matches an audio widget pattern (knob, fader, meter, etc.)
    //   2. AND it doesn't have child frames/groups (containers aren't widgets)
    //   A node with only shape children (ellipse/rectangle) + text is a widget.
    //   A node with child frames (like "KnobRow" containing 4 knob frames) is a container.
    {
        auto detected = detect_audio_widget(node.name);
        if (detected == AudioWidgetType::none && !node.type.empty())
            detected = detect_audio_widget(node.type);

        if (detected != AudioWidgetType::none) {
            // Check if this node has child frames — if so, it's a container, not a widget
            bool has_child_containers = false;
            for (auto& child : node.children) {
                if (child.type == "frame" || child.type == "group" ||
                    !child.children.empty()) {
                    has_child_containers = true;
                    break;
                }
            }
            // Only assign widget type if it's a leaf or has only shapes+text
            if (!has_child_containers)
                node.audio_widget = detected;
        }
    }

    // Parse stroke color from shapes (Pencil puts stroke on ellipse/rectangle nodes)
    if (obj.hasObjectMember("stroke")) {
        auto stroke = obj["stroke"];
        if (stroke.isObject() && stroke.hasObjectMember("fill")) {
            auto fill = stroke["fill"];
            if (fill.isString()) {
                auto fill_str = std::string(fill.toString());
                if (!fill_str.empty() && fill_str[0] == '#')
                    node.attributes["stroke_color"] = fill_str;
            }
        }
    }

    // For audio widgets: extract child shape dimensions into attributes
    // The frame's own width/height is for the column container.
    // The child shape's width/height is for the widget itself.
    if (node.audio_widget != AudioWidgetType::none && !node.children.empty()) {
        for (auto& child : node.children) {
            if (child.type == "ellipse" || child.type == "rectangle") {
                if (child.style.width)
                    node.attributes["shape_width"] = std::to_string(static_cast<int>(*child.style.width));
                if (child.style.height)
                    node.attributes["shape_height"] = std::to_string(static_cast<int>(*child.style.height));
                break;
            }
        }
    }

    return node;
}

static IRTokens parse_ir_tokens(const choc::value::ValueView& obj) {
    IRTokens tokens;
    if (!obj.isObject()) return tokens;

    if (obj.hasObjectMember("colors")) {
        auto c = obj["colors"];
        for (uint32_t i = 0; i < c.size(); ++i) {
            auto m = c.getObjectMemberAt(i);
            tokens.colors[std::string(m.name)] = std::string(m.value.toString());
        }
    }
    if (obj.hasObjectMember("dimensions")) {
        auto d = obj["dimensions"];
        for (uint32_t i = 0; i < d.size(); ++i) {
            auto m = d.getObjectMemberAt(i);
            tokens.dimensions[std::string(m.name)] = static_cast<float>(m.value.getWithDefault<double>(0));
        }
    }
    if (obj.hasObjectMember("strings")) {
        auto s = obj["strings"];
        for (uint32_t i = 0; i < s.size(); ++i) {
            auto m = s.getObjectMemberAt(i);
            tokens.strings[std::string(m.name)] = std::string(m.value.toString());
        }
    }
    return tokens;
}

// ── Source adapters ─────────────────────────────────────────────────────

DesignIR parse_figma_json(const std::string& json) {
    DesignIR ir;
    ir.source = DesignSource::figma;

    auto root = choc::json::parse(json);

    // Figma export can be the IR format directly (from MCP or export tool)
    ir.root = parse_ir_node(root);

    if (root.hasObjectMember("tokens"))
        ir.tokens = parse_ir_tokens(root["tokens"]);

    // Phase 0a: tag the root with adapter provenance + confidence, then
    // assign stable_anchor_id values via the adapter strategy. Figma
    // exports carry native layer UUIDs that parse_ir_node populates into
    // source_node_id; the adapter strategy prefixes those with "figma:".
    // Nodes that don't have a native ID (rare in Figma) silently fall
    // through to the content-hash branch inside compute_anchor_id.
    ir.root.provenance = IRProvenance{"figma", "1", /*source_uri=*/{}};
    ir.root.confidence = IRConfidence::pass;
    assign_anchors(ir.root, AnchorStrategy::adapter, "figma");

    return ir;
}

DesignIR parse_stitch_html(const std::string& html) {
    DesignIR ir;
    ir.source = DesignSource::stitch;

    // Try parsing as JSON IR first (from Stitch MCP get_screen)
    try {
        auto root = choc::json::parse(html);
        ir.root = parse_ir_node(root);
        if (root.hasObjectMember("tokens"))
            ir.tokens = parse_ir_tokens(root["tokens"]);
        ir.root.provenance = IRProvenance{"stitch-html", "1", {}};
        ir.root.confidence = IRConfidence::pass;
        assign_anchors(ir.root, AnchorStrategy::content_hash);
        return ir;
    } catch (...) {
        // Not JSON — minimal HTML extraction
    }

    // Basic HTML → IR: extract inline styles from HTML elements
    // For full HTML parsing, the AI pipeline handles translation
    ir.root.type = "frame";
    ir.root.name = "StitchImport";
    ir.root.layout.direction = LayoutDirection::column;

    // Extract text content between tags as a simple fallback
    std::regex tag_re("<([a-z][a-z0-9]*)[^>]*>([^<]*)</\\1>");
    auto begin = std::sregex_iterator(html.begin(), html.end(), tag_re);
    auto end = std::sregex_iterator();

    for (auto it = begin; it != end; ++it) {
        std::string tag = (*it)[1].str();
        std::string content = (*it)[2].str();
        if (content.empty()) continue;

        IRNode child;
        child.type = "text";
        child.name = tag;
        child.text_content = content;
        ir.root.children.push_back(std::move(child));
    }

    // Phase 0a: assign anchors to the regex-extracted tree.
    ir.root.provenance = IRProvenance{"stitch-html", "1", {}};
    ir.root.confidence = IRConfidence::diverge;  // regex fallback is lossy
    assign_anchors(ir.root, AnchorStrategy::content_hash);
    return ir;
}

DesignIR parse_v0_tsx(const std::string& tsx) {
    DesignIR ir;
    ir.source = DesignSource::v0;

    // Try parsing as JSON IR first (pre-processed by AI pipeline)
    try {
        auto root = choc::json::parse(tsx);
        ir.root = parse_ir_node(root);
        if (root.hasObjectMember("tokens"))
            ir.tokens = parse_ir_tokens(root["tokens"]);
        ir.root.provenance = IRProvenance{"v0-tsx", "1", {}};
        ir.root.confidence = IRConfidence::pass;
        assign_anchors(ir.root, AnchorStrategy::content_hash);
        return ir;
    } catch (...) {
        // Not JSON — TSX source
    }

    // TSX → IR: extract JSX elements and Tailwind classes
    // Full translation requires AI; this provides structural extraction
    ir.root.type = "frame";
    ir.root.name = "V0Import";
    ir.root.layout.direction = LayoutDirection::column;

    // Extract className strings for Tailwind analysis
    std::regex class_re("className=\"([^\"]+)\"");
    auto begin = std::sregex_iterator(tsx.begin(), tsx.end(), class_re);
    auto end = std::sregex_iterator();

    for (auto it = begin; it != end; ++it) {
        std::string classes = (*it)[1].str();
        IRNode child;
        child.type = "frame";
        child.name = classes;  // Store Tailwind classes as name for AI processing
        // Parse common Tailwind patterns
        if (classes.find("flex-row") != std::string::npos)
            child.layout.direction = LayoutDirection::row;
        if (classes.find("flex-col") != std::string::npos)
            child.layout.direction = LayoutDirection::column;
        ir.root.children.push_back(std::move(child));
    }

    // Phase 0a: anchor the regex-extracted Tailwind tree.
    ir.root.provenance = IRProvenance{"v0-tsx", "1", {}};
    ir.root.confidence = IRConfidence::diverge;  // regex extraction is lossy
    assign_anchors(ir.root, AnchorStrategy::content_hash);
    return ir;
}

DesignIR parse_pencil_json(const std::string& json) {
    DesignIR ir;
    ir.source = DesignSource::pencil;

    auto root = choc::json::parse(json);

    // Pencil MCP batch_get returns a node tree similar to our IR
    ir.root = parse_ir_node(root);

    if (root.hasObjectMember("tokens"))
        ir.tokens = parse_ir_tokens(root["tokens"]);
    if (root.hasObjectMember("variables"))
        ir.tokens = parse_ir_tokens(root["variables"]);

    // Phase 0a: Pencil MCP nodes carry a stable `id` that parse_ir_node
    // populates into source_node_id; the adapter strategy uses it directly.
    ir.root.provenance = IRProvenance{"pencil", "1", {}};
    ir.root.confidence = IRConfidence::pass;
    assign_anchors(ir.root, AnchorStrategy::adapter, "pencil");

    return ir;
}


} // namespace pulp::view
