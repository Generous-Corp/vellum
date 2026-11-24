// Link-time spies exercise the real container router without a platform parser.
#include "framework_test.hpp"
#include <vellum/view/font_container.hpp>
#include <array>

namespace {
bool decoder_available = true;
bool decoder_accepts = false;
int decoder_calls = 0;
int parser_calls = 0;
}
namespace vellum::canvas {
bool woff2_decoder_available() noexcept { return decoder_available; }
bool register_font_woff2(const std::uint8_t*, std::size_t, const std::string&) {
    ++decoder_calls;
    return decoder_accepts;
}
bool validate_font_bytes(const std::uint8_t*, std::size_t) { return true; }
bool register_font(const std::uint8_t*, std::size_t, const std::string&) {
    ++parser_calls;
    return true;
}
}

FRAMEWORK_TEST(decoder_rejection_never_falls_through_to_platform_parser) {
    std::array<std::uint8_t, 48> bytes{};
    bytes[0] = 'w'; bytes[1] = 'O'; bytes[2] = 'F'; bytes[3] = '2';
    bytes[18] = 0x10; // Declares 4096 bytes, inside the raw fallback bound.
    decoder_available = true;
    decoder_accepts = false;
    decoder_calls = parser_calls = 0;
    CHECK(!vellum::view::register_font_container(bytes.data(), bytes.size(), "rejected"));
    CHECK(decoder_calls == 1);
    CHECK(parser_calls == 0);

    decoder_accepts = true;
    CHECK(vellum::view::register_font_container(bytes.data(), bytes.size(), "decoded"));
    CHECK(decoder_calls == 2);
    CHECK(parser_calls == 0);

    // Positive control: the same platform spy is reachable without a decoder.
    decoder_available = false;
    CHECK(vellum::view::register_font_container(bytes.data(), bytes.size(), "fallback"));
    CHECK(parser_calls == 1);
    bytes[16] = 0x7f;
    CHECK(!vellum::view::register_font_container(bytes.data(), bytes.size(), "oversized"));
    CHECK(parser_calls == 1);
}

int main() { return framework_test::run_all(); }
