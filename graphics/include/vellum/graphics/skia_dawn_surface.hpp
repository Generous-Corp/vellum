#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::graphics {

struct GpuEvidence final {
    bool available = false;
    bool native_surface = false;
    bool fallback = false;
    std::string renderer;
    std::string backend;
    std::string adapter;
    std::string texture_format;
};

struct TextMetrics final {
    float width = 0.0F;
    float ascent = 0.0F;
    float descent = 0.0F;
    float baseline = 0.0F;
};

/// Skia Graphite rendering on a Dawn device.
///
/// `native_surface_handle` is a CAMetalLayer on macOS. Passing nullptr creates
/// an offscreen GPU target for deterministic capture. A requested native
/// surface never silently falls back to the offscreen target.
class SkiaDawnSurface final {
public:
    struct Config final {
        std::uint32_t width = 800;
        std::uint32_t height = 600;
        float scale = 1.0F;
        void* native_surface_handle = nullptr;
        bool vsync = true;
        /// Optional override for packaged font assets. An empty value resolves
        /// the configured install data directory relative to the GPU library,
        /// or the native app's bundle-local `Resources/vellum/fonts` directory.
        std::string font_directory;
    };

    static std::unique_ptr<SkiaDawnSurface> create(
        const Config& config, std::string* error = nullptr);
    [[nodiscard]] static bool measure_text(
        const std::vector<TextRun>& runs,
        TextMetrics& metrics,
        std::string_view font_directory = {},
        std::string* error = nullptr);

    ~SkiaDawnSurface();
    SkiaDawnSurface(SkiaDawnSurface&&) noexcept;
    SkiaDawnSurface& operator=(SkiaDawnSurface&&) noexcept;
    SkiaDawnSurface(const SkiaDawnSurface&) = delete;
    SkiaDawnSurface& operator=(const SkiaDawnSurface&) = delete;

    [[nodiscard]] bool render(const Scene& scene, std::string* error = nullptr);
    [[nodiscard]] bool resize(
        std::uint32_t width, std::uint32_t height, float scale,
        std::string* error = nullptr);
    [[nodiscard]] bool capture_png(
        std::vector<std::uint8_t>& png, std::string* error = nullptr);
    [[nodiscard]] bool capture_rgba(
        std::vector<std::uint8_t>& rgba,
        std::uint32_t& pixel_width,
        std::uint32_t& pixel_height,
        std::string* error = nullptr);
    [[nodiscard]] const GpuEvidence& evidence() const noexcept;

private:
    class Impl;
    explicit SkiaDawnSurface(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::graphics
