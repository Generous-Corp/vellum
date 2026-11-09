#pragma once

// Least-recently-used caches and the exact keys the Skia/Dawn renderer looks
// its resources up by. Nothing here depends on Skia, so budgets, eviction and
// key equality are tested without a GPU.

#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/scene.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <list>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vellum::graphics::detail {

/// A cache with a count and a byte budget that evicts its least recently
/// used entry first. `find` counts a hit or a miss; `insert` follows a miss.
template <class Key, class Value, class Hash>
class LruCache final {
public:
    explicit LruCache(CacheBudget budget = {}) : budget_(budget) {}

    [[nodiscard]] bool enabled() const noexcept {
        return budget_.max_entries > 0 && budget_.max_bytes > 0;
    }

    /// Replaces the budget, dropping the least recently used entries that no
    /// longer fit. Returns how many were dropped; they count as trimmed.
    std::size_t set_budget(CacheBudget budget) {
        budget_ = budget;
        std::size_t dropped = 0;
        while (!order_.empty() && over_budget()) {
            erase(std::prev(order_.end()));
            ++dropped;
        }
        stats_.trimmed += dropped;
        return dropped;
    }

    [[nodiscard]] const CacheBudget& budget() const noexcept { return budget_; }

    /// Starts a frame: the `frame_` counters restart from zero.
    void begin_frame() noexcept {
        stats_.frame_hits = 0;
        stats_.frame_misses = 0;
    }

    /// The cached value for `key`, made the most recently used and stamped
    /// with `frame`, or nullptr after counting a miss.
    Value* find(const Key& key, std::uint64_t frame) {
        const auto found = index_.find(key);
        if (found == index_.end()) {
            ++stats_.misses;
            ++stats_.frame_misses;
            return nullptr;
        }
        ++stats_.hits;
        ++stats_.frame_hits;
        const Iterator entry = found->second;
        order_.splice(order_.begin(), order_, entry);
        entry->last_used = frame;
        return &entry->value;
    }

    /// Caches `value` as the most recently used entry, evicting least
    /// recently used entries until both budgets hold. An entry larger than
    /// the byte budget, or any entry while the cache is off, is refused.
    /// Returns whether the value was kept.
    bool insert(Key key, Value value, std::size_t bytes, std::uint64_t frame) {
        if (!enabled() || bytes > budget_.max_bytes) {
            ++stats_.refused;
            return false;
        }
        if (const auto existing = index_.find(key); existing != index_.end()) {
            erase(existing->second);
        }
        // The key lives only in the index; its list entry points at it. An
        // unordered_map node never moves, so the pointer survives rehashing.
        const auto slot = index_.emplace(std::move(key), Iterator{}).first;
        order_.push_front(Entry{&slot->first, std::move(value), bytes, frame});
        slot->second = order_.begin();
        bytes_ += bytes;
        while (over_budget()) {
            erase(std::prev(order_.end()));
            ++stats_.evictions;
        }
        return true;
    }

    /// Drops every entry last used before `frame`. Returns how many.
    std::size_t trim_unused_since(std::uint64_t frame) {
        std::size_t dropped = 0;
        for (auto entry = order_.begin(); entry != order_.end();) {
            const auto next = std::next(entry);
            if (entry->last_used < frame) {
                erase(entry);
                ++dropped;
            }
            entry = next;
        }
        stats_.trimmed += dropped;
        return dropped;
    }

    /// Drops every entry. Returns how many.
    std::size_t clear() {
        const std::size_t dropped = order_.size();
        index_.clear();
        order_.clear();
        bytes_ = 0;
        stats_.trimmed += dropped;
        return dropped;
    }

    [[nodiscard]] CacheStats stats() const noexcept {
        CacheStats stats = stats_;
        stats.entries = order_.size();
        stats.bytes = bytes_;
        return stats;
    }

    [[nodiscard]] bool contains(const Key& key) const { return index_.count(key) != 0U; }

    /// Drops `key` if it is cached; it counts as trimmed. Returns whether it was.
    bool erase(const Key& key) {
        const auto found = index_.find(key);
        if (found == index_.end()) return false;
        erase(found->second);
        ++stats_.trimmed;
        return true;
    }

    /// The frame counters, for restoring after a draw that is not a frame.
    [[nodiscard]] std::pair<std::uint64_t, std::uint64_t> frame_counters() const noexcept {
        return {stats_.frame_hits, stats_.frame_misses};
    }
    void restore_frame_counters(std::pair<std::uint64_t, std::uint64_t> counters) noexcept {
        stats_.frame_hits = counters.first;
        stats_.frame_misses = counters.second;
    }

private:
    struct Entry final {
        const Key* key = nullptr;
        Value value;
        std::size_t bytes = 0;
        std::uint64_t last_used = 0;
    };
    using Iterator = typename std::list<Entry>::iterator;

    [[nodiscard]] bool over_budget() const noexcept {
        return order_.size() > budget_.max_entries || bytes_ > budget_.max_bytes;
    }

    void erase(Iterator entry) {
        bytes_ -= entry->bytes;
        // Unlink the list entry before its key, which the entry points into.
        const auto slot = index_.find(*entry->key);
        order_.erase(entry);
        index_.erase(slot);
    }

    CacheBudget budget_;
    std::list<Entry> order_;
    std::unordered_map<Key, Iterator, Hash> index_;
    std::size_t bytes_ = 0;
    CacheStats stats_;
};

// Keys compare floats by their bits, so a key matches only an input that
// renders identically: -0 and 0 differ, and a NaN matches only itself.
inline bool same_bits(float a, float b) noexcept {
    return std::memcmp(&a, &b, sizeof(float)) == 0;
}

inline bool same_bits(const Color& a, const Color& b) noexcept {
    return std::memcmp(&a, &b, sizeof(Color)) == 0;
}

class KeyHasher final {
public:
    void add_bytes(const void* data, std::size_t size) noexcept {
        const auto* bytes = static_cast<const unsigned char*>(data);
        for (std::size_t index = 0; index < size; ++index) {
            hash_ = (hash_ ^ bytes[index]) * 0x100000001B3ULL;
        }
    }
    void add(std::string_view text) noexcept {
        add_value(text.size());
        add_bytes(text.data(), text.size());
    }
    template <class T>
    void add_value(const T& value) noexcept {
        add_bytes(&value, sizeof(T));
    }
    [[nodiscard]] std::size_t value() const noexcept { return static_cast<std::size_t>(hash_); }

private:
    std::uint64_t hash_ = 0xCBF29CE484222325ULL;
};

// A new TextStyle or TextRun field changes what a paragraph draws: compare and
// hash it below, then add it to these mirrors. The mirrors share the standard
// library's layout, so the check holds on every toolchain.
struct TextStyleFields final {
    std::string font_family;
    int font_weight;
    float font_size;
    float letter_spacing;
    Color color;
    bool underline;
    bool strikethrough;
};
struct TextRunFields final {
    std::string text;
    TextStyleFields style;
};
static_assert(sizeof(TextStyle) == sizeof(TextStyleFields) &&
                  sizeof(TextRun) == sizeof(TextRunFields),
              "TextStyle changed: update same_style and ParagraphKeyHash");
static_assert(sizeof(Color) == 4 * sizeof(float), "Color changed: update same_bits");

inline bool same_style(const TextStyle& a, const TextStyle& b) noexcept {
    return a.font_family == b.font_family && a.font_weight == b.font_weight &&
           same_bits(a.font_size, b.font_size) &&
           same_bits(a.letter_spacing, b.letter_spacing) && same_bits(a.color, b.color) &&
           a.underline == b.underline && a.strikethrough == b.strikethrough;
}

/// Everything a laid-out paragraph depends on: the font directory and every
/// run with the colour it is drawn in.
struct ParagraphKey final {
    std::string font_directory;
    std::vector<TextRun> runs;

    friend bool operator==(const ParagraphKey& a, const ParagraphKey& b) noexcept {
        if (a.font_directory != b.font_directory || a.runs.size() != b.runs.size()) {
            return false;
        }
        for (std::size_t index = 0; index < a.runs.size(); ++index) {
            if (a.runs[index].text != b.runs[index].text ||
                !same_style(a.runs[index].style, b.runs[index].style)) {
                return false;
            }
        }
        return true;
    }
};

struct ParagraphKeyHash final {
    std::size_t operator()(const ParagraphKey& key) const noexcept {
        KeyHasher hasher;
        hasher.add(key.font_directory);
        hasher.add_value(key.runs.size());
        for (const auto& run : key.runs) {
            hasher.add(run.text);
            hasher.add(run.style.font_family);
            hasher.add_value(run.style.font_weight);
            hasher.add_value(run.style.font_size);
            hasher.add_value(run.style.letter_spacing);
            hasher.add_value(run.style.color);
            hasher.add_value(run.style.underline);
            hasher.add_value(run.style.strikethrough);
        }
        return hasher.value();
    }
};

/// The memory a cached paragraph key owns: the key itself, its font
/// directory, and every run with its text and font family.
[[nodiscard]] inline std::size_t key_bytes(const ParagraphKey& key) noexcept {
    std::size_t bytes = sizeof(ParagraphKey) + key.font_directory.size();
    for (const auto& run : key.runs) {
        bytes += sizeof(TextRun) + run.text.size() + run.style.font_family.size();
    }
    return bytes;
}

/// An estimate of a cached paragraph's memory: its key plus the laid-out
/// paragraph, which Skia does not report. Shaping keeps glyphs, positions and
/// clusters per code unit, its own copy of the text and of every run's font
/// family, and a fixed overhead per run and per paragraph.
[[nodiscard]] inline std::size_t estimated_paragraph_bytes(const ParagraphKey& key) noexcept {
    std::size_t bytes = key_bytes(key) + 1024U;
    for (const auto& run : key.runs) {
        bytes += 256U + run.text.size() * 49U + run.style.font_family.size();
    }
    return bytes;
}

/// The exact inputs of one linear-gradient shader: the two end points, the
/// colours after any folded opacity, the positions and the tile mode.
struct GradientKey final {
    std::array<float, 4> points{};
    std::vector<Color> colors;
    std::vector<float> positions;
    bool repeating = false;

    friend bool operator==(const GradientKey& a, const GradientKey& b) noexcept {
        if (a.repeating != b.repeating || a.colors.size() != b.colors.size() ||
            a.positions.size() != b.positions.size() ||
            std::memcmp(a.points.data(), b.points.data(), sizeof(a.points)) != 0) {
            return false;
        }
        for (std::size_t index = 0; index < a.colors.size(); ++index) {
            if (!same_bits(a.colors[index], b.colors[index])) return false;
        }
        for (std::size_t index = 0; index < a.positions.size(); ++index) {
            if (!same_bits(a.positions[index], b.positions[index])) return false;
        }
        return true;
    }
};

struct GradientKeyHash final {
    std::size_t operator()(const GradientKey& key) const noexcept {
        KeyHasher hasher;
        hasher.add_value(key.points);
        hasher.add_value(key.repeating);
        hasher.add_value(key.colors.size());
        hasher.add_bytes(key.colors.data(), key.colors.size() * sizeof(Color));
        hasher.add_value(key.positions.size());
        hasher.add_bytes(key.positions.data(), key.positions.size() * sizeof(float));
        return hasher.value();
    }
};

/// The memory a cached gradient key owns: the key and its stop arrays.
[[nodiscard]] inline std::size_t key_bytes(const GradientKey& key) noexcept {
    return sizeof(GradientKey) + key.colors.size() * sizeof(Color) +
           key.positions.size() * sizeof(float);
}

/// A cached gradient's memory: its key plus the shader, which keeps its own
/// copy of every stop's colour and position.
[[nodiscard]] inline std::size_t gradient_bytes(const GradientKey& key) noexcept {
    return key_bytes(key) + 128U + key.colors.size() * sizeof(Color) +
           key.positions.size() * sizeof(float);
}

using TextureDigest = std::array<std::uint8_t, 32>;

/// A texture by content: its size and the SHA-256 of its texels.
struct TextureKey final {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    TextureDigest digest{};

    friend bool operator==(const TextureKey& a, const TextureKey& b) noexcept {
        return a.width == b.width && a.height == b.height && a.digest == b.digest;
    }
};

struct TextureKeyHash final {
    std::size_t operator()(const TextureKey& key) const noexcept {
        std::uint64_t value = 0;
        std::memcpy(&value, key.digest.data(), sizeof(value));
        value ^= (static_cast<std::uint64_t>(key.width) << 32U) ^ key.height;
        return static_cast<std::size_t>(value ^ (value >> 32U));
    }
};

}  // namespace vellum::graphics::detail
