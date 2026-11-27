// SkiaSurface on a real Dawn device: the frame cycle, retained layers, the
// persistent-scene blit, readback and its failure paths, and the retained
// layer stores of SkiaCanvas. Every case that needs pixels reads them back
// from the GPU. Offscreen only; no window is created. The executable exits 77
// (skipped) when there is no Dawn adapter or the adapter is the Null backend,
// which cannot produce raster output.

#include "skia_surface_test_support.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/canvas/skia_canvas.hpp>
#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>

#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>


namespace {

using skia_surface_test::TextureDrawableGpu;
using skia_surface_test::make_drawable_texture;
using skia_surface_test::StderrCapture;
using skia_surface_test::pixel_is;
using skia_surface_test::read_drawable;
using vellum::render::GpuSurface;
using vellum::render::SkiaSurface;

FRAMEWORK_TEST(creation_needs_an_initialized_gpu_surface) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    // Don't initialize GpuSurface: SkiaSurface should fail gracefully
    SkiaSurface::Config config{};
    config.width = 400;
    config.height = 300;

    auto skia = SkiaSurface::create(*gpu, config);
    // Should be null because GpuSurface has no Dawn handles
    REQUIRE(skia == nullptr);
}

FRAMEWORK_TEST(creation_shares_the_gpu_surface_device) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 400;
    gpu_config.height = 300;

    REQUIRE(!(!gpu->initialize(gpu_config)));

    SkiaSurface::Config config{};
    config.width = 400;
    config.height = 300;
    config.scale_factor = 1.0f;

    auto skia = SkiaSurface::create(*gpu, config);
    REQUIRE(!(!skia));

    REQUIRE(skia->is_available());
}

FRAMEWORK_TEST(an_offscreen_frame_cycle_draws_and_submits) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 200;
    gpu_config.height = 150;

    REQUIRE(!(!gpu->initialize(gpu_config)));

    SkiaSurface::Config config{};
    config.width = 200;
    config.height = 150;

    auto skia = SkiaSurface::create(*gpu, config);
    REQUIRE(!(!skia || !skia->is_available()));

    // Frame cycle: GpuSurface brackets the frame, SkiaSurface draws
    REQUIRE(gpu->begin_frame());

    auto* canvas = skia->begin_frame();
    REQUIRE(canvas != nullptr);

    // Draw something
    canvas->set_fill_color(vellum::canvas::Color::rgba8(255, 0, 0));
    canvas->fill_rect(0, 0, 200, 150);

    skia->end_frame();  // submit Graphite recording
    gpu->end_frame();   // present (no-op in offscreen mode)
}

FRAMEWORK_TEST(repeated_frame_cycles_keep_no_state) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 100;
    gpu_config.height = 100;

    REQUIRE(!(!gpu->initialize(gpu_config)));

    auto skia = SkiaSurface::create(*gpu, {.width = 100, .height = 100});
    REQUIRE(!(!skia || !skia->is_available()));

    // Multiple frames: verify no state leaks
    for (int i = 0; i < 5; ++i) {
        REQUIRE(gpu->begin_frame());
        auto* canvas = skia->begin_frame();
        REQUIRE(canvas != nullptr);
        canvas->fill_rect(0, 0, 100, 100);
        skia->end_frame();
        gpu->end_frame();
    }
}

FRAMEWORK_TEST(cacheable_layers_survive_across_frame_canvases) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 100;
    gpu_config.height = 100;
    REQUIRE(!(!gpu->initialize(gpu_config)));
    REQUIRE(!(gpu->adapter_info().backend_type == "Null"));

    auto skia = SkiaSurface::create(*gpu, {.width = 100, .height = 100});
    REQUIRE(!(!skia || !skia->is_available()));

    REQUIRE(gpu->begin_frame());
    auto* first_frame = skia->begin_frame();
    REQUIRE(first_frame != nullptr);
    REQUIRE(first_frame->supports(
        vellum::canvas::CanvasCapability::retained_layer_cache));

    first_frame->begin_layer({0.0f, 0.0f, 100.0f, 100.0f},
                             /*cacheable=*/true);
    first_frame->set_fill_color(
        vellum::canvas::Color::rgba8(255, 0, 0));
    first_frame->fill_rect(0.0f, 0.0f, 100.0f, 100.0f);
    const auto cached = first_frame->end_layer();
    REQUIRE(cached);
    REQUIRE(first_frame->layer_valid(cached));
    first_frame->draw_layer(cached);

    skia->end_frame();
    gpu->end_frame();

    REQUIRE(gpu->begin_frame());
    auto* second_frame = skia->begin_frame();
    REQUIRE(second_frame != nullptr);

    // SkiaSurface wraps each frame in a fresh SkiaCanvas. The renderer-owned
    // retained-layer store must keep both the handle and its SkImage alive.
    REQUIRE(second_frame->layer_valid(cached));
    second_frame->set_fill_color(
        vellum::canvas::Color::rgba8(0, 0, 255));
    second_frame->fill_rect(0.0f, 0.0f, 100.0f, 100.0f);
    second_frame->draw_layer(cached);

    std::vector<uint8_t> pixels;
    uint32_t pixel_width = 0;
    uint32_t pixel_height = 0;
    REQUIRE(skia->read_current_rgba(
        pixels, pixel_width, pixel_height));
    REQUIRE(pixel_width == 100);
    REQUIRE(pixel_height == 100);
    const size_t center =
        (static_cast<size_t>(50) * pixel_width + 50u) * 4u;
    REQUIRE(pixels.at(center) > 200);
    REQUIRE(pixels.at(center + 1) < 50);
    REQUIRE(pixels.at(center + 2) < 50);

    skia->end_frame();
    gpu->end_frame();

    // Moving the renderer to a new DPI retires the old-density texture.
    skia->resize(100, 100, 2.0f);
    REQUIRE(gpu->begin_frame());
    auto* scaled_frame = skia->begin_frame();
    REQUIRE(scaled_frame != nullptr);
    REQUIRE_FALSE(scaled_frame->layer_valid(cached));
    skia->end_frame();
    gpu->end_frame();
}

FRAMEWORK_TEST(readback_fails_when_graphite_rejects_the_flushed_recording) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 64;
    gpu_config.height = 64;
    REQUIRE(!(!gpu->initialize(gpu_config)));
    REQUIRE(!(gpu->adapter_info().backend_type == "Null"));

    auto skia = SkiaSurface::create(*gpu, {.width = 64, .height = 64});
    REQUIRE(!(!skia || !skia->is_available()));

    // Restores the production path even when an assertion below throws.
    struct HookGuard {
        explicit HookGuard(SkiaSurface::InsertOutcomeHook hook) {
            SkiaSurface::set_insert_outcome_hook_for_testing(hook);
        }
        ~HookGuard() {
            SkiaSurface::set_insert_outcome_hook_for_testing(nullptr);
        }
    };

    auto draw_red_frame = [&]() {
        REQUIRE(gpu->begin_frame());
        auto* canvas = skia->begin_frame();
        REQUIRE(canvas != nullptr);
        canvas->set_fill_color(vellum::canvas::Color::rgba8(255, 0, 0));
        canvas->fill_rect(0.0f, 0.0f, 64.0f, 64.0f);
    };

    std::vector<uint8_t> pixels;
    uint32_t pixel_width = 0;
    uint32_t pixel_height = 0;

    // Control: an accepted flush captures the frame and the frame succeeds.
    draw_red_frame();
    REQUIRE(skia->read_current_rgba(pixels, pixel_width, pixel_height));
    REQUIRE(pixels.at((32u * pixel_width + 32u) * 4u) > 200);
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::offscreen);
    REQUIRE(skia->wait_for_submitted_work());
    gpu->end_frame();

    // A rejected flush is a failed capture, and the frame it belonged to is a
    // failed frame rather than one whose work "already reached the GPU".
    {
        HookGuard reject([](bool) { return false; });
        draw_red_frame();
        REQUIRE_FALSE(skia->read_current_rgba(pixels, pixel_width, pixel_height));
        REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::failed);
        gpu->end_frame();
    }

    // Removing the hook restores normal captures on the same surface.
    draw_red_frame();
    REQUIRE(skia->read_current_rgba(pixels, pixel_width, pixel_height));
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::offscreen);
    gpu->end_frame();
}

FRAMEWORK_TEST(the_persistent_scene_blit_presents_each_frames_own_scene) {
    constexpr uint32_t kSize = 64;
    auto dawn = GpuSurface::create_dawn();
    REQUIRE(!(!dawn));
    REQUIRE(!(!dawn->initialize({.width = kSize, .height = kSize})));
    REQUIRE(!(dawn->adapter_info().backend_type == "Null"));

    TextureDrawableGpu gpu(*dawn);
    auto skia = SkiaSurface::create(gpu, {.width = kSize, .height = kSize});
    REQUIRE(!(!skia || !skia->is_available()));
    REQUIRE(skia->set_persistent_scene(true));

    // Two drawables, as a swapchain rotates them: frame 1 presents into the
    // first, frame 2 into the second.
    wgpu::Texture drawable_1 = make_drawable_texture(*dawn, kSize, kSize);
    wgpu::Texture drawable_2 = make_drawable_texture(*dawn, kSize, kSize);
    REQUIRE(drawable_1);
    REQUIRE(drawable_2);

    // Frame 1 paints the whole scene red.
    gpu.set_drawable(&drawable_1);
    REQUIRE(gpu.begin_frame());
    auto* frame_1 = skia->begin_frame();
    REQUIRE(frame_1 != nullptr);
    frame_1->set_fill_color(vellum::canvas::Color::rgba8(255, 0, 0));
    frame_1->fill_rect(0.0f, 0.0f, 64.0f, 64.0f);
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::presented);
    gpu.end_frame();

    // Frame 2 repaints only the left half, blue: the right half is retained.
    gpu.set_drawable(&drawable_2);
    REQUIRE(gpu.begin_frame());
    auto* frame_2 = skia->begin_frame();
    REQUIRE(frame_2 != nullptr);
    frame_2->set_fill_color(vellum::canvas::Color::rgba8(0, 0, 255));
    frame_2->fill_rect(0.0f, 0.0f, 32.0f, 64.0f);
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::presented);
    gpu.end_frame();
    REQUIRE(skia->wait_for_submitted_work());

    auto* context = skia->graphite_context();
    REQUIRE(context != nullptr);
    std::vector<uint8_t> px_1;
    std::vector<uint8_t> px_2;
    REQUIRE(read_drawable(gpu, drawable_1, px_1));
    REQUIRE(read_drawable(gpu, drawable_2, px_2));

    // Frame 1's drawable holds frame 1's scene: frame 2's later scene draws
    // did not reach back into a blit that shares the scene's texture. This
    // fails only if the blit leaves its own frame's recording (a cached view,
    // a deferred blit); it guards that ordering rather than probing a hazard
    // the current code has.
    CHECK(pixel_is(px_1, kSize, 16, 32, 255, 0, 0));
    CHECK(pixel_is(px_1, kSize, 48, 32, 255, 0, 0));
    // Frame 2's drawable shows frame 2: its repaint plus the retained half.
    CHECK(pixel_is(px_2, kSize, 16, 32, 0, 0, 255));
    CHECK(pixel_is(px_2, kSize, 48, 32, 255, 0, 0));
}

FRAMEWORK_TEST(the_readback_fallback_keeps_the_scene_blit_free_of_snapshot_warnings) {
    constexpr uint32_t kSize = 64;
    auto dawn = GpuSurface::create_dawn();
    REQUIRE(!(!dawn));
    REQUIRE(!(!dawn->initialize({.width = kSize, .height = kSize})));
    REQUIRE(!(dawn->adapter_info().backend_type == "Null"));

    TextureDrawableGpu gpu(*dawn);
    auto skia = SkiaSurface::create(gpu, {.width = kSize, .height = kSize});
    REQUIRE(!(!skia || !skia->is_available()));
    REQUIRE(skia->set_persistent_scene(true));
    wgpu::Texture drawable = make_drawable_texture(*dawn, kSize, kSize);
    REQUIRE(drawable);
    gpu.set_drawable(&drawable);

    // Restores the asynchronous readback even when an assertion throws.
    struct AsyncReadbackOff {
        AsyncReadbackOff() {
            SkiaSurface::set_async_readback_for_testing(false);
        }
        ~AsyncReadbackOff() {
            SkiaSurface::set_async_readback_for_testing(true);
        }
    };

    auto paint = [](vellum::canvas::Canvas& canvas, vellum::canvas::Color color, float width) {
        canvas.set_fill_color(color);
        canvas.fill_rect(0.0f, 0.0f, width, 64.0f);
    };

    // Frame 1 paints the scene red and blits it.
    REQUIRE(gpu.begin_frame());
    auto* frame_1 = skia->begin_frame();
    REQUIRE(frame_1 != nullptr);
    paint(*frame_1, vellum::canvas::Color::rgba8(255, 0, 0), 64.0f);
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::presented);
    gpu.end_frame();
    REQUIRE(skia->wait_for_submitted_work());

#if !defined(_WIN32)
    StderrCapture captured;
    REQUIRE(captured.active());
#endif

    // Frame 2 repaints the left half blue and reads the scene back through
    // the fallback a timed-out readback takes, then blits as usual. Frame 3
    // blits again, which is where a snapshot taken by the fallback would make
    // Graphite warn about mixing the two image APIs.
    std::vector<uint8_t> px;
    uint32_t pw = 0;
    uint32_t ph = 0;
    bool fallback_read = false;
    {
        AsyncReadbackOff fallback;
        REQUIRE(gpu.begin_frame());
        auto* frame_2 = skia->begin_frame();
        REQUIRE(frame_2 != nullptr);
        paint(*frame_2, vellum::canvas::Color::rgba8(0, 0, 255), 32.0f);
        fallback_read = skia->read_current_rgba(px, pw, ph);
        REQUIRE(vellum::render::frame_reached_output(skia->end_frame()));
        gpu.end_frame();
    }
    REQUIRE(gpu.begin_frame());
    auto* frame_3 = skia->begin_frame();
    REQUIRE(frame_3 != nullptr);
    paint(*frame_3, vellum::canvas::Color::rgba8(0, 255, 0), 8.0f);
    REQUIRE(skia->end_frame() == vellum::render::FrameOutcome::presented);
    gpu.end_frame();
    REQUIRE(skia->wait_for_submitted_work());

#if !defined(_WIN32)
    const std::string log = captured.text();
    CHECK(log.find("Intermingling makeImageSnapshot and asImage") == std::string::npos);
#endif

    // On Graphite the synchronous fallback cannot read a texture-backed image
    // without a context, so it reports failure rather than handing back
    // pixels; it must still leave the scene and the blits intact.

    // The drawable shows frame 3: the blits after the fallback still work.
    std::vector<uint8_t> shown;
    auto* context = skia->graphite_context();
    REQUIRE(context != nullptr);
    REQUIRE(read_drawable(gpu, drawable, shown));
    CHECK(pixel_is(shown, kSize, 4, 32, 0, 255, 0));
    CHECK(pixel_is(shown, kSize, 16, 32, 0, 0, 255));
    CHECK(pixel_is(shown, kSize, 48, 32, 255, 0, 0));
}

FRAMEWORK_TEST(resize_keeps_frames_working) {
    auto gpu = GpuSurface::create_dawn();
    REQUIRE(!(!gpu));

    GpuSurface::Config gpu_config{};
    gpu_config.width = 200;
    gpu_config.height = 200;

    REQUIRE(!(!gpu->initialize(gpu_config)));

    auto skia = SkiaSurface::create(*gpu, {.width = 200, .height = 200});
    REQUIRE(!(!skia || !skia->is_available()));

    // Resize both GpuSurface and SkiaSurface
    gpu->resize(400, 300);
    skia->resize(400, 300);

    // Should still work after resize
    REQUIRE(gpu->begin_frame());
    auto* canvas = skia->begin_frame();
    REQUIRE(canvas != nullptr);
    canvas->fill_rect(0, 0, 400, 300);
    skia->end_frame();
    gpu->end_frame();
}

} // namespace

int main() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    auto probe = GpuSurface::create_dawn();
    GpuSurface::Config config{};
    config.width = 16;
    config.height = 16;
    if (!probe || !probe->initialize(config)) {
        std::printf("SKIP no Dawn adapter\n");
        return 77;
    }
    if (probe->adapter_info().backend_type == "Null") {
        std::printf("SKIP the Null backend cannot produce raster output\n");
        return 77;
    }
    auto skia = SkiaSurface::create(*probe, {.width = 16, .height = 16});
    if (!skia || !skia->is_available()) {
        std::printf("SKIP Skia Graphite surface unavailable\n");
        return 77;
    }
    skia.reset();
    probe.reset();
    return framework_test::run_all();
}
