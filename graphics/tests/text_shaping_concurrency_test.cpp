#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <future>
#include <iostream>
#include <latch>
#include <string>
#include <utility>
#include <vector>

namespace {

using vellum::graphics::Color;
using vellum::graphics::SkiaDawnSurface;
using vellum::graphics::TextMetrics;
using vellum::graphics::TextRun;
using vellum::graphics::TextStyle;

struct ShapeResult final {
    bool ok = false;
    TextMetrics metrics;
    std::string error;
};

std::uint32_t next_random(std::uint32_t& state) {
    state = state * 1664525U + 1013904223U;
    return state;
}

bool close(float lhs, float rhs) {
    return std::abs(lhs - rhs) <= 0.01F;
}

bool same_metrics(const TextMetrics& lhs, const TextMetrics& rhs) {
    return close(lhs.width, rhs.width) && close(lhs.ascent, rhs.ascent) &&
        close(lhs.descent, rhs.descent) && close(lhs.baseline, rhs.baseline);
}

ShapeResult measure(std::vector<TextRun> runs) {
    ShapeResult result;
    result.ok = SkiaDawnSurface::measure_text(
        runs, result.metrics, {}, &result.error);
    return result;
}

std::vector<TextRun> make_runs(std::uint32_t& state) {
    constexpr std::array<const char*, 8> text{
        "oscillator", "Semibold 日本語", "سلام", "e\xCC\x81" "cho",
        "launch \xF0\x9F\x9A\x80", "envelope", "Vellum ", "render",
    };
    constexpr std::array<const char*, 2> families{"Inter", "Jost"};
    constexpr std::array<int, 4> weights{400, 500, 600, 700};
    const auto count = 1U + next_random(state) % 3U;
    std::vector<TextRun> runs;
    runs.reserve(count);
    for (std::uint32_t index = 0; index < count; ++index) {
        const auto value = next_random(state);
        runs.push_back({
            .text = text[value % text.size()],
            .style = {
                .font_family = families[(value >> 3U) % families.size()],
                .font_weight = weights[(value >> 5U) % weights.size()],
                .font_size = 10.0F + static_cast<float>((value >> 7U) % 28U),
                .letter_spacing = static_cast<float>((value >> 12U) % 5U) * 0.25F,
                .color = Color::hex(0xF8FAFC),
                .underline = ((value >> 16U) & 1U) != 0U,
                .strikethrough = ((value >> 17U) & 1U) != 0U,
            },
        });
    }
    return runs;
}

int configured_rounds() {
    const char* requested = std::getenv("VELLUM_TEXT_STRESS_ROUNDS");
    if (requested == nullptr || *requested == '\0') return 4;
    char* end = nullptr;
    const long parsed = std::strtol(requested, &end, 10);
    if (end == requested || *end != '\0' || parsed < 1 || parsed > 1000) {
        std::cerr << "VELLUM_TEXT_STRESS_ROUNDS must be between 1 and 1000\n";
        return -1;
    }
    return static_cast<int>(parsed);
}

}  // namespace

int main() {
    const int rounds = configured_rounds();
    if (rounds < 0) return 2;

    constexpr std::size_t cases_per_round = 8U;
    std::uint32_t state = 0x56454C4CU;
    for (int round = 0; round < rounds; ++round) {
        std::vector<std::vector<TextRun>> cases;
        cases.reserve(cases_per_round);
        for (std::size_t index = 0; index < cases_per_round; ++index) {
            cases.push_back(make_runs(state));
        }

        std::vector<std::future<ShapeResult>> futures;
        futures.reserve(cases.size() * 2U);
        std::latch ready(static_cast<std::ptrdiff_t>(cases.size() * 2U));
        std::promise<void> release;
        const auto start = release.get_future().share();
        for (const auto& runs : cases) {
            futures.push_back(std::async(
                std::launch::async, [owned = runs, &ready, start] {
                    ready.count_down();
                    start.wait();
                    return measure(owned);
                }));
            futures.push_back(std::async(
                std::launch::async, [owned = runs, &ready, start] {
                    ready.count_down();
                    start.wait();
                    return measure(owned);
                }));
        }
        ready.wait();
        release.set_value();

        for (std::size_t index = 0; index < cases.size(); ++index) {
            const auto first = futures[index * 2U].get();
            const auto second = futures[index * 2U + 1U].get();
            const auto serial = measure(cases[index]);
            if (!first.ok || !second.ok || !serial.ok ||
                first.metrics.width <= 0.0F || first.metrics.ascent <= 0.0F ||
                !same_metrics(first.metrics, second.metrics) ||
                !same_metrics(first.metrics, serial.metrics)) {
                std::cerr << "text-shaping concurrency mismatch at round " << round
                          << " case " << index << ": " << first.error << ' '
                          << second.error << ' ' << serial.error << '\n';
                return 1;
            }
        }
    }

    std::cout << "text-shaping concurrency stress passed " << rounds
              << " rounds and "
              << static_cast<std::size_t>(rounds) * cases_per_round * 3U
              << " paragraph builds\n";
    return 0;
}
