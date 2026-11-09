#pragma once

// The Skia/Dawn renderer's cross-frame resource caches. Each one returns the
// object the renderer would otherwise build from the same exact inputs, so a
// hit draws the same pixels as a miss.

#include "render_cache.hpp"

#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <CommonCrypto/CommonDigest.h>

#include "include/core/SkImage.h"
#include "include/core/SkRefCnt.h"
#include "include/core/SkShader.h"
#include "modules/skparagraph/include/Paragraph.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <span>

namespace vellum::graphics::detail {

/// A laid-out paragraph and its metrics. Painting does not change a
/// paragraph, so one is shared by every draw of the same key.
struct CachedParagraph final {
    std::shared_ptr<skia::textlayout::Paragraph> paragraph;
    TextMetrics metrics;
};

/// The SHA-256 of `bytes`.
[[nodiscard]] inline TextureDigest sha256(std::span<const std::uint8_t> bytes) noexcept {
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    constexpr std::size_t chunk = std::numeric_limits<CC_LONG>::max() / 2U;
    for (std::size_t offset = 0; offset < bytes.size(); offset += chunk) {
        const std::size_t size = std::min(chunk, bytes.size() - offset);
        CC_SHA256_Update(&context, bytes.data() + offset, static_cast<CC_LONG>(size));
    }
    TextureDigest digest{};
    CC_SHA256_Final(digest.data(), &context);
    return digest;
}

struct SkiaResourceCaches final {
    LruCache<ParagraphKey, CachedParagraph, ParagraphKeyHash> paragraphs;
    LruCache<GradientKey, sk_sp<SkShader>, GradientKeyHash> gradients;
    LruCache<TextureKey, sk_sp<SkImage>, TextureKeyHash> textures;
    /// Counts every frame the renderer starts; entries are stamped with it.
    std::uint64_t frame = 0;

    explicit SkiaResourceCaches(const RenderCacheBudgets& budgets = {})
        : paragraphs(budgets.paragraphs),
          gradients(budgets.gradients),
          textures(budgets.textures) {}

    void begin_frame() {
        ++frame;
        paragraphs.begin_frame();
        gradients.begin_frame();
        textures.begin_frame();
    }

    /// Applies new budgets. Returns how many entries no longer fit.
    std::size_t set_budgets(const RenderCacheBudgets& budgets) {
        return paragraphs.set_budget(budgets.paragraphs) +
               gradients.set_budget(budgets.gradients) +
               textures.set_budget(budgets.textures);
    }

    [[nodiscard]] RenderCacheBudgets budgets() const {
        return {paragraphs.budget(), gradients.budget(), textures.budget()};
    }

    /// Drops every entry last used before `first_frame`. Returns how many.
    std::size_t trim_unused_since(std::uint64_t first_frame) {
        return paragraphs.trim_unused_since(first_frame) +
               gradients.trim_unused_since(first_frame) +
               textures.trim_unused_since(first_frame);
    }

    /// The per-frame counters of every cache.
    struct FrameState final {
        std::pair<std::uint64_t, std::uint64_t> paragraphs;
        std::pair<std::uint64_t, std::uint64_t> gradients;
        std::pair<std::uint64_t, std::uint64_t> textures;
    };
    [[nodiscard]] FrameState frame_state() const {
        return {paragraphs.frame_counters(), gradients.frame_counters(),
                textures.frame_counters()};
    }
    /// Restores the per-frame counters; the frame number stays monotonic.
    void restore(const FrameState& state) {
        paragraphs.restore_frame_counters(state.paragraphs);
        gradients.restore_frame_counters(state.gradients);
        textures.restore_frame_counters(state.textures);
    }

    std::size_t clear() { return paragraphs.clear() + gradients.clear() + textures.clear(); }
};

}  // namespace vellum::graphics::detail
