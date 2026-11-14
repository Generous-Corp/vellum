#include "render_cache.hpp"

#include <vellum/graphics/render_telemetry.hpp>

#include <cctype>
#include <clocale>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <locale>
#include <stdexcept>
#include <string>
#include <string_view>

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

// A key that counts its live copies, to prove the cache stores each key once.
struct CountedKey final {
    static inline int live = 0;
    int value = 0;
    explicit CountedKey(int v) : value(v) { ++live; }
    CountedKey(const CountedKey& other) : value(other.value) { ++live; }
    CountedKey(CountedKey&& other) noexcept : value(other.value) { ++live; }
    CountedKey& operator=(const CountedKey&) = default;
    CountedKey& operator=(CountedKey&&) noexcept = default;
    ~CountedKey() { --live; }
    friend bool operator==(const CountedKey& a, const CountedKey& b) noexcept {
        return a.value == b.value;
    }
};
struct CountedKeyHash final {
    std::size_t operator()(const CountedKey& key) const noexcept {
        return std::hash<int>{}(key.value);
    }
};

void keys_are_stored_once() {
    {
        detail::LruCache<CountedKey, int, CountedKeyHash> cache(
            {.max_entries = 64, .max_bytes = 1U << 20U});
        for (int key = 0; key < 40; ++key) cache.insert(CountedKey{key}, key, 1, 1);
        expect(CountedKey::live == 40, "a cache holds one copy of each key",
               "live keys " + std::to_string(CountedKey::live));
        // Rehashing must not strand the list's pointers into the index.
        for (int key = 0; key < 40; ++key) {
            const auto* found = cache.find(CountedKey{key}, 2);
            if (found == nullptr || *found != key) {
                expect(false, "every key is found after the index rehashes",
                       std::to_string(key));
                break;
            }
        }
        cache.insert(CountedKey{7}, 70, 1, 2);
        expect(CountedKey::live == 40 && *cache.find(CountedKey{7}, 2) == 70,
               "re-inserting a key keeps one copy");
        cache.set_budget({.max_entries = 10, .max_bytes = 1U << 20U});
        expect(CountedKey::live == 10 && cache.stats().entries == 10 &&
                   cache.contains(CountedKey{35}),
               "evicting entries frees their keys",
               "live keys " + std::to_string(CountedKey::live));
        // Only a hit stamps key 35 with frame 3; nothing is re-inserted.
        expect(cache.find(CountedKey{35}, 3) != nullptr, "a hit at frame 3");
        cache.trim_unused_since(3);
        expect(CountedKey::live == 1 && cache.contains(CountedKey{35}) &&
                   cache.stats().entries == 1,
               "a trim keeps only the entry a hit refreshed, and frees the other keys",
               "live keys " + std::to_string(CountedKey::live));
    }
    expect(CountedKey::live == 0, "destroying the cache frees every key");
}

// A value whose copy and move throw on demand, and a hash that throws on a
// chosen call, to drive insert's two allocation points.
struct FragileValue final {
    static inline bool fail = false;
    int value = 0;
    explicit FragileValue(int v) : value(v) {}
    FragileValue(const FragileValue& other) : value(other.value) {
        if (fail) throw std::runtime_error("copy");
    }
    FragileValue(FragileValue&& other) : value(other.value) {
        if (fail) throw std::runtime_error("move");
    }
    FragileValue& operator=(const FragileValue&) = default;
    FragileValue& operator=(FragileValue&&) = default;
};
struct FragileHash final {
    static inline int calls = 0;
    static inline int throw_on_call = -1;
    std::size_t operator()(int key) const {
        if (++calls == throw_on_call) throw std::runtime_error("hash");
        return std::hash<int>{}(key);
    }
};

// An insert that throws leaves the cache as it was: no index entry without a
// list entry, no bytes charged, and every later lookup safe.
void insert_is_exception_safe() {
    using Fragile = detail::LruCache<int, FragileValue, FragileHash>;
    const auto consistent = [](Fragile& cache, int key, const char* what) {
        if (cache.contains(key)) {
            // A stale index entry: a find would splice an invalid iterator.
            expect(false, what, "the index kept the key after a throw");
            return;
        }
        const auto stats = cache.stats();
        expect(stats.entries == 1 && stats.bytes == 10 && cache.find(key, 2) == nullptr &&
                   cache.find(1, 2) != nullptr && cache.find(1, 2)->value == 1,
               what, describe(stats));
        expect(cache.insert(key, FragileValue{key}, 10, 3) && cache.find(key, 3) != nullptr &&
                   cache.stats().entries == 2,
               std::string(what) + ": a later insert of the key works");
    };
    {
        Fragile cache({.max_entries = 8, .max_bytes = 1000});
        cache.insert(1, FragileValue{1}, 10, 1);
        bool threw = false;
        FragileValue::fail = true;
        try {
            cache.insert(2, FragileValue{2}, 10, 1);
        } catch (const std::runtime_error&) {
            threw = true;
        }
        FragileValue::fail = false;
        expect(threw, "a throwing value move escapes insert");
        consistent(cache, 2, "a throwing value move leaves the cache consistent");
    }
    {
        Fragile cache({.max_entries = 8, .max_bytes = 1000});
        cache.insert(1, FragileValue{1}, 10, 1);
        // insert hashes once to look for an existing entry, then to index.
        FragileHash::throw_on_call = FragileHash::calls + 2;
        bool threw = false;
        try {
            cache.insert(2, FragileValue{2}, 10, 1);
        } catch (const std::runtime_error&) {
            threw = true;
        }
        FragileHash::throw_on_call = -1;
        expect(threw, "a throwing index insertion escapes insert");
        consistent(cache, 2, "a throwing index insertion leaves the cache consistent");
    }
}

// A budget is in bytes of memory the cache owns, so the key's own strings and
// stop arrays count, not just the resource built from it.
void budgets_count_owned_key_memory() {
    GradientKey wide;
    wide.points = {0.0F, 0.0F, 100.0F, 0.0F};
    constexpr std::size_t stops = 50000;
    for (std::size_t index = 0; index < stops; ++index) {
        wide.colors.push_back(Color::rgba(0.5F, 0.25F, 0.125F, 1.0F));
        wide.positions.push_back(static_cast<float>(index) / static_cast<float>(stops - 1U));
    }
    const std::size_t stop_arrays =
        wide.colors.size() * sizeof(Color) + wide.positions.size() * sizeof(float);
    expect(detail::key_bytes(wide) >= stop_arrays &&
               detail::gradient_bytes(wide) >= 2U * stop_arrays,
           "a gradient charges its key's stops and the shader's copy",
           std::to_string(detail::gradient_bytes(wide)) + " for " +
               std::to_string(stop_arrays) + " bytes of stops");
    detail::LruCache<GradientKey, int, GradientKeyHash> gradients(
        {.max_entries = 8, .max_bytes = 2U * stop_arrays});
    expect(!gradients.insert(wide, 1, detail::gradient_bytes(wide), 1),
           "a gradient whose owned stops exceed the byte budget is refused");

    const std::string family(200000, 'F');
    const std::string directory(100000, 'D');
    ParagraphKey styled{.font_directory = directory,
                        .runs = {{.text = "Hi", .style = {.font_family = family}},
                                 {.text = "!", .style = {.font_family = family}}}};
    const std::size_t strings = directory.size() + 2U * family.size() + 3U;
    expect(detail::key_bytes(styled) >= strings &&
               detail::estimated_paragraph_bytes(styled) >= strings + 2U * family.size(),
           "a paragraph charges its key's strings and Skia's copy of each family",
           std::to_string(detail::estimated_paragraph_bytes(styled)) + " for " +
               std::to_string(strings) + " bytes of strings");
    detail::LruCache<ParagraphKey, int, ParagraphKeyHash> paragraphs(
        {.max_entries = 8, .max_bytes = strings});
    expect(!paragraphs.insert(styled, 1, detail::estimated_paragraph_bytes(styled), 1),
           "a paragraph whose owned strings exceed the byte budget is refused");
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
    differs("the gradient kind", [](GradientKey& key) { key.kind = GradientKind::radial; });
    differs("the spread", [](GradientKey& key) { key.spread = GradientSpread::reflect; });
    differs("the interpolation", [](GradientKey& key) {
        key.interpolation = GradientInterpolation::linear_srgb;
    });
    differs("the colour space", [](GradientKey& key) { key.color_space = "linear-srgb"; });
    differs("the repeat length", [](GradientKey& key) { key.repeat_length = 12.0F; });
    differs("the centre", [](GradientKey& key) { key.geometry[0] = 0.25F; });
    differs("the radii", [](GradientKey& key) {
        key.geometry[2] = 0.25F;
        key.geometry[3] = 0.75F;
    });
    differs("the focal point", [](GradientKey& key) {
        key.geometry[4] = 0.25F;
        key.geometry[5] = 0.75F;
    });
    differs("the conic start angle", [](GradientKey& key) { key.angle_degrees = 30.0F; });
    differs("the conic end angle", [](GradientKey& key) { key.end_angle_degrees = 270.0F; });
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

// A minimal strict JSON checker: objects, strings, true/false/null and numbers
// in JSON's grammar. A comma inside a number breaks the object grammar.
class JsonChecker final {
public:
    explicit JsonChecker(std::string_view text) : text_(text) {}
    bool valid() {
        skip();
        if (!value()) return false;
        skip();
        return at_ == text_.size();
    }

private:
    bool value() {
        if (at_ >= text_.size()) return false;
        const char c = text_[at_];
        if (c == '{') return object();
        if (c == '"') return string();
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) return number();
        return word("true") || word("false") || word("null");
    }
    bool object() {
        ++at_;
        skip();
        if (peek('}')) return ++at_, true;
        for (;;) {
            skip();
            if (!string()) return false;
            skip();
            if (!peek(':')) return false;
            ++at_;
            skip();
            if (!value()) return false;
            skip();
            if (peek('}')) return ++at_, true;
            if (!peek(',')) return false;
            ++at_;
        }
    }
    bool string() {
        if (!peek('"')) return false;
        for (++at_; at_ < text_.size(); ++at_) {
            if (text_[at_] == '\\') {
                ++at_;
            } else if (text_[at_] == '"') {
                ++at_;
                return true;
            }
        }
        return false;
    }
    bool number() {
        const auto digits = [this] {
            const auto start = at_;
            while (at_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[at_]))) {
                ++at_;
            }
            return at_ > start;
        };
        if (peek('-')) ++at_;
        if (!digits()) return false;
        if (peek('.')) {
            ++at_;
            if (!digits()) return false;
        }
        if (peek('e') || peek('E')) {
            ++at_;
            if (peek('+') || peek('-')) ++at_;
            if (!digits()) return false;
        }
        return true;
    }
    bool word(std::string_view literal) {
        if (text_.substr(at_, literal.size()) != literal) return false;
        at_ += literal.size();
        return true;
    }
    bool peek(char c) const { return at_ < text_.size() && text_[at_] == c; }
    void skip() {
        while (at_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[at_]))) ++at_;
    }
    std::string_view text_;
    std::size_t at_ = 0;
};

// Groups thousands with '.' and writes ',' as the decimal point, like de_DE.
struct CommaDecimal final : std::numpunct<char> {
    char do_decimal_point() const override { return ','; }
    char do_thousands_sep() const override { return '.'; }
    std::string do_grouping() const override { return "\3"; }
};

// A host may switch the process to a comma-decimal locale; the telemetry JSON
// must not follow it.
void telemetry_json_ignores_the_locale() {
    RenderTelemetry telemetry;
    telemetry.frame = 1234567;
    telemetry.damage_area_pixels = 64000;
    telemetry.frame_area_pixels = 256000;
    telemetry.damage_fraction = 0.25;
    telemetry.paragraphs = {.hits = 3000, .misses = 1000, .frame_hits = 1, .frame_misses = 3,
                            .entries = 4, .bytes = 4096000};
    telemetry.atlas_bytes = 1048576;
    telemetry.gpu_time_available = true;
    telemetry.gpu_milliseconds = 1.5;
    telemetry.cpu_milliseconds = 12345.5;
    const auto classic = to_json(telemetry);
    expect(JsonChecker(classic).valid(), "telemetry JSON parses in the classic locale", classic);

    const std::string saved_c = std::setlocale(LC_ALL, nullptr);
    const auto saved_global = std::locale();
    const char* comma_c = nullptr;
    for (const char* name : {"de_DE.UTF-8", "de_DE.utf8", "de_DE"}) {
        if (std::setlocale(LC_ALL, name) != nullptr) {
            comma_c = name;
            break;
        }
    }
    std::locale::global(std::locale(std::locale::classic(), new CommaDecimal));
    const bool c_is_comma = std::localeconv()->decimal_point[0] == ',';
    const auto localized = to_json(telemetry);
    std::locale::global(saved_global);
    std::setlocale(LC_ALL, saved_c.c_str());

    std::cout << "NOTE C locale for the comma test: "
              << (comma_c != nullptr ? comma_c : "none available")
              << (c_is_comma ? " (comma decimal)" : " (dot decimal)") << '\n';
    expect(JsonChecker(localized).valid(), "telemetry JSON parses under a comma-decimal locale",
           localized);
    expect(localized == classic, "telemetry JSON is the same under every locale", localized);
    expect(localized.find("\"damage_fraction\":0.25") != std::string::npos &&
               localized.find("\"frame\":1234567,") != std::string::npos &&
               localized.find("\"cpu_milliseconds\":12345.5") != std::string::npos,
           "numbers keep a dot and no grouping", localized);
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
    keys_are_stored_once();
    insert_is_exception_safe();
    budgets_count_owned_key_memory();
    paragraph_keys();
    gradient_keys();
    texture_keys();
    telemetry_json();
    telemetry_json_ignores_the_locale();
    default_budgets();
    std::cout << "render cache " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
