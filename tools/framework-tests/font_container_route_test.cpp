// Parser spies exercise the production routing policy without a platform parser.
#include "framework_test.hpp"
#include <vellum/view/font_container.hpp>
#include <array>

namespace {
bool decoder_available = true;
bool decoder_accepts = false;
int decoder_calls = 0;
int parser_calls = 0;
bool spy_decoder_available() noexcept { return decoder_available; }
bool spy_decode_woff2(const std::uint8_t*, std::size_t, const std::string&) {
    ++decoder_calls;
    return decoder_accepts;
}
bool spy_validate(const std::uint8_t*, std::size_t) { return true; }
bool spy_register_raw(const std::uint8_t*, std::size_t, const std::string&) {
    ++parser_calls;
    return true;
}
}

FRAMEWORK_TEST(decoder_rejection_never_falls_through_to_platform_parser) {
    const vellum::view::detail::FontContainerParsers parsers{
        spy_decoder_available, spy_decode_woff2, spy_validate, spy_register_raw};
    const auto register_container = [&](const auto& bytes, const std::string& family) {
        return vellum::view::detail::register_font_container_with(
            bytes.data(), bytes.size(), family, parsers);
    };
    std::array<std::uint8_t, 48> bytes{};
    bytes[0] = 'w'; bytes[1] = 'O'; bytes[2] = 'F'; bytes[3] = '2';
    bytes[18] = 0x10; // Declares 4096 bytes, inside the raw fallback bound.
    decoder_available = true;
    decoder_accepts = false;
    decoder_calls = parser_calls = 0;
    CHECK(!register_container(bytes, "rejected"));
    CHECK(decoder_calls == 1);
    CHECK(parser_calls == 0);

    decoder_accepts = true;
    CHECK(register_container(bytes, "decoded"));
    CHECK(decoder_calls == 2);
    CHECK(parser_calls == 0);

    // Without a decoder, compressed WOFF2 cannot reach the sfnt-only registrar.
    decoder_available = false;
    CHECK(!register_container(bytes, "no-decoder"));
    CHECK(parser_calls == 0);
    bytes[16] = 0x7f;
    CHECK(!register_container(bytes, "oversized"));
    CHECK(parser_calls == 0);
}

int main() { return framework_test::run_all(); }
