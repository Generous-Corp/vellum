// claude_bundle_sources.cpp — per-design-tool source detection +
// runtime-JS builders for the runtime-import subsystem (pulp #468).
//
// Extracted from claude_bundle.cpp so that source-detection work
// (adding/refining a design-tool importer: v0.dev, Figma Make, Google
// Stitch, React Native, Pencil) no longer recompiles the whole 2.4k-line
// Claude-bundle harness.
//
// This TU owns:
//   * the shared `v0_*` foundation helpers (trim/lower/regex match,
//     supported-surface gating, runtime-JS template builder) that every
//     design-tool importer reuses;
//   * the per-tool `figma_*`, `stitch_*`, `rn_*`, and `pencil_*` families
//     that layer source-signal detection, reject-marker policy, and
//     runtime-JS string rewrites on top of the v0 foundation;
//   * the public `parse_v0_dev_react`, `parse_figma_make_react`,
//     `parse_stitch_react`, `parse_react_native_export`, and
//     `parse_pencil_react` entry points (declared in design_import.hpp).
//
// The cross-TU interface is deliberately tiny: the public parse_* entry
// points (public header) plus `json_string_literal` — a shared JS-string
// escaper defined in claude_bundle.cpp and declared in
// design_import_internal.hpp. `set_runtime_error` and `parse_jsx_react`
// stay in claude_bundle.cpp; they consume the runtime harness, not this
// source-detection cluster.
//
// Definitions only; the public declarations stay in
// pulp/view/design_import.hpp.

#include <pulp/view/design_import.hpp>
#include <algorithm>
#include <cctype>
#include <optional>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

#include "design_import_internal.hpp"

namespace pulp::view {

namespace {

struct V0ReactSource {
    std::string source;
    std::string file_name;
};

std::string v0_trim(std::string s) {
    auto is_ws = [](unsigned char c) { return std::isspace(c) != 0; };
    while (!s.empty() && is_ws(static_cast<unsigned char>(s.front()))) {
        s.erase(s.begin());
    }
    while (!s.empty() && is_ws(static_cast<unsigned char>(s.back()))) {
        s.pop_back();
    }
    return s;
}

std::string v0_lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

bool v0_contains_ci(const std::string& haystack, const std::string& needle) {
    auto h = v0_lower(haystack);
    auto n = v0_lower(needle);
    return h.find(n) != std::string::npos;
}

bool v0_ends_with_ci(const std::string& s, const std::string& suffix) {
    auto ls = v0_lower(s);
    auto lf = v0_lower(suffix);
    return ls.size() >= lf.size() &&
           ls.compare(ls.size() - lf.size(), lf.size(), lf) == 0;
}

bool v0_is_react_file(const std::string& path) {
    return v0_ends_with_ci(path, ".tsx") || v0_ends_with_ci(path, ".jsx") ||
           v0_ends_with_ci(path, ".ts") || v0_ends_with_ci(path, ".js");
}

std::string v0_extract_file_attr(const std::string& header) {
    static const std::regex file_re(R"RX(\bfile\s*=\s*"([^"]+)")RX");
    std::smatch m;
    if (std::regex_search(header, m, file_re)) return m[1].str();
    return {};
}

std::optional<V0ReactSource> v0_extract_source(const std::string& input) {
    if (input.find("[V0_FILE]") == std::string::npos) {
        auto src = v0_trim(input);
        if (src.empty()) return std::nullopt;
        return V0ReactSource{std::move(src), "component.tsx"};
    }

    std::vector<V0ReactSource> candidates;
    size_t marker = 0;
    while ((marker = input.find("[V0_FILE]", marker)) != std::string::npos) {
        auto header_end = input.find('\n', marker);
        auto header = input.substr(marker, header_end == std::string::npos
            ? std::string::npos
            : header_end - marker);
        auto content_begin = header_end == std::string::npos ? input.size() : header_end + 1;
        auto next = input.find("\n[V0_FILE]", content_begin);
        auto content_end = next == std::string::npos ? input.size() : next;

        auto file_name = v0_extract_file_attr(header);
        if (v0_is_react_file(file_name)) {
            auto body = v0_trim(input.substr(content_begin, content_end - content_begin));
            if (!body.empty()) {
                candidates.push_back({std::move(body), std::move(file_name)});
            }
        }

        marker = next == std::string::npos ? input.size() : next + 1;
    }

    if (candidates.empty()) return std::nullopt;

    auto score = [](const V0ReactSource& c) {
        auto name = v0_lower(c.file_name);
        int s = 0;
        if (name == "app/page.tsx" || name == "src/app/page.tsx") s += 100;
        if (name.find("/page.tsx") != std::string::npos) s += 80;
        if (v0_ends_with_ci(name, ".tsx")) s += 20;
        if (c.source.find("export default") != std::string::npos) s += 10;
        return s;
    };

    return *std::max_element(candidates.begin(), candidates.end(),
        [&](const auto& a, const auto& b) { return score(a) < score(b); });
}

bool v0_import_statement_is_supported(const std::string& statement) {
    static const std::regex from_re(R"RX(\bfrom\s*["']([^"']+)["'])RX");
    static const std::regex side_effect_re(R"RX(^\s*import\s*["'][^"']+["'])RX");
    if (std::regex_search(statement, side_effect_re)) return false;

    std::smatch m;
    if (!std::regex_search(statement, m, from_re)) return false;
    return m[1].str() == "react";
}

bool v0_imports_are_supported(const std::string& source) {
    std::istringstream lines(source);
    std::string line;
    std::string statement;
    bool in_import = false;

    auto starts_import = [](const std::string& t) {
        if (t.rfind("import", 0) != 0) return false;
        return t.size() == 6 ||
               (!std::isalnum(static_cast<unsigned char>(t[6])) && t[6] != '_');
    };
    static const std::regex from_re(R"RX(\bfrom\s*["'][^"']+["'])RX");
    static const std::regex side_effect_re(R"RX(^\s*import\s*["'][^"']+["'])RX");

    while (std::getline(lines, line)) {
        auto t = v0_trim(line);
        if (!in_import) {
            if (!starts_import(t)) continue;
            statement = t;
        } else {
            statement += ' ';
            statement += t;
        }

        if (std::regex_search(statement, from_re) ||
            std::regex_search(statement, side_effect_re) ||
            statement.find(';') != std::string::npos) {
            if (!v0_import_statement_is_supported(statement)) return false;
            statement.clear();
            in_import = false;
        } else {
            in_import = true;
        }
    }

    return !in_import;
}

bool v0_uses_only_supported_surfaces_with_tailwind_policy(const std::string& source,
                                                          bool reject_tailwind_marker) {
    if (!v0_imports_are_supported(source)) return false;

    const auto lower = v0_lower(source);
    const char* unsupported_markers[] = {
        "classname", "@/components", "@radix-ui", "radix-ui", "shadcn",
        "next/", "next\\", "next/dynamic", "lucide-react", "framer-motion",
        "clsx(", "cva(", "cn(", "fetch(", "xmlhttprequest",
        "localstorage", "sessionstorage", "indexeddb", "websocket",
        "serviceworker", "sharedworker", "new worker", "broadcastchannel",
        "settimeout(", "setinterval(", "document.", "window.", "navigator.",
        "history.", "location.", "dangerouslysetinnerhtml",
        "<form", "<select", "<textarea", "<iframe"
    };
    if (reject_tailwind_marker && lower.find("tailwind") != std::string::npos) {
        return false;
    }
    for (const char* marker : unsupported_markers) {
        if (lower.find(marker) != std::string::npos) return false;
    }

    static const std::regex dynamic_import_re(R"RX(\bimport\s*\()RX");
    if (std::regex_search(source, dynamic_import_re)) return false;

    static const std::regex input_tag_re(R"RX(<input\b)RX", std::regex::icase);
    static const std::regex input_type_re(
        R"RX(<input\b[^>]*\btype\s*=\s*(?:"([^"]+)"|'([^']+)'))RX",
        std::regex::icase);
    const auto input_tag_count = static_cast<size_t>(std::distance(
        std::sregex_iterator(source.begin(), source.end(), input_tag_re),
        std::sregex_iterator()));
    size_t typed_input_count = 0;
    auto input_begin = std::sregex_iterator(source.begin(), source.end(), input_type_re);
    auto input_end = std::sregex_iterator();
    for (auto it = input_begin; it != input_end; ++it) {
        ++typed_input_count;
        std::string type = (*it)[1].matched ? (*it)[1].str() : (*it)[2].str();
        if (v0_lower(type) != "range") return false;
    }
    if (typed_input_count != input_tag_count) return false;

    static const std::regex tag_re(R"RX(</?([A-Za-z][A-Za-z0-9]*)(?=[\s>/]))RX");
    static const std::unordered_set<std::string> supported = {
        "div", "span", "button", "canvas", "svg", "path", "rect", "circle",
        "image", "input", "img", "p", "h1", "h2", "h3", "h4", "h5", "h6"
    };
    static const std::unordered_set<std::string> ts_type_names = {
        "HTMLCanvasElement", "HTMLDivElement", "HTMLInputElement", "HTMLElement",
        "SVGElement", "ReactElement"
    };

    auto tag_begin = std::sregex_iterator(source.begin(), source.end(), tag_re);
    auto tag_end = std::sregex_iterator();
    for (auto it = tag_begin; it != tag_end; ++it) {
        auto tag = (*it)[1].str();
        if (!tag.empty() && std::isupper(static_cast<unsigned char>(tag[0]))) {
            if (ts_type_names.count(tag) != 0) continue;
            return false;
        }
        if (supported.count(v0_lower(tag)) == 0) return false;
    }

    return true;
}

bool v0_uses_only_supported_surfaces(const std::string& source) {
    return v0_uses_only_supported_surfaces_with_tailwind_policy(source, true);
}

std::optional<std::string> v0_match_first(const std::string& source, const std::regex& re) {
    std::smatch m;
    if (!std::regex_search(source, m, re)) return std::nullopt;
    for (size_t i = 1; i < m.size(); ++i) {
        if (m[i].matched) return v0_trim(m[i].str());
    }
    return std::nullopt;
}

std::string v0_extract_component_name(const std::string& source) {
    static const std::regex export_fn_re(
        R"RX(export\s+default\s+function\s+([A-Za-z_][A-Za-z0-9_]*))RX");
    if (auto m = v0_match_first(source, export_fn_re)) return *m;
    return "V0RuntimeImport";
}

std::string v0_slug_from_component(std::string name) {
    std::string out = "v0";
    for (char c : name) {
        if (std::isupper(static_cast<unsigned char>(c)) && !out.empty() && out.back() != '-') {
            out += '-';
        }
        if (std::isalnum(static_cast<unsigned char>(c))) {
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (c == '-' || c == '_') {
            if (!out.empty() && out.back() != '-') out += '-';
        }
    }
    while (!out.empty() && out.back() == '-') out.pop_back();
    return out.empty() ? "v0-runtime-import" : out;
}

std::string v0_extract_root_id(const std::string& source, const std::string& component_name) {
    static const std::regex id_re(R"RX(\bid\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, id_re)) return *m;
    return v0_slug_from_component(component_name);
}

size_t v0_count_tag(const std::string& source, const char* tag) {
    std::regex re(std::string(R"RX(<\s*)RX") + tag + R"RX(\b)RX", std::regex::icase);
    return static_cast<size_t>(std::distance(
        std::sregex_iterator(source.begin(), source.end(), re),
        std::sregex_iterator()));
}

void v0_push_unique(std::vector<std::string>& values, std::string value) {
    value = v0_trim(std::move(value));
    if (value.empty()) return;
    if (value.size() > 80) return;
    if (value[0] == '#') return;
    if (std::find(values.begin(), values.end(), value) == values.end()) {
        values.push_back(std::move(value));
    }
}

std::vector<std::string> v0_extract_texts(const std::string& source, const char* tag) {
    std::vector<std::string> out;
    std::regex re(std::string(R"RX(<\s*)RX") + tag +
        R"RX(\b[^>]*>\s*([^<>{}]+?)\s*</\s*)RX" + tag + R"RX(\s*>)RX",
        std::regex::icase);
    auto begin = std::sregex_iterator(source.begin(), source.end(), re);
    auto end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) {
        v0_push_unique(out, (*it)[1].str());
    }
    return out;
}

std::string v0_json_array(const std::vector<std::string>& values) {
    std::string out = "[";
    for (size_t i = 0; i < values.size(); ++i) {
        if (i != 0) out += ",";
        out += json_string_literal(values[i]);
    }
    out += "]";
    return out;
}

std::string replace_all_copy(std::string s,
                             const std::string& needle,
                             const std::string& replacement) {
    if (needle.empty()) return s;
    size_t pos = 0;
    while ((pos = s.find(needle, pos)) != std::string::npos) {
        s.replace(pos, needle.size(), replacement);
        pos += replacement.size();
    }
    return s;
}

std::string v0_build_runtime_js(const std::string& source,
                                const std::string& file_name,
                                const std::string& component_name,
                                const std::string& root_id) {
    auto headings = v0_extract_texts(source, "h[1-6]");
    auto paragraphs = v0_extract_texts(source, "p");
    auto spans = v0_extract_texts(source, "span");
    auto buttons = v0_extract_texts(source, "button");

    const size_t canvas_count = v0_count_tag(source, "canvas");
    const size_t slider_count = v0_count_tag(source, "input");
    size_t button_count = v0_count_tag(source, "button");

    if (spans.empty()) spans = {"Control"};
    while (spans.size() < slider_count) {
        spans.push_back("Control " + std::to_string(spans.size() + 1));
    }
    if (buttons.empty() && button_count > 0) {
        buttons = {"Armed", "Bypass", "Play", "Stop"};
    }

    const auto title = headings.empty() ? component_name : headings.front();
    const auto subtitle = paragraphs.empty()
        ? std::string("v0.dev React runtime import")
        : paragraphs.front();

    std::ostringstream js;
    js << "(function(){\n"
       << "  var React = globalThis.React;\n"
       << "  var ReactDOM = globalThis.ReactDOM;\n"
       << "  if (!React || !ReactDOM || typeof ReactDOM.createRoot !== 'function') {\n"
       << "    throw new Error('v0.dev runtime import requires host React and ReactDOM');\n"
       << "  }\n"
       << "  var h = React.createElement;\n"
       << "  var rootId = " << json_string_literal(root_id) << ";\n"
       << "  var title = " << json_string_literal(title) << ";\n"
       << "  var subtitle = " << json_string_literal(subtitle) << ";\n"
       << "  var sourceFile = " << json_string_literal(file_name) << ";\n"
       << "  var sliderLabels = " << v0_json_array(spans) << ";\n"
       << "  var buttonLabels = " << v0_json_array(buttons) << ";\n"
       << "  var hasCanvas = " << (canvas_count > 0 ? "true" : "false") << ";\n"
       << "  var sliderCount = " << slider_count << ";\n"
       << "  var buttonCount = " << button_count << ";\n"
       << "  function App(){\n"
       << "    var canvasRef = React.useRef(null);\n"
       << "    var levelState = React.useState(0.55);\n"
       << "    var level = levelState[0];\n"
       << "    var setLevel = levelState[1];\n"
       << "    var enabledState = React.useState(true);\n"
       << "    var enabled = enabledState[0];\n"
       << "    var setEnabled = enabledState[1];\n"
       << "    React.useEffect(function(){\n"
       << "      if (!hasCanvas) return function(){};\n"
       << "      var active = true;\n"
       << "      function draw(){\n"
       << "        var canvas = canvasRef.current;\n"
       << "        if (canvas && typeof canvas.getContext === 'function') {\n"
       << "          var ctx = canvas.getContext('2d');\n"
       << "          if (ctx) {\n"
       << "            var width = canvas.width || 384;\n"
       << "            var height = canvas.height || 112;\n"
       << "            var t = ((typeof performance !== 'undefined' && performance.now) ? performance.now() : 0) / 1000;\n"
       << "            var peak = enabled ? Math.max(0, Math.min(1, 0.35 + level * 0.45 + Math.sin(t * 2.4) * 0.12)) : 0.12;\n"
       << "            var bars = 22;\n"
       << "            var gap = 3;\n"
       << "            var barWidth = (width - gap * (bars - 1)) / bars;\n"
       << "            ctx.clearRect(0, 0, width, height);\n"
       << "            ctx.fillStyle = '#111827';\n"
       << "            ctx.fillRect(0, 0, width, height);\n"
       << "            for (var i = 0; i < bars; i++) {\n"
       << "              var ratio = (i + 1) / bars;\n"
       << "              var barHeight = Math.max(6, height * ratio * 0.86);\n"
       << "              ctx.fillStyle = ratio <= peak ? (ratio > 0.82 ? '#f97316' : '#22c55e') : '#263244';\n"
       << "              ctx.fillRect(i * (barWidth + gap), height - barHeight, barWidth, barHeight);\n"
       << "            }\n"
       << "            ctx.fillStyle = '#d1d5db';\n"
       << "            ctx.font = '12px Inter, sans-serif';\n"
       << "            ctx.fillText(sourceFile.indexOf('/') >= 0 ? 'v0' : 'OUT', 8, 18);\n"
       << "          }\n"
       << "        }\n"
       << "        if (active && typeof requestAnimationFrame === 'function') requestAnimationFrame(draw);\n"
       << "      }\n"
       << "      draw();\n"
       << "      return function(){ active = false; };\n"
       << "    }, [enabled, level]);\n"
       << "    var panelStyle = { width: 420, minHeight: 280, display: 'flex', flexDirection: 'column', gap: 16, padding: 18, backgroundColor: '#0b1020', color: '#f9fafb', border: '1px solid #334155', borderRadius: 8, fontFamily: 'Inter, system-ui, sans-serif' };\n"
       << "    var rowStyle = { display: 'flex', flexDirection: 'row', gap: 12, alignItems: 'center' };\n"
       << "    var controlStyle = { display: 'flex', flexDirection: 'column', gap: 6, flexGrow: 1 };\n"
       << "    var buttonStyle = { minWidth: 88, minHeight: 36, borderRadius: 6, border: '1px solid #475569', backgroundColor: enabled ? '#14532d' : '#1f2937', color: '#f8fafc', cursor: 'pointer' };\n"
       << "    var buttonChildren = [];\n"
       << "    for (var b = 0; b < buttonCount; b++) {\n"
       << "      buttonChildren.push(h('button', { key: 'button-' + b, type: 'button', onClick: function(){ setEnabled(!enabled); }, style: buttonStyle }, buttonLabels[b] || (enabled ? 'Armed' : 'Bypass')));\n"
       << "    }\n"
       << "    var sliderChildren = [];\n"
       << "    for (var s = 0; s < sliderCount; s++) {\n"
       << "      sliderChildren.push(h('div', { key: 'slider-' + s, style: controlStyle },\n"
       << "        h('span', { style: { fontSize: 12, color: '#cbd5e1' } }, sliderLabels[s] || ('Control ' + (s + 1))),\n"
       << "        h('input', { type: 'range', min: 0, max: 1, step: 0.01, value: level, onChange: function(event){ setLevel(Number(event.currentTarget.value)); } }),\n"
       << "        h('span', { style: { fontSize: 12, color: '#94a3b8' } }, String(Math.round(level * 100)) + '%')));\n"
       << "    }\n"
       << "    var children = [\n"
       << "      h('div', { key: 'header', style: Object.assign({}, rowStyle, { justifyContent: 'space-between' }) },\n"
       << "        h('div', null,\n"
       << "          h('h2', { style: { margin: 0, fontSize: 22, lineHeight: 1.1 } }, title),\n"
       << "          h('p', { style: { margin: '6px 0 0', color: '#94a3b8', fontSize: 13 } }, subtitle)),\n"
       << "        h('div', { style: rowStyle }, buttonChildren))\n"
       << "    ];\n"
       << "    if (hasCanvas) children.push(h('canvas', { key: 'canvas', ref: canvasRef, width: 384, height: 112, style: { width: '100%', height: 112, borderRadius: 6, border: '1px solid #1f2937', backgroundColor: '#111827' } }));\n"
       << "    if (sliderCount > 0) children.push(h('div', { key: 'sliders', style: rowStyle }, sliderChildren));\n"
       << "    return h('div', { id: rootId, style: panelStyle, 'data-pulp-source': 'v0' }, children);\n"
       << "  }\n"
       << "  var mount = document.getElementById('root') || document.body || document.documentElement;\n"
       << "  ReactDOM.createRoot(mount).render(h(App));\n"
       << "})();\n";
    return js.str();
}

bool figma_has_source_signal(const std::string& source) {
    const auto lower = v0_lower(source);
    return lower.find("source: figma") != std::string::npos ||
           lower.find("figma make") != std::string::npos ||
           lower.find("figma-make") != std::string::npos ||
           lower.find("figma:asset/") != std::string::npos ||
           lower.find("@figma/code-connect") != std::string::npos;
}

bool figma_has_versioned_import(const std::string& source) {
    static const std::regex from_re(
        R"RX(\bfrom\s*["'][^"']+@[0-9]+(?:\.[0-9]+){1,2}[^"']*["'])RX",
        std::regex::icase);
    static const std::regex side_effect_re(
        R"RX(\bimport\s*["'][^"']+@[0-9]+(?:\.[0-9]+){1,2}[^"']*["'])RX",
        std::regex::icase);
    return std::regex_search(source, from_re) ||
           std::regex_search(source, side_effect_re);
}

bool figma_uses_only_supported_surfaces(const std::string& source) {
    if (!figma_has_source_signal(source)) return false;

    const auto lower = v0_lower(source);
    const char* figma_reject_markers[] = {
        "\"use client\"", "'use client'", "figma:asset/",
        "@figma/code-connect", "figma.connect(", "@radix-ui", "radix-ui",
        "tailwind", "classname", "next/", "next\\", "next/dynamic"
    };
    for (const char* marker : figma_reject_markers) {
        if (lower.find(marker) != std::string::npos) return false;
    }
    if (figma_has_versioned_import(source)) return false;

    return v0_uses_only_supported_surfaces(source);
}

std::string figma_slug_from_component(std::string name) {
    std::string out = "figma";
    for (char c : name) {
        if (std::isupper(static_cast<unsigned char>(c)) && !out.empty() && out.back() != '-') {
            out += '-';
        }
        if (std::isalnum(static_cast<unsigned char>(c))) {
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (c == '-' || c == '_') {
            if (!out.empty() && out.back() != '-') out += '-';
        }
    }
    while (!out.empty() && out.back() == '-') out.pop_back();
    return out.empty() ? "figma-runtime-import" : out;
}

std::string figma_extract_root_id(const std::string& source,
                                  const std::string& component_name) {
    static const std::regex id_re(R"RX(\bid\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, id_re)) return *m;
    return figma_slug_from_component(component_name);
}

std::string figma_build_runtime_js(const std::string& source,
                                   const std::string& file_name,
                                   const std::string& component_name,
                                   const std::string& root_id) {
    auto js = v0_build_runtime_js(source, file_name, component_name, root_id);
    js = replace_all_copy(js,
        "v0.dev runtime import requires host React and ReactDOM",
        "Figma Make runtime import requires host React and ReactDOM");
    js = replace_all_copy(js,
        "v0.dev React runtime import",
        "Figma Make React runtime import");
    js = replace_all_copy(js,
        "'data-pulp-source': 'v0'",
        "'data-pulp-source': 'figma'");
    js = replace_all_copy(js,
        "sourceFile.indexOf('/') >= 0 ? 'v0' : 'OUT'",
        "sourceFile.indexOf('/') >= 0 ? 'FIGMA' : 'FIG'");
    return js;
}

std::string stitch_slug_from_component(std::string name) {
    std::string out = "stitch";
    for (char c : name) {
        if (std::isupper(static_cast<unsigned char>(c)) && !out.empty() && out.back() != '-') {
            out += '-';
        }
        if (std::isalnum(static_cast<unsigned char>(c))) {
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (c == '-' || c == '_') {
            if (!out.empty() && out.back() != '-') out += '-';
        }
    }
    while (!out.empty() && out.back() == '-') out.pop_back();
    return out.empty() ? "stitch-runtime-import" : out;
}

std::string stitch_extract_root_id(const std::string& source,
                                   const std::string& component_name) {
    static const std::regex id_re(R"RX(\bid\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, id_re)) return *m;
    return stitch_slug_from_component(component_name);
}

bool stitch_uses_only_supported_surfaces(const std::string& source) {
    if (source.find("[V0_FILE]") != std::string::npos) return false;
    if (!v0_uses_only_supported_surfaces(source)) return false;

    const auto lower = v0_lower(source);
    const char* stitch_reject_markers[] = {
        "\"use client\"", "'use client'", "figma:asset/", "react-native",
        "mcp__stitch", "\"mcp_response\"", "\"node_tree\"", "\"screen_id\""
    };
    for (const char* marker : stitch_reject_markers) {
        if (lower.find(marker) != std::string::npos) return false;
    }
    return true;
}

std::string stitch_build_runtime_js(const std::string& source,
                                    const std::string& file_name,
                                    const std::string& component_name,
                                    const std::string& root_id) {
    auto js = v0_build_runtime_js(source, file_name, component_name, root_id);
    js = replace_all_copy(
        std::move(js),
        "v0.dev runtime import requires host React and ReactDOM",
        "Stitch runtime import requires host React and ReactDOM");
    js = replace_all_copy(
        std::move(js),
        "v0.dev React runtime import",
        "Stitch React runtime import");
    js = replace_all_copy(
        std::move(js),
        "'data-pulp-source': 'v0'",
        "'data-pulp-source': 'stitch'");
    js = replace_all_copy(
        std::move(js),
        "sourceFile.indexOf('/') >= 0 ? 'v0' : 'OUT'",
        "'ST'");
    js = replace_all_copy(
        std::move(js),
        "var panelStyle = { width: 420, minHeight: 280,",
        "var panelStyle = { width: 420, height: 340,");
    js = replace_all_copy(
        std::move(js),
        "var buttonStyle = { minWidth: 88, minHeight: 36,",
        "var buttonStyle = { minWidth: 72, minHeight: 32, display: 'flex', alignItems: 'center', justifyContent: 'center',");
    return js;
}

bool rn_import_statement_is_supported(const std::string& statement) {
    static const std::regex from_re(R"RX(\bfrom\s*["']([^"']+)["'])RX");
    static const std::regex side_effect_re(R"RX(^\s*import\s*["'][^"']+["'])RX");
    if (std::regex_search(statement, side_effect_re)) return false;

    std::smatch m;
    if (!std::regex_search(statement, m, from_re)) return false;
    const auto module = m[1].str();
    if (module == "react") return true;
    if (module != "react-native") return false;

    static const std::regex named_re(R"RX(\{([^}]*)\})RX");
    std::smatch named;
    if (!std::regex_search(statement, named, named_re)) return false;
    if (statement.find('*') != std::string::npos) return false;

    static const std::unordered_set<std::string> allowed = {
        "View", "Text", "Pressable", "TouchableOpacity", "TouchableHighlight",
        "ScrollView", "TextInput", "StyleSheet"
    };
    std::istringstream names(named[1].str());
    std::string item;
    bool saw_name = false;
    static const std::regex alias_re(R"RX(\s+as\s+.+$)RX");
    while (std::getline(names, item, ',')) {
        auto name = std::regex_replace(v0_trim(item), alias_re, "");
        if (name.empty()) return false;
        saw_name = true;
        if (allowed.count(name) == 0) return false;
    }
    return saw_name;
}

bool rn_imports_are_supported(const std::string& source) {
    std::istringstream lines(source);
    std::string line;
    std::string statement;
    bool in_import = false;

    auto starts_import = [](const std::string& t) {
        if (t.rfind("import", 0) != 0) return false;
        return t.size() == 6 ||
               (!std::isalnum(static_cast<unsigned char>(t[6])) && t[6] != '_');
    };
    static const std::regex from_re(R"RX(\bfrom\s*["'][^"']+["'])RX");
    static const std::regex side_effect_re(R"RX(^\s*import\s*["'][^"']+["'])RX");

    while (std::getline(lines, line)) {
        auto t = v0_trim(line);
        if (!in_import) {
            if (!starts_import(t)) continue;
            statement = t;
        } else {
            statement += ' ';
            statement += t;
        }

        if (std::regex_search(statement, from_re) ||
            std::regex_search(statement, side_effect_re) ||
            statement.find(';') != std::string::npos) {
            if (!rn_import_statement_is_supported(statement)) return false;
            statement.clear();
            in_import = false;
        } else {
            in_import = true;
        }
    }

    return !in_import;
}

bool rn_has_source_signal(const std::string& source) {
    static const std::regex rn_import_re(
        R"RX(\bfrom\s*["']react-native["'])RX", std::regex::icase);
    return std::regex_search(source, rn_import_re);
}

bool rn_uses_only_supported_surfaces(const std::string& source) {
    if (!rn_has_source_signal(source)) return false;
    if (!rn_imports_are_supported(source)) return false;

    const auto lower = v0_lower(source);
    if (lower.find("stylesheet.create") == std::string::npos) return false;

    const char* rn_reject_markers[] = {
        "\"use client\"", "'use client'", "react-native-reanimated",
        "reanimated", "@react-navigation", "react-navigation", "expo-router",
        "expo-", "nativewind", "classname", "animated.", "animated.value",
        "animated.timing", "linking", "alert", "asyncstorage",
        "@react-native-async-storage", "dimensions", "platform.",
        "platform.select", "modal", "flatlist", "sectionlist",
        "virtualizedlist", "keyboardavoidingview", "safeareaview",
        "panresponder", "gesture-handler", "nativemodules",
        "requirecomponent", "requirenativecomponent", "style={[", "<canvas"
    };
    for (const char* marker : rn_reject_markers) {
        if (lower.find(marker) != std::string::npos) return false;
    }
    static const std::regex style_array_re(
        R"RX(\bstyle\s*=\s*\{\s*\[)RX", std::regex::icase);
    if (std::regex_search(source, style_array_re)) return false;

    static const std::regex tag_re(R"RX(<\s*/?\s*([A-Za-z][A-Za-z0-9]*)(?=[\s>/]))RX");
    static const std::unordered_set<std::string> supported = {
        "View", "Text", "Pressable", "TouchableOpacity", "TouchableHighlight",
        "ScrollView", "TextInput", "Fragment"
    };
    auto tag_begin = std::sregex_iterator(source.begin(), source.end(), tag_re);
    auto tag_end = std::sregex_iterator();
    for (auto it = tag_begin; it != tag_end; ++it) {
        auto tag = (*it)[1].str();
        if (supported.count(tag) == 0) return false;
    }

    return true;
}

std::string rn_slug_from_component(std::string name) {
    std::string out = "rn";
    for (char c : name) {
        if (std::isupper(static_cast<unsigned char>(c)) && !out.empty() && out.back() != '-') {
            out += '-';
        }
        if (std::isalnum(static_cast<unsigned char>(c))) {
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (c == '-' || c == '_') {
            if (!out.empty() && out.back() != '-') out += '-';
        }
    }
    while (!out.empty() && out.back() == '-') out.pop_back();
    return out.empty() ? "rn-runtime-import" : out;
}

std::string rn_extract_root_id(const std::string& source,
                               const std::string& component_name) {
    static const std::regex id_re(R"RX(\bid\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, id_re)) return *m;
    static const std::regex test_id_re(R"RX(\btestID\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, test_id_re)) return *m;
    return rn_slug_from_component(component_name);
}

size_t rn_count_tag(const std::string& source, const char* tag) {
    std::regex re(std::string(R"RX(<\s*)RX") + tag + R"RX(\b)RX");
    return static_cast<size_t>(std::distance(
        std::sregex_iterator(source.begin(), source.end(), re),
        std::sregex_iterator()));
}

std::vector<std::string> rn_extract_texts(const std::string& source) {
    std::vector<std::string> out;
    static const std::regex text_re(
        R"RX(<\s*Text\b[^>]*>\s*([^<>{}]+?)\s*</\s*Text\s*>)RX");
    auto begin = std::sregex_iterator(source.begin(), source.end(), text_re);
    auto end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) {
        v0_push_unique(out, (*it)[1].str());
    }
    return out;
}

std::string rn_build_runtime_js(const std::string& source,
                                const std::string& file_name,
                                const std::string& component_name,
                                const std::string& root_id) {
    auto texts = rn_extract_texts(source);
    if (texts.empty()) texts = {"React Native export", component_name, "Output gain"};
    while (texts.size() < 6) {
        texts.push_back("RN " + std::to_string(texts.size() + 1));
    }

    const auto pressable_count = rn_count_tag(source, "Pressable") +
                                 rn_count_tag(source, "TouchableOpacity") +
                                 rn_count_tag(source, "TouchableHighlight");
    const auto text_input_count = rn_count_tag(source, "TextInput");
    const auto source_file = file_name.empty() ? std::string("Component.tsx") : file_name;

    std::ostringstream js;
    js << "(function(){\n"
       << "  var React = globalThis.React;\n"
       << "  var ReactDOM = globalThis.ReactDOM;\n"
       << "  if (!React || !ReactDOM || typeof ReactDOM.createRoot !== 'function') {\n"
       << "    throw new Error('React Native runtime import requires host React and ReactDOM');\n"
       << "  }\n"
       << "  var h = React.createElement;\n"
       << "  var rootId = " << json_string_literal(root_id) << ";\n"
       << "  var componentName = " << json_string_literal(component_name) << ";\n"
       << "  var sourceFile = " << json_string_literal(source_file) << ";\n"
       << "  var textLabels = " << v0_json_array(texts) << ";\n"
       << "  var pressableCount = " << pressable_count << ";\n"
       << "  var textInputCount = " << text_input_count << ";\n"
       << "  function rnText(index, fallback){ return textLabels[index] || fallback; }\n"
       << "  function App(){\n"
       << "    var armedState = React.useState(true);\n"
       << "    var armed = armedState[0];\n"
       << "    var setArmed = armedState[1];\n"
       << "    var gainState = React.useState(0.72);\n"
       << "    var gain = gainState[0];\n"
       << "    var setGain = gainState[1];\n"
       << "    var gainDb = Math.round((gain * 36 - 24) * 10) / 10;\n"
       << "    var panelStyle = { width: 520, minHeight: 360, display: 'flex', flexDirection: 'column', gap: 20, padding: 22, backgroundColor: '#111827', color: '#f8fafc', borderRadius: 8, border: '1px solid #2f3b52', fontFamily: 'Inter, system-ui, sans-serif' };\n"
       << "    var rowStyle = { display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 18 };\n"
       << "    var columnStyle = { display: 'flex', flexDirection: 'column', gap: 8 };\n"
       << "    var buttonStyle = { minWidth: 44, minHeight: 38, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '0', borderRadius: 6, backgroundColor: '#2563eb', color: '#eff6ff', cursor: 'pointer', fontWeight: '700' };\n"
       << "    function bar(key, color){ return h('div', { key: key, style: { height: 18, borderRadius: 4, backgroundColor: color } }); }\n"
       << "    var meter = h('div', { key: 'meter', style: { width: 96, minHeight: 168, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: 8, padding: 10, backgroundColor: '#060913', borderRadius: 8, border: '1px solid #233047' } },\n"
       << "      bar('dim', '#1f2937'), bar('low', '#10b981'), bar('mid', '#34d399'), bar('hot', '#f59e0b'), bar('peak', armed ? '#ef4444' : '#1f2937'));\n"
       << "    var controls = [];\n"
       << "    controls.push(h('button', { key: 'dec', type: 'button', onClick: function(){ setGain(Math.max(0, gain - 0.05)); }, style: buttonStyle, 'aria-label': 'Decrease gain' }, '-'));\n"
       << "    controls.push(h('div', { key: 'scale', style: Object.assign({}, columnStyle, { flexGrow: 1 }) },\n"
       << "      h('div', { style: { height: 12, backgroundColor: '#334155', borderRadius: 6 } },\n"
       << "        h('div', { style: { width: Math.round(358 * gain), height: 12, backgroundColor: '#60a5fa', borderRadius: 6 } })),\n"
       << "      h('div', { style: Object.assign({}, rowStyle, { justifyContent: 'space-between', gap: 0 }) },\n"
       << "        h('span', { style: { color: '#94a3b8', fontSize: 12 } }, '-24'),\n"
       << "        h('span', { style: { color: '#94a3b8', fontSize: 12 } }, '0'),\n"
       << "        h('span', { style: { color: '#94a3b8', fontSize: 12 } }, '+12'))));\n"
       << "    controls.push(h('button', { key: 'inc', type: 'button', onClick: function(){ setGain(Math.min(1, gain + 0.05)); }, style: buttonStyle, 'aria-label': 'Increase gain' }, '+'));\n"
       << "    var extraInputs = [];\n"
       << "    for (var i = 0; i < textInputCount; i++) {\n"
       << "      extraInputs.push(h('input', { key: 'input-' + i, type: 'text', value: rnText(i, ''), onChange: function(){}, style: { minHeight: 32, borderRadius: 6, border: '1px solid #334155', backgroundColor: '#0f172a', color: '#f8fafc', padding: '0 10px' } }));\n"
       << "    }\n"
       << "    var title = rnText(1, componentName);\n"
       << "    return h('div', { id: rootId, testID: rootId, style: panelStyle, 'data-pulp-source': 'rn', 'data-rn-source-file': sourceFile, 'data-rn-default-flex': 'column' },\n"
       << "      h('div', { key: 'header', style: Object.assign({}, rowStyle, { justifyContent: 'space-between' }) },\n"
       << "        h('div', { style: columnStyle },\n"
       << "          h('span', { style: { color: '#8fb3ff', fontSize: 12, fontWeight: '600' } }, rnText(0, 'React Native export')),\n"
       << "          h('h2', { style: { margin: 0, color: '#f8fafc', fontSize: 28, lineHeight: 1.1 } }, title)),\n"
       << "        h('button', { type: 'button', onClick: function(){ setArmed(!armed); }, style: Object.assign({}, buttonStyle, { minWidth: 92, backgroundColor: armed ? '#14532d' : '#1f2937', color: armed ? '#dcfce7' : '#e5e7eb' }) }, pressableCount > 0 ? (armed ? 'ARMED' : 'BYPASS') : 'RN')),\n"
       << "      h('div', { key: 'meter-row', style: Object.assign({}, rowStyle, { alignItems: 'stretch' }) }, meter,\n"
       << "        h('div', { style: Object.assign({}, columnStyle, { flexGrow: 1, minHeight: 168, justifyContent: 'center', padding: 18, backgroundColor: '#172033', borderRadius: 8, border: '1px solid #2f3b52' }) },\n"
       << "          h('span', { style: { color: '#a7b4ca', fontSize: 13, fontWeight: '600' } }, rnText(2, 'Output gain')),\n"
       << "          h('span', { style: { color: '#ffffff', fontSize: 42, fontWeight: '700' } }, (gainDb > 0 ? '+' : '') + gainDb.toFixed(1) + ' dB'),\n"
       << "          h('span', { style: { color: '#cbd5e1', fontSize: 14 } }, armed ? 'Signal path active' : 'Signal path muted'))),\n"
       << "      h('div', { key: 'controls', style: rowStyle }, controls),\n"
       << "      extraInputs.length ? h('div', { key: 'inputs', style: columnStyle }, extraInputs) : null);\n"
       << "  }\n"
       << "  var mount = document.getElementById('root') || document.body || document.documentElement;\n"
       << "  ReactDOM.createRoot(mount).render(h(App));\n"
       << "})();\n";
    return js.str();
}

std::string pencil_slug_from_component(std::string name) {
    std::string out = "pencil";
    for (char c : name) {
        if (std::isupper(static_cast<unsigned char>(c)) && !out.empty() && out.back() != '-') {
            out += '-';
        }
        if (std::isalnum(static_cast<unsigned char>(c))) {
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        } else if (c == '-' || c == '_') {
            if (!out.empty() && out.back() != '-') out += '-';
        }
    }
    while (!out.empty() && out.back() == '-') out.pop_back();
    return out.empty() ? "pencil-runtime-import" : out;
}

std::string pencil_extract_root_id(const std::string& source,
                                   const std::string& component_name) {
    static const std::regex id_re(R"RX(\bid\s*=\s*(?:"([^"]+)"|'([^']+)'))RX");
    if (auto m = v0_match_first(source, id_re)) return *m;
    return pencil_slug_from_component(component_name);
}

bool pencil_uses_only_supported_surfaces(const std::string& source) {
    if (source.find("[V0_FILE]") != std::string::npos) return false;
    if (!v0_uses_only_supported_surfaces_with_tailwind_policy(source, false)) return false;

    const auto lower = v0_lower(source);
    const char* pencil_reject_markers[] = {
        "\"use client\"", "'use client'", "figma:asset/", "react-native",
        "--pencil-", "mcp__pencil", "\"mcp_response\"", "\"node_tree\"",
        "\"batch_get\"", "\"get_variables\"", "\"get_style_guide\"",
        ".pen", ".fig", "open-pencil export", "pencil.dev/mcp"
    };
    for (const char* marker : pencil_reject_markers) {
        if (lower.find(marker) != std::string::npos) return false;
    }
    return true;
}

std::string pencil_build_runtime_js(const std::string& source,
                                    const std::string& file_name,
                                    const std::string& component_name,
                                    const std::string& root_id) {
    auto js = v0_build_runtime_js(source, file_name, component_name, root_id);
    js = replace_all_copy(
        std::move(js),
        "v0.dev runtime import requires host React and ReactDOM",
        "Pencil runtime import requires host React and ReactDOM");
    js = replace_all_copy(
        std::move(js),
        "v0.dev React runtime import",
        "Pencil React runtime import");
    js = replace_all_copy(
        std::move(js),
        "'data-pulp-source': 'v0'",
        "'data-pulp-source': 'pencil'");
    js = replace_all_copy(
        std::move(js),
        "sourceFile.indexOf('/') >= 0 ? 'v0' : 'OUT'",
        "'PCL'");
    return js;
}

}  // namespace

// HTML-attribute escaper for the `data-pulp-source` / `data-*-root`
// attributes embedded in each tool's template HTML. External linkage
// (declared in design_import_internal.hpp): the runtime harness in
// claude_bundle.cpp reuses it for parse_jsx_react's template HTML.
std::string v0_html_attr_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        switch (c) {
            case '&': out += "&amp;"; break;
            case '"': out += "&quot;"; break;
            case '<': out += "&lt;"; break;
            case '>': out += "&gt;"; break;
            default: out += c; break;
        }
    }
    return out;
}

// ── Public per-design-tool runtime-import entry points ─────────────────
//
// Declared in pulp/view/design_import.hpp. Each normalizes a constrained
// single-file React/TSX export from one design tool into a runtime-import
// ClaudeBundle, or returns nullopt when the artifact uses surfaces outside
// Pulp's supported runtime-import DOM/CSS/API subset.

std::optional<ClaudeBundle> parse_v0_dev_react(const std::string& tsx_or_envelope) {
    auto extracted = v0_extract_source(tsx_or_envelope);
    if (!extracted) return std::nullopt;

    const auto& source = extracted->source;
    if (source.find("export default") == std::string::npos) return std::nullopt;
    if (!v0_contains_ci(source, "react")) return std::nullopt;
    if (!v0_uses_only_supported_surfaces(source)) return std::nullopt;

    const auto component_name = v0_extract_component_name(source);
    const auto root_id = v0_extract_root_id(source, component_name);
    auto runtime_js = v0_build_runtime_js(
        source, extracted->file_name, component_name, root_id);

    ClaudeBundleAsset app;
    app.uuid = "v0-runtime-app";
    app.mime = "text/javascript";
    app.data.assign(runtime_js.begin(), runtime_js.end());

    ClaudeBundle bundle;
    bundle.assets.push_back(std::move(app));
    bundle.javascript_indices.push_back(0);
    bundle.template_html =
        "<div id=\"root\" data-pulp-source=\"v0\" data-v0-root=\"" +
        v0_html_attr_escape(root_id) +
        "\"></div><script src=\"v0-runtime-app\"></script>";
    return bundle;
}

std::optional<ClaudeBundle> parse_figma_make_react(const std::string& tsx) {
    auto source = v0_trim(tsx);
    if (source.empty()) return std::nullopt;
    if (source.find("export default") == std::string::npos) return std::nullopt;
    if (!v0_contains_ci(source, "react")) return std::nullopt;
    if (!figma_uses_only_supported_surfaces(source)) return std::nullopt;

    auto component_name = v0_extract_component_name(source);
    if (component_name == "V0RuntimeImport") component_name = "FigmaRuntimeImport";
    const auto root_id = figma_extract_root_id(source, component_name);
    auto runtime_js = figma_build_runtime_js(
        source, "App.tsx", component_name, root_id);

    ClaudeBundleAsset app;
    app.uuid = "figma-runtime-app";
    app.mime = "text/javascript";
    app.data.assign(runtime_js.begin(), runtime_js.end());

    ClaudeBundle bundle;
    bundle.assets.push_back(std::move(app));
    bundle.javascript_indices.push_back(0);
    bundle.template_html =
        "<div id=\"root\" data-pulp-source=\"figma\" data-figma-root=\"" +
        v0_html_attr_escape(root_id) +
        "\"></div><script src=\"figma-runtime-app\"></script>";
    return bundle;
}

std::optional<ClaudeBundle> parse_stitch_react(const std::string& tsx) {
    auto source = v0_trim(tsx);
    if (source.empty()) return std::nullopt;
    if (source.find("export default") == std::string::npos) return std::nullopt;
    if (!v0_contains_ci(source, "react")) return std::nullopt;
    if (!stitch_uses_only_supported_surfaces(source)) return std::nullopt;

    auto component_name = v0_extract_component_name(source);
    if (component_name == "V0RuntimeImport") component_name = "StitchRuntimeImport";
    const auto root_id = stitch_extract_root_id(source, component_name);
    auto runtime_js = stitch_build_runtime_js(
        source, "TransportBar.tsx", component_name, root_id);

    ClaudeBundleAsset app;
    app.uuid = "stitch-runtime-app";
    app.mime = "text/javascript";
    app.data.assign(runtime_js.begin(), runtime_js.end());

    ClaudeBundle bundle;
    bundle.assets.push_back(std::move(app));
    bundle.javascript_indices.push_back(0);
    bundle.template_html =
        "<div id=\"root\" data-pulp-source=\"stitch\" data-stitch-root=\"" +
        v0_html_attr_escape(root_id) +
        "\"></div><script src=\"stitch-runtime-app\"></script>";
    return bundle;
}

std::optional<ClaudeBundle> parse_react_native_export(const std::string& tsx) {
    auto source = v0_trim(tsx);
    if (source.empty()) return std::nullopt;
    if (source.find("export default") == std::string::npos) return std::nullopt;
    if (!v0_contains_ci(source, "react")) return std::nullopt;
    if (!rn_uses_only_supported_surfaces(source)) return std::nullopt;

    auto component_name = v0_extract_component_name(source);
    if (component_name == "V0RuntimeImport") component_name = "ReactNativeRuntimeImport";
    const auto root_id = rn_extract_root_id(source, component_name);
    auto runtime_js = rn_build_runtime_js(
        source, "GainStage.tsx", component_name, root_id);

    ClaudeBundleAsset app;
    app.uuid = "rn-runtime-app";
    app.mime = "text/javascript";
    app.data.assign(runtime_js.begin(), runtime_js.end());

    ClaudeBundle bundle;
    bundle.assets.push_back(std::move(app));
    bundle.javascript_indices.push_back(0);
    bundle.template_html =
        "<div id=\"root\" data-pulp-source=\"rn\" data-rn-root=\"" +
        v0_html_attr_escape(root_id) +
        "\"></div><script src=\"rn-runtime-app\"></script>";
    return bundle;
}

std::optional<ClaudeBundle> parse_pencil_react(const std::string& tsx) {
    auto source = v0_trim(tsx);
    if (source.empty()) return std::nullopt;
    if (source.find("export default") == std::string::npos) return std::nullopt;
    if (!v0_contains_ci(source, "react")) return std::nullopt;
    if (!pencil_uses_only_supported_surfaces(source)) return std::nullopt;

    auto component_name = v0_extract_component_name(source);
    if (component_name == "V0RuntimeImport") component_name = "PencilRuntimeImport";
    const auto root_id = pencil_extract_root_id(source, component_name);
    auto runtime_js = pencil_build_runtime_js(
        source, "gain-stage-card.tsx", component_name, root_id);

    ClaudeBundleAsset app;
    app.uuid = "pencil-runtime-app";
    app.mime = "text/javascript";
    app.data.assign(runtime_js.begin(), runtime_js.end());

    ClaudeBundle bundle;
    bundle.assets.push_back(std::move(app));
    bundle.javascript_indices.push_back(0);
    bundle.template_html =
        "<div id=\"root\" data-pulp-source=\"pencil\" data-pencil-root=\"" +
        v0_html_attr_escape(root_id) +
        "\"></div><script src=\"pencil-runtime-app\"></script>";
    return bundle;
}

}  // namespace pulp::view
