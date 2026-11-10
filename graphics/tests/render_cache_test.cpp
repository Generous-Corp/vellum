#include "render_cache.hpp"

#include <vellum/graphics/render_telemetry.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <string>

namespace {

using namespace vellum::graphics;
using detail::GradientKey;
using detail::GradientKeyHash;
using detail::ParagraphKey;
using detail::ParagraphKeyHash;
using detail::TextureKey;
using detail::TextureKeyHash;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

using IntCache = detail::LruCache<int, int, std::hash<int>>;

std::string describe(const CacheStats& stats) {
    return "hits=" + std::to_string(stats.hits) + " misses=" + std::to_string(stats.misses) +
           " evictions=" + std::to_string(stats.evictions) +
           " trimmed=" + std::to_string(stats.trimmed) +
           " refused=" + std::to_string(stats.refused) +
           " entries=" + std::to_string(stats.entries) +
           " bytes=" + std::to_string(stats.bytes);
}

void count_budget() {
    IntCache cache({.max_entries = 3, .max_bytes = 1000});
    for (int key = 0; key < 3; ++key) {
        expect(cache.find(key, 1) == nullptr, "a cold key misses");
        expect(cache.insert(key, key * 10, 100, 1), "an entry within budget is kept");
    }
    // Touch 0 so 1 is now the least recently used.
    const auto* found = cache.find(0, 2);
    expect(found != nullptr && *found == 0, "a cached key hits with its value");
    expect(cache.insert(3, 30, 100, 2), "a fourth entry is kept");
    const auto stats = cache.stats();
    expect(stats.entries == 3 && stats.bytes == 300 && stats.evictions == 1,
           "the count budget evicts one entry", describe(stats));
    expect(!cache.contains(1) && cache.contains(0) && cache.contains(2) && cache.contains(3),
           "the least recently used entry is the one evicted");
    expect(stats.hits == 1 && stats.misses == 3, "lookups are counted", describe(stats));
}

void byte_budget() {
    IntCache cache({.max_entries = 100, .max_bytes = 250});
    cache.insert(1, 1, 100, 1);
    cache.insert(2, 2, 100, 1);
    cache.insert(3, 3, 100, 1);
    auto stats = cache.stats();
    expect(stats.entries == 2 && stats.bytes == 200 && stats.evictions == 1 &&
               !cache.contains(1),
           "the byte budget evicts the least recently used entry", describe(stats));

    expect(!cache.insert(4, 4, 251, 1), "an entry larger than the byte budget is refused");
    stats = cache.stats();
    expect(stats.refused == 1 && stats.entries == 2 && stats.bytes == 200 &&
               cache.contains(2) && cache.contains(3),
           "a refused entry evicts nothing", describe(stats));

    expect(cache.insert(5, 5, 250, 1), "an entry exactly the byte budget is kept");
    stats = cache.stats();
    expect(stats.entries == 1 && stats.bytes == 250 && cache.contains(5),
           "keeping it evicts everything else", describe(stats));

    cache.insert(5, 6, 10, 1);
    stats = cache.stats();
    const auto* replaced = cache.find(5, 1);
    expect(stats.entries == 1 && stats.bytes == 10 && replaced != nullptr && *replaced == 6,
           "re-inserting a key replaces its value and bytes", describe(stats));
}

void disabled() {
    for (const CacheBudget budget : {CacheBudget{0, 1000}, CacheBudget{10, 0}}) {
        IntCache cache(budget);
        expect(!cache.enabled(), "a zero budget turns the cache off");
        expect(cache.find(1, 1) == nullptr && !cache.insert(1, 1, 1, 1) &&
                   cache.find(1, 1) == nullptr,
               "a cache that is off keeps nothing");
        const auto stats = cache.stats();
        expect(stats.misses == 2 && stats.refused == 1 && stats.entries == 0,
               "a cache that is off counts its misses and refusals", describe(stats));
    }
}

void trims() {
    IntCache cache({.max_entries = 10, .max_bytes = 1000});
    cache.insert(1, 1, 10, 1);
    cache.insert(2, 2, 10, 2);
    cache.insert(3, 3, 10, 3);
    (void)cache.find(1, 3);
    expect(cache.trim_unused_since(3) == 1 && !cache.contains(2) && cache.contains(1) &&
               cache.contains(3),
           "a trim drops exactly the entries the frame did not use");
    auto stats = cache.stats();
    expect(stats.trimmed == 1 && stats.evictions == 0 && stats.bytes == 20,
           "a trim is counted apart from evictions", describe(stats));

    expect(cache.set_budget({.max_entries = 1, .max_bytes = 1000}) == 1 &&
               cache.contains(1) && !cache.contains(3),
           "lowering the budget drops the least recently used entries");
    stats = cache.stats();
    expect(stats.trimmed == 2 && stats.entries == 1, "the budget change counts as a trim",
           describe(stats));
    expect(cache.set_budget({.max_entries = 10, .max_bytes = 1000}) == 0,
           "raising the budget drops nothing");

    expect(cache.clear() == 1 && cache.stats().entries == 0 && cache.stats().bytes == 0 &&
               cache.stats().trimmed == 3,
           "clear drops everything and counts it");
}

void frame_counters() {
    IntCache cache({.max_entries = 10, .max_bytes = 1000});
    cache.begin_frame();
    (void)cache.find(1, 1);
    cache.insert(1, 1, 1, 1);
    (void)cache.find(1, 1);
    auto stats = cache.stats();
    expect(stats.frame_hits == 1 && stats.frame_misses == 1, "frame counters count the frame");
    cache.begin_frame();
    (void)cache.find(1, 2);
    (void)cache.find(1, 2);
    stats = cache.stats();
    expect(stats.frame_hits == 2 && stats.frame_misses == 0 && stats.hits == 3 &&
               stats.misses == 1,
           "begin_frame restarts only the frame counters", describe(stats));
    expect(hit_rate(stats) == 0.75 && frame_hit_rate(stats) == 1.0,
           "hit rates divide hits by lookups");
    expect(hit_rate(CacheStats{}) == 0.0 && frame_hit_rate(CacheStats{}) == 0.0,
           "hit rates are zero without lookups");
}

ParagraphKey paragraph_key() {
    return {
        .font_directory = "/fonts",
        .runs = {{.text = "Gain",
                  .style = {.font_family = "Jost", .font_weight = 500, .font_size = 13.0F,
                            .letter_spacing = 0.0F, .color = Color::hex(0xE5E7EB)}},
                 {.text = " dB", .style = {.underline = true}}},
    };
}

void paragraph_keys() {
    const ParagraphKeyHash hash;
    const auto base = paragraph_key();
    const auto copy = paragraph_key();
    expect(base == copy && hash(base) == hash(copy), "equal paragraph inputs make equal keys");

    const auto differs = [&](const char* what, auto edit) {
        auto changed = paragraph_key();
        edit(changed);
        expect(!(changed == base), std::string("a paragraph key differs by ") + what);
    };
    differs("text", [](ParagraphKey& key) { key.runs[0].text = "Gaim"; });
    differs("font family", [](ParagraphKey& key) { key.runs[0].style.font_family = "Inter"; });
    differs("weight", [](ParagraphKey& key) { key.runs[0].style.font_weight = 600; });
    differs("size", [](ParagraphKey& key) { key.runs[0].style.font_size = 13.5F; });
    differs("letter spacing sign",
            [](ParagraphKey& key) { key.runs[0].style.letter_spacing = -0.0F; });
    differs("colour", [](ParagraphKey& key) { key.runs[0].style.color.alpha = 0.5F; });
    differs("red", [](ParagraphKey& key) { key.runs[1].style.color.red = 0.5F; });
    differs("underline", [](ParagraphKey& key) { key.runs[1].style.underline = false; });
    differs("strikethrough", [](ParagraphKey& key) { key.runs[1].style.strikethrough = true; });
    differs("run count", [](ParagraphKey& key) { key.runs.pop_back(); });
    differs("run split", [](ParagraphKey& key) {
        key.runs[0].text = "Gain d";
        key.runs[1].text = "B";
    });
    differs("font directory", [](ParagraphKey& key) { key.font_directory = "/other"; });
    expect(detail::estimated_paragraph_bytes(base) > detail::estimated_paragraph_bytes({}),
           "a paragraph's estimated size grows with its runs");
}

GradientKey gradient_key() {
    return {
        .points = {0.0F, 0.0F, 10.0F, 20.0F},
        .colors = {Color::hex(0xFF0000), Color::hex(0x0000FF, 0.5F)},
        .positions = {0.0F, 1.0F},
        .repeating = false,
    };
}

void gradient_keys() {
    const GradientKeyHash hash;
    const auto base = gradient_key();
    expect(base == gradient_key() && hash(base) == hash(gradient_key()),
           "equal gradient inputs make equal keys");
    const auto differs = [&](const char* what, auto edit) {
        auto changed = gradient_key();
        edit(changed);
        expect(!(changed == base), std::string("a gradient key differs by ") + what);
    };
    for (std::size_t index = 0; index < 4; ++index) {
        differs("an end point", [index](GradientKey& key) { key.points[index] += 0.25F; });
    }
    differs("a colour's alpha", [](GradientKey& key) { key.colors[1].alpha = 0.25F; });
    differs("a colour", [](GradientKey& key) { key.colors[0].green = 0.1F; });
    differs("a position", [](GradientKey& key) { key.positions[1] = 0.9F; });
    differs("the tile mode", [](GradientKey& key) { key.repeating = true; });
    differs("a stop", [](GradientKey& key) {
        key.colors.push_back(Color::hex(0x00FF00));
        key.positions.push_back(1.0F);
    });
}

void texture_keys() {
    TextureKey a{.width = 4, .height = 2};
    a.digest[0] = 1;
    auto b = a;
    expect(a == b && TextureKeyHash{}(a) == TextureKeyHash{}(b),
           "equal texture sizes and digests make equal keys");
    b.digest[31] = 7;
    expect(!(a == b), "a texture key differs by its digest");
    b = a;
    b.width = 2;
    b.height = 4;
    expect(!(a == b), "a texture key differs by its shape");
}

void telemetry_json() {
    RenderTelemetry telemetry;
    telemetry.frame = 7;
    telemetry.full_repaint = false;
    telemetry.full_repaint_reason = "none \"quoted\"";
    telemetry.damage_area_pixels = 300;
    telemetry.frame_area_pixels = 1200;
    telemetry.damage_fraction = 0.25;
    telemetry.layer_count = 2;
    telemetry.paragraphs = {.hits = 3, .misses = 1, .frame_hits = 1, .entries = 4,
                            .bytes = 4096};
    telemetry.textures = {.bytes = 64};
    telemetry.atlas_bytes = 64;
    telemetry.gpu_time_available = true;
    telemetry.gpu_milliseconds = 1.5;
    telemetry.resize_settled = true;
    telemetry.trims = 2;
    telemetry.last_trim = CacheTrimReason::resize_settled;
    const auto json = to_json(telemetry);
    for (const char* expected : {
             "\"frame\":7", "\"full_repaint\":false",
             "\"full_repaint_reason\":\"none \\\"quoted\\\"\"",
             "\"damage_fraction\":0.25", "\"layer_count\":2",
             "\"paragraphs\":{\"hits\":3,\"misses\":1,\"hit_rate\":0.75,\"frame_hits\":1,"
             "\"frame_misses\":0,\"frame_hit_rate\":1,",
             "\"entries\":4,\"bytes\":4096}", "\"atlas_bytes\":64",
             "\"gpu_time_available\":true", "\"gpu_milliseconds\":1.5",
             "\"resize_settled\":true", "\"trims\":2", "\"last_trim\":\"resize_settled\"",
         }) {
        expect(json.find(expected) != std::string::npos,
               std::string("telemetry JSON holds ") + expected, json);
    }
    expect(json.front() == '{' && json.back() == '}', "telemetry JSON is one object");
    expect(std::string(to_string(CacheTrimReason::device_lost)) == "device_lost" &&
               std::string(to_string(CacheTrimReason::budget_changed)) == "budget_changed" &&
               std::string(to_string(CacheTrimReason::none)) == "none",
           "trim reasons name themselves");
    RenderTelemetry nonfinite;
    nonfinite.gpu_milliseconds = std::numeric_limits<double>::quiet_NaN();
    expect(to_json(nonfinite).find("\"gpu_milliseconds\":null") != std::string::npos,
           "a non-finite number is written as null");
}

void default_budgets() {
    const RenderCacheBudgets budgets;
    expect(budgets.paragraphs.max_entries > 0 && budgets.paragraphs.max_bytes > 0 &&
               budgets.gradients.max_entries > 0 && budgets.gradients.max_bytes > 0 &&
               budgets.textures.max_entries > 0 && budgets.textures.max_bytes > 0,
           "every cache is on by default");
}

}  // namespace

int main() {
    count_budget();
    byte_budget();
    disabled();
    trims();
    frame_counters();
    paragraph_keys();
    gradient_keys();
    texture_keys();
    telemetry_json();
    default_budgets();
    std::cout << "render cache " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
