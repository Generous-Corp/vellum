#pragma once

#include <vellum/canvas/bundled_fonts.hpp>
#include <cstddef>
#include <cstdint>
#include <string>

namespace vellum::view {

inline constexpr std::size_t kMaxShippedFontBytes = 32u * 1024u * 1024u;
inline constexpr std::size_t kMaxShippedWoff2SfntBytes = 30u * 1024u * 1024u;

inline bool has_font_container_signature(const std::uint8_t* data, std::size_t size) noexcept {
    if (!data || size < 4) return false;
    const auto tag = [&](char a, char b, char c, char d) {
        return data[0] == static_cast<std::uint8_t>(a) && data[1] == static_cast<std::uint8_t>(b) &&
               data[2] == static_cast<std::uint8_t>(c) && data[3] == static_cast<std::uint8_t>(d);
    };
    return tag('\0', '\1', '\0', '\0') || tag('t','r','u','e') || tag('O','T','T','O') || tag('w','O','F','2');
}

inline std::size_t woff2_declared_sfnt_size(const std::uint8_t* data, std::size_t size) noexcept {
    if (!data || size < 20) return 0;
    return (static_cast<std::size_t>(data[16]) << 24) | (static_cast<std::size_t>(data[17]) << 16) |
           (static_cast<std::size_t>(data[18]) << 8) | static_cast<std::size_t>(data[19]);
}

inline bool register_font_container(const std::uint8_t* data, std::size_t size,
                                    const std::string& family) {
    if (size > kMaxShippedFontBytes || !has_font_container_signature(data, size)) return false;
    if (data[0] == 'w') {
        if (vellum::canvas::woff2_decoder_available())
            return vellum::canvas::register_font_woff2(data, size, family);
        const auto declared = woff2_declared_sfnt_size(data, size);
        if (declared == 0 || declared > kMaxShippedWoff2SfntBytes) return false;
        return vellum::canvas::register_font(data, size, family);
    }
    if (!vellum::canvas::validate_font_bytes(data, size)) return false;
    return vellum::canvas::register_font(data, size, family);
}

} // namespace vellum::view
