// Checks the platform-independent helpers behind the Linux backends: the
// AT-SPI UTF-8 offset arithmetic, the XDND reply validation and the
// /proc/meminfo classification. They have no platform dependency, so they run
// everywhere.

#include "framework_test.hpp"

#include "../../framework/core/platform/platform/linux/meminfo_pressure.hpp"
#include "../../framework/core/view/platform/linux/atspi_text_offsets.hpp"
#include "../../framework/core/view/platform/linux/xdnd_parse.hpp"

#include <string>

namespace {

namespace text = vellum::view::atspi_text;
namespace xdnd = vellum::view::xdnd;
namespace env = vellum::platform::linux_env;
using vellum::platform::MemoryPressure;

// "aé😀b": a 1-byte, a 2-byte, a 4-byte and a 1-byte character (8 bytes).
const std::string kMixed = "a\xC3\xA9\xF0\x9F\x98\x80" "b";

FRAMEWORK_TEST(text_counts_characters_not_bytes) {
    CHECK(kMixed.size() == 8);
    CHECK(text::character_count(kMixed) == 4);
    CHECK(text::character_count("") == 0);
    CHECK(text::character_count("\xC3\xA9") == 1);
}

FRAMEWORK_TEST(text_slices_on_character_boundaries) {
    CHECK(text::substring("\xC3\xA9", 0, 1) == "\xC3\xA9");
    CHECK(text::substring(kMixed, 1, 3) == "\xC3\xA9\xF0\x9F\x98\x80");
    CHECK(text::substring(kMixed, 0, -1) == kMixed);
    CHECK(text::substring(kMixed, 3, 2).empty());
    CHECK(text::substring(kMixed, -4, 99) == kMixed);
}

FRAMEWORK_TEST(text_reads_code_points) {
    CHECK(text::code_point_at(kMixed, 0) == 'a');
    CHECK(text::code_point_at(kMixed, 1) == 0xE9u);
    CHECK(text::code_point_at(kMixed, 2) == 0x1F600u);
    CHECK(text::code_point_at(kMixed, 3) == 'b');
    CHECK(text::code_point_at(kMixed, 4) == 0u);
    CHECK(text::code_point_at(kMixed, -1) == 0u);
}

FRAMEWORK_TEST(text_converts_between_byte_and_character_offsets) {
    CHECK(text::byte_offset_of_character(kMixed, 0) == 0);
    CHECK(text::byte_offset_of_character(kMixed, 2) == 3);
    CHECK(text::byte_offset_of_character(kMixed, 3) == 7);
    CHECK(text::byte_offset_of_character(kMixed, 99) == 8);
    CHECK(text::character_index_of_byte(kMixed, 0) == 0);
    CHECK(text::character_index_of_byte(kMixed, 3) == 2);
    CHECK(text::character_index_of_byte(kMixed, 7) == 3);
    CHECK(text::character_index_of_byte(kMixed, 8) == 4);
    CHECK(text::character_index_of_byte(kMixed, 99) == 4);
}

FRAMEWORK_TEST(text_treats_malformed_bytes_as_single_characters) {
    const std::string stray = "a\x80z";           // lone continuation byte
    const std::string cut = "a\xE2\x82";          // truncated 3-byte sequence
    CHECK(text::character_count(stray) == 3);
    CHECK(text::character_count(cut) == 3);
    CHECK(text::code_point_at(stray, 1) == 0xFFFDu);
}

FRAMEWORK_TEST(xdnd_replies_apply_only_from_the_current_target) {
    CHECK(xdnd::status_is_from_target(42, 42));
    CHECK(!xdnd::status_is_from_target(41, 42));   // delayed reply from an earlier target
    CHECK(!xdnd::status_is_from_target(0, 0));     // no target yet
}

FRAMEWORK_TEST(xdnd_finished_needs_a_drop_and_the_current_target) {
    CHECK(!xdnd::finished_outcome(42, 42, false, 5, 1));  // premature
    CHECK(!xdnd::finished_outcome(41, 42, true, 5, 1));   // wrong sender
    CHECK(xdnd::finished_outcome(42, 42, true, 5, 1) == std::optional<bool>(true));
    CHECK(xdnd::finished_outcome(42, 42, true, 5, 0) == std::optional<bool>(false));
}

FRAMEWORK_TEST(xdnd_older_targets_finish_without_the_success_bit) {
    // Reserved fields are zero before version 5, and that is still a success.
    CHECK(xdnd::finished_outcome(42, 42, true, 3, 0) == std::optional<bool>(true));
    CHECK(xdnd::finished_outcome(42, 42, true, 4, 0) == std::optional<bool>(true));
}

FRAMEWORK_TEST(xdnd_versions_below_the_floor_are_not_driven) {
    CHECK(xdnd::negotiated_version(0) == 0);
    CHECK(xdnd::negotiated_version(2) == 0);
    CHECK(xdnd::negotiated_version(3) == 3);
    CHECK(xdnd::negotiated_version(5) == 5);
    CHECK(xdnd::negotiated_version(9) == 5);
}

FRAMEWORK_TEST(meminfo_zero_available_is_critical) {
    const env::MeminfoField total{true, 16'000'000};
    CHECK(env::classify_memory_pressure(total, {true, 0}) == MemoryPressure::critical);
    CHECK(env::classify_memory_pressure(total, {true, 500'000}) == MemoryPressure::critical);
    CHECK(env::classify_memory_pressure(total, {true, 2'000'000}) == MemoryPressure::moderate);
    CHECK(env::classify_memory_pressure(total, {true, 8'000'000}) == MemoryPressure::normal);
}

FRAMEWORK_TEST(meminfo_missing_fields_are_no_reading) {
    const env::MeminfoField total{true, 16'000'000};
    CHECK(env::classify_memory_pressure(total, {}) == MemoryPressure::normal);
    CHECK(env::classify_memory_pressure({}, {true, 0}) == MemoryPressure::normal);
    CHECK(env::classify_memory_pressure({true, 0}, {true, 0}) == MemoryPressure::normal);
}

FRAMEWORK_TEST(meminfo_lines_parse_with_presence) {
    const auto zero = env::parse_meminfo_line("MemAvailable:             0 kB", "MemAvailable:");
    CHECK(zero.present && zero.kb == 0);
    const auto value = env::parse_meminfo_line("MemTotal:       16384 kB", "MemTotal:");
    CHECK(value.present && value.kb == 16384);
    CHECK(!env::parse_meminfo_line("MemFree: 5 kB", "MemTotal:").present);
    CHECK(!env::parse_meminfo_line("MemAvailable: junk kB", "MemAvailable:").present);
}

}  // namespace

int main() { return framework_test::run_all(); }
