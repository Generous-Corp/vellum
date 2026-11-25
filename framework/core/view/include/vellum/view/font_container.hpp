#pragma once

// Checks for a font container held in memory, applied before any font parser
// sees the bytes, and registration of such a container without a path lookup.
// Panel fonts sealed into a bundle, design-import fonts read from a manifest,
// and captured page fonts share them.

#include <vellum/canvas/bundled_fonts.hpp>

#include <cstddef>
#include <cstdint>
#include <string>

namespace vellum::view {

/// The largest font container a design or panel ships that will be parsed.
/// No real face is near it, and the bytes are copied whole into the registry.
inline constexpr std::size_t kMaxShippedFontBytes = 32u * 1024u * 1024u;

/// Whether `data` starts like a TrueType (0x00010000 or 'true'), OpenType
/// ('OTTO') or WOFF2 ('wOF2') container. WOFF (`wOFF`) is intentionally not
/// accepted: the sealed-font discovery and import paths exclude it until a
/// bounded converter is available.
inline bool has_font_container_signature(const std::uint8_t* data, std::size_t size) noexcept {
    if (data == nullptr || size < 4)
        return false;
    const auto tag = [&](char a, char b, char c, char d) {
        return data[0] == static_cast<std::uint8_t>(a) && data[1] == static_cast<std::uint8_t>(b) &&
               data[2] == static_cast<std::uint8_t>(c) && data[3] == static_cast<std::uint8_t>(d);
    };
    return tag('\0', '\1', '\0', '\0') || tag('t', 'r', 'u', 'e') || tag('O', 'T', 'T', 'O') ||
           tag('w', 'O', 'F', '2');
}

namespace detail {

// The routing policy accepts parser operations so its refusal paths can be
// checked independently of a platform font manager's permissiveness.
struct FontContainerParsers {
    bool (*woff2_available)() noexcept;
    bool (*decode_woff2)(const std::uint8_t*, std::size_t, const std::string&);
    bool (*validate)(const std::uint8_t*, std::size_t);
    bool (*register_raw)(const std::uint8_t*, std::size_t, const std::string&);
};

inline bool register_font_container_with(const std::uint8_t* data, std::size_t size,
                                         const std::string& family,
                                         const FontContainerParsers& parsers) {
    if (size > kMaxShippedFontBytes || !has_font_container_signature(data, size))
        return false;
    if (data[0] == 'w') {
        if (parsers.woff2_available())
            return parsers.decode_woff2(data, size, family);
        return false;
    }
    if (!parsers.validate(data, size))
        return false;
    return parsers.register_raw(data, size, family);
}

} // namespace detail

/// Register an in-memory font container under `family`. Bytes larger than
/// `kMaxShippedFontBytes` or without a font container signature are refused
/// before any parser sees them. TrueType and OpenType bytes are structurally
/// validated first. WOFF2 goes through the WOFF2 decoder, which bounds and
/// validates the decompressed face; when the decoder rejects the bytes they
/// are refused, never handed to the platform font manager instead. Builds
/// without a decoder fail closed because the raw registrar accepts sfnt bytes,
/// not compressed WOFF2. Never consults the filesystem. Registration is
/// process-global.
inline bool register_font_container(const std::uint8_t* data, std::size_t size,
                                    const std::string& family) {
    const detail::FontContainerParsers parsers{
        vellum::canvas::woff2_decoder_available, vellum::canvas::register_font_woff2,
        vellum::canvas::validate_font_bytes, vellum::canvas::register_font};
    return detail::register_font_container_with(data, size, family, parsers);
}

} // namespace vellum::view
