#pragma once

#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <vector>

/// Deterministic offscreen capture harness for scene rendering tests.
///
/// Every capture renders on a fresh `SkiaDawnSurface` created with a null
/// native surface handle, so it never touches a window or a CAMetalLayer.
/// Pixels are premultiplied RGBA8, row-major, top row first, at the physical
/// size `round(width * scale)` by `round(height * scale)`.
///
/// Goldens are raw RGBA files (see `write_raw_rgba`). Relative golden paths
/// resolve against `default_golden_directory()`, which is
/// `graphics/tests/fixtures/capture/`. Setting `VELLUM_UPDATE_GOLDENS=1` makes
/// `compare_to_golden` in `GoldenMode::from_environment` rewrite the golden
/// from the capture instead of comparing; it is refused when `CI` is set.
/// Any update writes only under `golden_roots()`.
namespace vellum::graphics::capture_harness {

/// The outcome of a harness check, with a diagnostic on failure.
struct Verdict final {
    bool passed = false;
    std::string message;

    explicit operator bool() const noexcept { return passed; }
};

/// One offscreen capture and the evidence needed to judge it.
struct CaptureResult final {
    bool ok = false;
    std::string error;
    std::vector<std::uint8_t> rgba;
    std::uint32_t pixel_width = 0;
    std::uint32_t pixel_height = 0;
    GpuEvidence evidence;
    FrameStats frame_stats;
    /// The surface's telemetry right after the render.
    RenderTelemetry telemetry;
    CaptureStats stats;
    std::chrono::microseconds surface_creation{0};
    std::chrono::microseconds render{0};
    std::chrono::microseconds capture{0};
};

/// The first pixel at which two captures differ beyond a tolerance.
struct PixelDifference final {
    std::uint32_t x = 0;
    std::uint32_t y = 0;
    std::uint8_t expected[4]{};
    std::uint8_t actual[4]{};
};

/// A pixel comparison summary. A pixel differs when any channel differs by
/// more than the comparison's channel tolerance.
struct PixelComparison final {
    bool same_size = false;
    std::size_t pixel_count = 0;
    std::size_t differing_pixels = 0;
    int max_channel_delta = 0;
    std::optional<PixelDifference> first;
};

/// Registers the process's native Dawn bootstrap. Idempotent.
[[nodiscard]] bool prepare_device(std::string* error = nullptr);

/// Renders `scene` once on a fresh offscreen surface of `width` by `height`
/// logical pixels at `scale`, then reads it back and analyzes it.
[[nodiscard]] CaptureResult render_offscreen(
    const Scene& scene, std::uint32_t width, std::uint32_t height,
    float scale = 1.0F);

/// Compares two RGBA buffers of the given size.
[[nodiscard]] PixelComparison compare_pixels(
    std::span<const std::uint8_t> expected, std::uint32_t expected_width,
    std::uint32_t expected_height, std::span<const std::uint8_t> actual,
    std::uint32_t actual_width, std::uint32_t actual_height,
    int max_channel_delta = 0);

/// Requires two captures to be byte-identical, naming the first differing
/// pixel on failure.
[[nodiscard]] Verdict assert_identical(
    const CaptureResult& expected, const CaptureResult& actual);

/// Renders `scene` twice on fresh surfaces and requires byte-identical
/// RGBA. The captures are returned through the optional out parameters.
[[nodiscard]] Verdict assert_deterministic(
    const Scene& scene, std::uint32_t width, std::uint32_t height,
    float scale = 1.0F, CaptureResult* first = nullptr,
    CaptureResult* second = nullptr);

/// Requires a successful capture whose statistics pass the shared
/// `passes_content_floor` check.
[[nodiscard]] Verdict assert_content_floor(const CaptureResult& result);

enum class GoldenMode {
    /// Update when `VELLUM_UPDATE_GOLDENS=1`, otherwise compare. Updating
    /// fails when the `CI` environment variable is set and not empty.
    from_environment,
    compare,
    update,
};

/// True when `VELLUM_UPDATE_GOLDENS` is set to `1`.
[[nodiscard]] bool update_goldens_requested();

/// The directory relative golden paths resolve against.
[[nodiscard]] std::filesystem::path default_golden_directory();

/// The directories goldens may be written under: `default_golden_directory()`
/// and this test directory's build tree.
[[nodiscard]] std::vector<std::filesystem::path> golden_roots();

/// True when `path`, with symlinks and `..` resolved, lies strictly inside one
/// of `golden_roots()`.
[[nodiscard]] bool is_within_golden_roots(const std::filesystem::path& path);

/// Compares a capture to a raw RGBA golden. The capture passes when the
/// sizes match and at most `max_differing_fraction` of the pixels have a
/// channel that differs by more than `max_channel_delta`. In update mode the
/// golden is rewritten from the capture and the verdict passes, unless the
/// path lies outside `golden_roots()`, which fails without writing.
[[nodiscard]] Verdict compare_to_golden(
    const CaptureResult& result, const std::filesystem::path& golden,
    int max_channel_delta, double max_differing_fraction,
    GoldenMode mode = GoldenMode::from_environment);

/// Writes a raw RGBA golden: the eight bytes `VLMRGBA1`, the width and the
/// height as little-endian 32-bit values, then `width * height * 4` bytes.
[[nodiscard]] bool write_raw_rgba(
    const std::filesystem::path& path, std::uint32_t width,
    std::uint32_t height, std::span<const std::uint8_t> rgba,
    std::string* error = nullptr);

/// Reads a raw RGBA golden written by `write_raw_rgba`.
[[nodiscard]] bool read_raw_rgba(
    const std::filesystem::path& path, std::uint32_t& width,
    std::uint32_t& height, std::vector<std::uint8_t>& rgba,
    std::string* error = nullptr);

/// Scene builders for every renderable node kind. A new node kind adds its
/// builder here and an entry in `all`.
namespace fixtures {

struct NamedScene final {
    std::string name;
    Scene scene;
};

/// Background only. It must fail the content floor.
[[nodiscard]] Scene empty(float width, float height);
/// Rounded rectangles with a linear gradient fill and an outset shadow.
[[nodiscard]] Scene gradient_shadow_rectangle(float width, float height);
/// Attributed text runs in the packaged fonts.
[[nodiscard]] Scene text(float width, float height);
/// A vertex-coloured triangle mesh and a textured, clipped quad.
[[nodiscard]] Scene mesh(float width, float height);
/// The same pentagram filled with the nonzero and the evenodd rule, side by
/// side; only the evenodd one leaves its centre unfilled.
[[nodiscard]] Scene path_fill_rules(float width, float height);
/// A dashed polyline stroked with round caps and joins.
[[nodiscard]] Scene path_dashed_stroke(float width, float height);
/// A closed cubic curve with a linear gradient fill and a thin stroke.
[[nodiscard]] Scene path_cubic_gradient(float width, float height);
/// Rotated, scaled, faded and per-corner rounded rectangles: node transforms,
/// a group opacity layer, a folded opacity, and `corner_radii`.
[[nodiscard]] Scene compositing(float width, float height);
/// The version 1 node kinds above in one scene, so it can also be drawn at
/// `kDamageModelSceneVersion`. The compositing and path fixtures need versions
/// 2 and 3 and stay separate.
[[nodiscard]] Scene combined(float width, float height);
/// Each content fixture, named, excluding `empty`.
[[nodiscard]] std::vector<NamedScene> all(float width, float height);

}  // namespace fixtures

}  // namespace vellum::graphics::capture_harness
