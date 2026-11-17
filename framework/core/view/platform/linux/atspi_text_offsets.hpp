#pragma once

// Pure UTF-8 offset helpers for the AT-SPI Text interface. AT-SPI counts and
// addresses text in Unicode characters (code points), while a View's text and
// selection are UTF-8 bytes; every conversion between the two goes through
// these functions. Malformed bytes count as one character each, so a lone
// continuation or truncated sequence can never stall a walk.

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace vellum::view::atspi_text {

// Byte length of the character starting at `pos`, never past the end.
inline size_t char_length_at(std::string_view s, size_t pos) {
    const auto lead = static_cast<unsigned char>(s[pos]);
    size_t want = 1;
    if (lead >= 0xF0 && lead <= 0xF4) want = 4;
    else if (lead >= 0xE0) want = lead < 0xF0 ? 3 : 1;
    else if (lead >= 0xC2 && lead < 0xE0) want = 2;
    if (want == 1 || pos + want > s.size()) return 1;
    for (size_t i = 1; i < want; ++i) {
        if ((static_cast<unsigned char>(s[pos + i]) & 0xC0) != 0x80) return 1;
    }
    return want;
}

inline int character_count(std::string_view s) {
    int count = 0;
    for (size_t pos = 0; pos < s.size(); pos += char_length_at(s, pos)) ++count;
    return count;
}

// Byte offset of the character at index `chars`; the text size when `chars`
// is at or past the end, 0 when negative.
inline size_t byte_offset_of_character(std::string_view s, int chars) {
    size_t pos = 0;
    for (int i = 0; i < chars && pos < s.size(); ++i) pos += char_length_at(s, pos);
    return pos;
}

// Character index of the byte offset `bytes`, clamped to the text.
inline int character_index_of_byte(std::string_view s, int bytes) {
    if (bytes <= 0) return 0;
    int index = 0;
    for (size_t pos = 0; pos < s.size() && pos < static_cast<size_t>(bytes);
         pos += char_length_at(s, pos)) {
        ++index;
    }
    return index;
}

// Code point of the character at index `chars`; 0 when out of range. A
// malformed byte decodes to U+FFFD.
inline uint32_t code_point_at(std::string_view s, int chars) {
    if (chars < 0 || chars >= character_count(s)) return 0;
    const size_t pos = byte_offset_of_character(s, chars);
    const size_t len = char_length_at(s, pos);
    const auto b0 = static_cast<unsigned char>(s[pos]);
    if (len == 1) return b0 < 0x80 ? b0 : 0xFFFDu;
    uint32_t cp = len == 2 ? (b0 & 0x1Fu) : len == 3 ? (b0 & 0x0Fu) : (b0 & 0x07u);
    for (size_t i = 1; i < len; ++i) cp = (cp << 6) | (static_cast<unsigned char>(s[pos + i]) & 0x3Fu);
    return cp;
}

// Characters [start, end) as UTF-8. `end` < 0 or past the end means the end;
// `start` < 0 means 0.
inline std::string substring(std::string_view s, int start, int end) {
    const int total = character_count(s);
    if (start < 0) start = 0;
    if (end < 0 || end > total) end = total;
    if (start >= end) return {};
    const size_t from = byte_offset_of_character(s, start);
    const size_t to = byte_offset_of_character(s, end);
    return std::string(s.substr(from, to - from));
}

}  // namespace vellum::view::atspi_text
