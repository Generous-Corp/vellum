#pragma once

#include <cstddef>
#include <cstdint>

namespace vellum::canvas::detail {

inline std::uint16_t read_u16_be(const std::uint8_t* p) noexcept {
    return static_cast<std::uint16_t>((p[0] << 8) | p[1]);
}

inline std::uint32_t read_u32_be(const std::uint8_t* p) noexcept {
    return (static_cast<std::uint32_t>(p[0]) << 24)
         | (static_cast<std::uint32_t>(p[1]) << 16)
         | (static_cast<std::uint32_t>(p[2]) << 8)
         | static_cast<std::uint32_t>(p[3]);
}

inline bool is_valid_sfnt_magic(std::uint32_t magic) noexcept {
    return magic == 0x00010000u || magic == 0x4F54544Fu || magic == 0x74727565u ||
           magic == 0x74797031u;
}

// Structural sfnt sanitizer shared by Skia and CoreText registration paths.
// A platform parser must never receive signature-only or out-of-bounds bytes.
inline bool structurally_validate_sfnt(const std::uint8_t* data, std::size_t size) noexcept {
    if (!data || size < 12) return false;
    if (!is_valid_sfnt_magic(read_u32_be(data))) return false;

    const auto num_tables = read_u16_be(data + 4);
    if (num_tables == 0) return false;
    const auto dir_end = 12u + 16u * static_cast<std::size_t>(num_tables);
    if (dir_end > size) return false;

    constexpr std::uint32_t kHead = 0x68656164u;
    constexpr std::uint32_t kName = 0x6E616D65u;
    constexpr std::uint32_t kCmap = 0x636D6170u;
    constexpr std::uint32_t kMaxp = 0x6D617870u;
    bool has_head = false, has_name = false, has_cmap = false, has_maxp = false;
    std::uint32_t head_offset = 0, head_length = 0;

    for (std::uint16_t i = 0; i < num_tables; ++i) {
        const auto* entry = data + 12 + 16 * i;
        const auto tag = read_u32_be(entry);
        const auto offset = read_u32_be(entry + 8);
        const auto length = read_u32_be(entry + 12);
        if (offset > size || length > size - offset) return false;
        if (tag == kHead) {
            if (length < 54) return false;
            has_head = true;
            head_offset = offset;
            head_length = length;
        } else if (tag == kName) {
            if (length < 6) return false;
            has_name = true;
        } else if (tag == kCmap) {
            if (length < 4) return false;
            has_cmap = true;
        } else if (tag == kMaxp) {
            if (length < 6) return false;
            has_maxp = true;
        }
    }
    if (!has_head || !has_name || !has_cmap || !has_maxp || head_length < 54)
        return false;
    return read_u32_be(data + head_offset + 12) == 0x5F0F3CF5u;
}

} // namespace vellum::canvas::detail
