#include "../src/utf8_text.hpp"

#include <cstdint>
#include <cstdio>
#include <string>
#include <string_view>

namespace {

int failures = 0;

void expect(bool condition, const char* label) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", label);
        ++failures;
    }
}

bool all_glyphs(std::uint32_t) { return true; }

std::string sanitize(std::string_view text) {
    return vellum::graphics::detail::replace_unsupported_text(text, all_glyphs);
}

void expect_invalid(std::string_view bytes, std::size_t consumed, const char* label) {
    const auto decoded = vellum::graphics::detail::decode_utf8(bytes, 0U);
    expect(!decoded.valid, label);
    expect(decoded.end == consumed, label);
    expect(decoded.code_point == 0xFFFDU, label);
}

}  // namespace

int main() {
    using vellum::graphics::detail::decode_utf8;

    // Well-formed sequences of every length decode to their code point.
    expect(decode_utf8("A", 0U).code_point == 0x41U, "ascii");
    expect(decode_utf8("\xC3\xA9", 0U).code_point == 0xE9U, "two-byte");
    expect(decode_utf8("\xE2\x82\xAC", 0U).code_point == 0x20ACU, "three-byte");
    expect(decode_utf8("\xF0\x9F\x8E\xB5", 0U).code_point == 0x1F3B5U, "four-byte");
    expect(decode_utf8("\xF0\x9F\x8E\xB5", 0U).end == 4U, "four-byte length");

    // Malformed input is invalid and always consumes exactly one byte.
    expect_invalid("\x80", 1U, "lone continuation byte");
    expect_invalid("\xFF", 1U, "invalid lead byte");
    expect_invalid("\xC3", 1U, "truncated two-byte");
    expect_invalid("\xE2\x82", 1U, "truncated three-byte");
    expect_invalid("\xF0\x9F\x8E", 1U, "truncated four-byte");
    // A truncated view must not read the bytes that follow it, even when those
    // bytes would complete a valid sequence.
    expect_invalid(std::string_view("\xC3\xA9", 1U), 1U, "view-truncated two-byte");
    expect_invalid(std::string_view("\xE2\x82\xAC", 2U), 1U, "view-truncated three-byte");
    expect_invalid(std::string_view("\xF0\x9F\x8E\xB5", 3U), 1U, "view-truncated four-byte");
    expect_invalid("\xE2\x28\xA1", 1U, "interrupted continuation");
    expect_invalid("\xC0\xAF", 1U, "overlong two-byte");
    expect_invalid("\xE0\x80\xAF", 1U, "overlong three-byte");
    expect_invalid("\xF0\x80\x80\xAF", 1U, "overlong four-byte");
    expect_invalid("\xED\xA0\x80", 1U, "UTF-16 surrogate");
    expect_invalid("\xF4\x90\x80\x80", 1U, "above U+10FFFF");

    // Sanitizing replaces each malformed byte with one placeholder, keeps valid
    // text, and never grows the input.
    expect(sanitize("ok") == "ok", "valid ascii kept");
    expect(sanitize("caf\xC3\xA9") == "caf\xC3\xA9", "valid multibyte kept");
    expect(sanitize("a\xE0\x80\xAF" "b") == "a???b", "overlong replaced byte by byte");
    expect(sanitize("\xC3") == "?", "trailing truncation replaced");
    const std::string hostile(4096U, '\x80');
    const auto cleaned = sanitize(hostile);
    expect(cleaned.size() == hostile.size(), "hostile input does not grow");
    expect(cleaned == std::string(4096U, '?'), "hostile input fully replaced");

    // An undrawable but valid code point is replaced too.
    const auto no_euro = vellum::graphics::detail::replace_unsupported_text(
        "x\xE2\x82\xAC", [](std::uint32_t code_point) { return code_point != 0x20ACU; });
    expect(no_euro == "x?", "undrawable code point replaced");

    if (failures != 0) {
        std::fprintf(stderr, "%d utf8 text check(s) failed\n", failures);
        return 1;
    }
    return 0;
}
