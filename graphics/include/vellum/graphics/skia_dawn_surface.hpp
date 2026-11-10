#pragma once

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
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

/// Why a frame repainted the whole retained surface instead of its damage.
enum class FullRepaintReason {
    /// The frame repainted only its damage.
    none,
    /// `render(scene)` always repaints the whole frame.
    full_render,
    /// No frame has been drawn into the retained surface yet.
    first_frame,
    /// `resize` recreated the retained surface.
    surface_recreated,
    /// The previous frame failed part way, so the retained surface is stale.
    previous_frame_failed,
    /// The scene's `scene_version` changed.
    scene_version,
    /// The scene's size or background changed.
    frame_changed,
    /// The host sent a `reload` invalidation.
    reload,
    /// The damage covered the whole frame: an invalidation without a node id
    /// or with an unknown one.
    full_damage,
    /// The damage reached the policy's full-repaint fraction.
    damage_threshold,
    /// The scene's frame does not cover the whole surface, so damage clipped
    /// to it could leave stale pixels.
    frame_mismatch,
    /// The scene's version is newer than `kDamageModelSceneVersion`.
    damage_model_version,
    /// A caller-supplied damage region was computed for a different frame
    /// size or scale than the surface's.
    damage_frame_mismatch,
};

[[nodiscard]] std::string_view to_string(FullRepaintReason reason) noexcept;

/// Telemetry for the most recent frame drawn into the retained surface.
struct FrameStats final {
    /// Frames drawn since the surface was created, counting this one.
    std::uint64_t frame = 0;
    bool full_repaint = false;
    FullRepaintReason full_repaint_reason = FullRepaintReason::none;
    /// Damaged device pixels after clipping to the surface; the whole surface
    /// for a full repaint.
    std::int64_t damage_area_pixels = 0;
    std::int64_t frame_area_pixels = 0;
    double damage_fraction = 0.0;
    std::size_t damage_rects_before_coalescing = 0;
    std::size_t damage_rects_after_coalescing = 0;
    std::size_t paint_commands_total = 0;
    /// Distinct paint commands drawn because their paint extent met the damage.
    std::size_t paint_commands_recorded = 0;
    /// Paint command draws summed over every damage rectangle.
    std::size_t paint_command_draws = 0;
    /// The retained image was drawn into a native swapchain texture and presented.
    bool presented = false;
    /// CPU time spent recording and submitting the frame, including
    /// `present_wait_milliseconds`.
    double cpu_milliseconds = 0.0;
    /// CPU time spent waiting for the native swapchain texture.
    double present_wait_milliseconds = 0.0;
    /// GPU execution time of the frame's command buffer, once it has finished
    /// and when the device supports timestamp queries.
    bool gpu_time_available = false;
    double gpu_milliseconds = 0.0;
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
///
/// Every frame is drawn into a retained offscreen surface the size of the
/// target in device pixels. A native surface presents by drawing the retained
/// image into the swapchain texture; captures read the retained surface back,
/// through the swapchain when there is one.
///
/// Shaped text paragraphs, gradient shaders, and the textures uploaded from
/// mesh textures and image assets are cached across frames under
/// `RenderCacheBudgets`. Each cache is keyed on every input that affects its
/// pixels, so output never depends on what is cached, given that mesh
/// textures and image assets are not mutated once a scene references them. A resize that has settled drops the entries no frame has used
/// since the resize, and a device loss drops everything.
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

    /// Decodes a PNG into an `ImageAsset` named by the SHA-256 of `encoded`,
    /// the DesignIR content-hash convention. When `expected_content_hash` is
    /// not empty it must name the same digest, bare or prefixed, or decoding
    /// fails, so a mislabelled asset never reaches a scene. Texels are taken
    /// as stored, without colour management. Input over
    /// `kMaxEncodedImageBytes`, and a header whose dimensions do not fit
    /// `kMaxImageDimension` and `kMaxImageBytes`, are refused before the bytes
    /// are hashed or a codec is built.
    [[nodiscard]] static std::shared_ptr<const ImageAsset> decode_image_asset(
        std::span<const std::uint8_t> encoded, std::string_view expected_content_hash = {},
        std::string* error = nullptr);

    ~SkiaDawnSurface();
    SkiaDawnSurface(SkiaDawnSurface&&) noexcept;
    SkiaDawnSurface& operator=(SkiaDawnSurface&&) noexcept;
    SkiaDawnSurface(const SkiaDawnSurface&) = delete;
    SkiaDawnSurface& operator=(const SkiaDawnSurface&) = delete;

    /// Draws `scene`. A scene whose version fails `scene_version_is_supported`
    /// or that contains a node kind outside `is_known_kind` is refused before
    /// any drawing; the surface stays usable and a capture still returns the
    /// last frame that rendered successfully.
    [[nodiscard]] bool render(const Scene& scene, std::string* error = nullptr);

    /// Draws `scene`, repainting only what changed since the previous frame
    /// plus anything named by `invalidate`. The surface's `DamageTracker`
    /// diffs the scenes; within each damage rectangle the background is
    /// cleared and only paint commands whose paint extent meets it are drawn,
    /// leaving the rest of the retained image untouched. The first frame, a
    /// resize, a failed previous frame, a scene version, size or background
    /// change, a `reload`, whole-frame damage and a scene version newer than
    /// `kDamageModelSceneVersion` repaint everything. The output is
    /// byte-identical to `render(scene)`.
    [[nodiscard]] bool render_incremental(const Scene& scene, std::string* error = nullptr);

    /// Draws `scene`, repainting `damage` (device pixels, clipped to the
    /// surface) plus any host invalidations pending since the last frame,
    /// under the same full-repaint rules as `render_incremental`. An empty
    /// region (`DamageRegion{}`) with nothing pending presents the retained
    /// image unchanged. A region whose frame differs from
    /// `frame_rect(scene, scale)` repaints the whole frame. The caller is
    /// responsible for `damage` covering every scene change.
    [[nodiscard]] bool render(const Scene& scene, const DamageRegion& damage,
                              std::string* error = nullptr);

    /// Records a host `invalidate` for the next `render_incremental`: `reload`
    /// repaints the frame, a node id repaints that node's subtree, and any
    /// other reason without a node id repaints the frame.
    void invalidate(InvalidationReason reason, std::string_view node_id = {});

    /// Telemetry for the most recent frame; the GPU time fills in once that
    /// frame has finished on the GPU (see `finish`).
    [[nodiscard]] FrameStats frame_stats() const;

    /// Waits until every frame submitted so far has finished on the GPU.
    [[nodiscard]] bool finish(std::string* error = nullptr);

    /// Forces the Dawn device lost, for exercising the fail-closed paths in
    /// tests. Every later render, capture and finish fails.
    void simulate_device_loss();
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

    /// What the most recent `render` call lost, from
    /// `scene_loss_diagnostics`: for a drawn scene, a warning for each image
    /// drawn as `kMissingImagePlaceholder` or with nothing of its source to
    /// show; for a refused scene, only the refusal: its version error, or a
    /// `kInvalidImageCode` error for an undrawable image or image table.
    /// Other refusals leave it empty and report through `error`.
    [[nodiscard]] const std::vector<LoweringDiagnostic>& diagnostics() const noexcept;

    /// Replaces the cache budgets, dropping the least recently used entries
    /// that no longer fit. A zero budget turns that cache off.
    void set_cache_budgets(const RenderCacheBudgets& budgets);
    [[nodiscard]] RenderCacheBudgets cache_budgets() const;

    /// Damage, layer, cache and GPU-time telemetry for the most recent frame;
    /// the GPU time fills in once that frame has finished on the GPU.
    [[nodiscard]] RenderTelemetry telemetry() const;

private:
    class Impl;
    explicit SkiaDawnSurface(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::graphics
