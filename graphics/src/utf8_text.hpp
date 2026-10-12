#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace vellum::graphics::detail {

struct DecodedUtf8 {
    std::uint32_t code_point = 0xFFFDU;
    std::size_t end = 0U;
    bool valid = false;
};

// Decode one UTF-8 sequence at `start`. An invalid lead byte, a truncated or
// interrupted sequence, an overlong encoding, a surrogate, or a value above
// U+10FFFF is reported invalid and consumes exactly one byte, so callers always
// make progress and never read past the input.
inline DecodedUtf8 decode_utf8(std::string_view text, std::size_t start) {
    const auto lead = static_cast<unsigned char>(text[start]);
    if (lead < 0x80U) return {lead, start + 1U, true};
    int length = 0;
    std::uint32_t value = 0U;
    std::uint32_t minimum = 0U;
    if ((lead & 0xE0U) == 0xC0U) {
        length = 2; value = lead & 0x1FU; minimum = 0x80U;
    } else if ((lead & 0xF0U) == 0xE0U) {
        length = 3; value = lead & 0x0FU; minimum = 0x800U;
    } else if ((lead & 0xF8U) == 0xF0U) {
        length = 4; value = lead & 0x07U; minimum = 0x10000U;
    } else {
        return {0xFFFDU, start + 1U, false};
    }
    if (start + static_cast<std::size_t>(length) > text.size()) {
        return {0xFFFDU, start + 1U, false};
    }
    for (int index = 1; index < length; ++index) {
        const auto byte = static_cast<unsigned char>(text[start + static_cast<std::size_t>(index)]);
        if ((byte & 0xC0U) != 0x80U) return {0xFFFDU, start + 1U, false};
        value = (value << 6U) | (byte & 0x3FU);
    }
    if (value < minimum || value > 0x10FFFFU ||
        (value >= 0xD800U && value <= 0xDFFFU)) {
        return {0xFFFDU, start + 1U, false};
    }
    return {value, start + static_cast<std::size_t>(length), true};
}

// Copy valid sequences the renderer can draw and replace every invalid or
// undrawable sequence with the packaged "?" placeholder. The result is never
// longer than the input, so malformed text cannot inflate shaping work.
template <typename HasGlyph>
std::string replace_unsupported_text(std::string_view text, HasGlyph&& has_glyph) {
    constexpr std::string_view replacement = "?";
    std::string result;
    result.reserve(text.size());
    for (std::size_t offset = 0U; offset < text.size();) {
        const auto decoded = decode_utf8(text, offset);
        if (decoded.valid && has_glyph(decoded.code_point)) {
            result.append(text.substr(offset, decoded.end - offset));
        } else {
            result.append(replacement);
        }
        offset = decoded.end;
    }
    return result;
}

}  // namespace vellum::graphics::detail
