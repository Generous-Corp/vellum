#pragma once

// Strict JSON document parsing on top of CHOC.
//
// choc::json::parse() reads one top-level object or array and returns without
// looking at what follows, so `{"a": 1} trailing` parses as `{"a": 1}`. A
// detection manifest or a JSON input that is not a whole document must be
// rejected rather than read as its prefix, so this wrapper finds where the
// top-level value ends and requires that only whitespace follows it.

#include <choc/text/choc_JSON.h>

#include <cstddef>
#include <optional>
#include <string_view>

namespace vellum::authoring::strict_json {

/// The offset just past the top-level object or array in `text`, skipping
/// leading whitespace and the contents of strings; npos if the value never
/// closes or the text does not start with `{` or `[`.
inline std::size_t end_of_top_level_value(std::string_view text) {
    std::size_t i = text.find_first_not_of(" \t\r\n");
    if (i == std::string_view::npos || (text[i] != '{' && text[i] != '[')) return std::string_view::npos;
    int depth = 0;
    bool in_string = false;
    for (; i < text.size(); ++i) {
        const char c = text[i];
        if (in_string) {
            if (c == '\\') ++i;
            else if (c == '"') in_string = false;
            continue;
        }
        if (c == '"') in_string = true;
        else if (c == '{' || c == '[') ++depth;
        else if ((c == '}' || c == ']') && --depth == 0) return i + 1;
    }
    return std::string_view::npos;
}

/// Parses `text` as one complete JSON object or array, or returns nullopt for
/// any syntax error, an unterminated value, or non-whitespace after it.
inline std::optional<choc::value::Value> parse_document(std::string_view text) {
    const std::size_t end = end_of_top_level_value(text);
    if (end == std::string_view::npos) return std::nullopt;
    if (text.find_first_not_of(" \t\r\n", end) != std::string_view::npos) return std::nullopt;
    try {
        return choc::json::parse(text.substr(0, end));
    } catch (...) {
        return std::nullopt;
    }
}

} // namespace vellum::authoring::strict_json
