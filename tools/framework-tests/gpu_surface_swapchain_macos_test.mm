// A presentable GpuSurface configures its swapchain in begin_frame(), not in
// resize(). These cases run against a real CAMetalLayer that no window hosts,
// so the counted configures are Dawn's own surface configures and the acquired
// texture is the one a frame would present. Nothing is ordered in or shown.
// The executable exits 77 (skipped) when GpuSurface will not initialize on a
// layer.

#include "framework_test.hpp"

#include <TargetConditionals.h>

#if TARGET_OS_OSX

#import <QuartzCore/QuartzCore.h>

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>

#include "webgpu/webgpu_cpp.h"

#include <cstdint>
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#define REQUIRE(expression)                                                    \
    do {                                                                       \
        if (!(expression)) {                                                   \
            ::framework_test::record_failure(#expression, __FILE__, __LINE__); \
            throw std::runtime_error("REQUIRE failed: " #expression);          \
        }                                                                      \
    } while (false)
#define REQUIRE_FALSE(expression) REQUIRE(!(expression))

namespace {

using gpu_runtime::GpuSurface;
using vellum::render::SkiaSurface;

struct LayerSurface {
    CAMetalLayer* layer = nil;
    std::unique_ptr<GpuSurface> gpu;

    bool open(uint32_t width, uint32_t height) {
        layer = [CAMetalLayer layer];
        layer.drawableSize = CGSizeMake(width, height);
        gpu = GpuSurface::create_dawn();
        if (!gpu)
            return false;
        GpuSurface::Config config{};
        config.width = width;
        config.height = height;
        config.native_surface_handle = (__bridge void*)layer;
        return gpu->initialize(config) && gpu->has_surface();
    }

    uint64_t configures() const {
        return gpu->swapchain_stats().configure_count;
    }

    // The size of the texture this frame acquired, or 0x0 when it acquired none.
    bool frame(uint32_t* width, uint32_t* height) {
        *width = 0;
        *height = 0;
        if (!gpu->begin_frame())
            return false;
        auto* texture = static_cast<wgpu::Texture*>(gpu->current_texture_handle());
        if (texture && *texture) {
            *width = texture->GetWidth();
            *height = texture->GetHeight();
        }
        gpu->end_frame();
        return true;
    }
};

FRAMEWORK_TEST(presentable_gpusurface_configures_once_for_many_resizes_before_a_frame) {
    @autoreleasepool {
        LayerSurface s;
        REQUIRE(s.open(400, 300));
        REQUIRE(s.configures() == 1); // the initial configure

        uint32_t w = 0, h = 0;
        REQUIRE(s.frame(&w, &h));
        REQUIRE(s.configures() == 1);

        // A live resize: sixty size events land before the next frame.
        for (uint32_t i = 1; i <= 60; ++i)
            s.gpu->resize(400 + 4 * i, 300 + 2 * i);
        REQUIRE(s.configures() == 1);
        REQUIRE(s.gpu->width() == 640);
        REQUIRE(s.gpu->height() == 420);

        REQUIRE(s.frame(&w, &h));
        REQUIRE(s.configures() == 2);
    }
}

FRAMEWORK_TEST(presentable_gpusurface_presents_a_resize_at_the_new_size_on_the_next_frame) {
    @autoreleasepool {
        LayerSurface s;
        REQUIRE(s.open(400, 300));

        uint32_t w = 0, h = 0;
        REQUIRE(s.frame(&w, &h));
        REQUIRE(w == 400);
        REQUIRE(h == 300);

        s.gpu->resize(800, 500);
        REQUIRE(s.frame(&w, &h));
        CHECK(w == 800);
        CHECK(h == 500);
        CHECK(s.layer.drawableSize.width == 800);
        CHECK(s.layer.drawableSize.height == 500);
    }
}

FRAMEWORK_TEST(presentable_gpusurface_resize_to_the_same_size_configures_nothing) {
    @autoreleasepool {
        LayerSurface s;
        REQUIRE(s.open(400, 300));
        uint32_t w = 0, h = 0;
        REQUIRE(s.frame(&w, &h));
        const uint64_t before = s.configures();

        s.gpu->resize(400, 300);
        REQUIRE(s.frame(&w, &h));
        REQUIRE(s.configures() == before);
        REQUIRE(w == 400);
    }
}

FRAMEWORK_TEST(presentable_gpusurface_at_a_zero_size_skips_the_frame_and_configures_nothing) {
    @autoreleasepool {
        LayerSurface s;
        REQUIRE(s.open(400, 300));
        uint32_t w = 0, h = 0;
        REQUIRE(s.frame(&w, &h));
        const uint64_t before = s.configures();

        s.gpu->resize(0, 0);
        REQUIRE_FALSE(s.frame(&w, &h));
        s.gpu->resize(400, 0);
        REQUIRE_FALSE(s.frame(&w, &h));
        REQUIRE(s.configures() == before);

        s.gpu->resize(640, 480);
        REQUIRE(s.frame(&w, &h));
        REQUIRE(s.configures() == before + 1);
        CHECK(w == 640);
        CHECK(h == 480);
    }
}

FRAMEWORK_TEST(presentable_gpusurface_created_at_a_zero_size_configures_on_its_first_sized_frame) {
    @autoreleasepool {
        LayerSurface s;
        REQUIRE(s.open(0, 0));
        REQUIRE(s.configures() == 0);

        uint32_t w = 0, h = 0;
        REQUIRE_FALSE(s.frame(&w, &h));
        s.gpu->resize(320, 240);
        REQUIRE(s.frame(&w, &h));
        REQUIRE(s.configures() == 1);
        CHECK(w == 320);
        CHECK(h == 240);
    }
}

// A full GpuSurface + SkiaSurface pair on a layer no window hosts. No
// compositor returns its drawables, so a case presents only a handful of
// frames.
struct LayerFrames {
    CAMetalLayer* layer = nil;
    std::unique_ptr<GpuSurface> gpu;
    std::unique_ptr<SkiaSurface> skia;

    bool open(uint32_t width, uint32_t height) {
        layer = [CAMetalLayer layer];
        layer.drawableSize = CGSizeMake(width, height);
        gpu = GpuSurface::create_dawn();
        if (!gpu)
            return false;
        GpuSurface::Config config{};
        config.width = width;
        config.height = height;
        config.vsync = false;
        config.native_surface_handle = (__bridge void*)layer;
        if (!gpu->initialize(config) || !gpu->has_surface())
            return false;
        skia = SkiaSurface::create(*gpu, {.width = width, .height = height});
        if (!skia || !skia->is_available())
            return false;
        return skia->set_persistent_scene(true);
    }

    void resize(uint32_t width, uint32_t height) {
        gpu->resize(width, height);
        skia->resize(width, height, 1.0f);
    }

    // One full frame; reports the size of the texture it presented.
    bool frame(uint32_t* width, uint32_t* height) {
        if (!gpu->begin_frame())
            return false;
        auto* texture = static_cast<wgpu::Texture*>(gpu->current_texture_handle());
        *width = texture && *texture ? texture->GetWidth() : 0;
        *height = texture && *texture ? texture->GetHeight() : 0;
        auto* canvas = skia->begin_frame();
        if (!canvas) {
            gpu->end_frame();
            return false;
        }
        canvas->fill_rect(0, 0, static_cast<float>(*width), static_cast<float>(*height));
        skia->end_frame();
        gpu->end_frame();
        [CATransaction flush];
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.005, false);
        return true;
    }
};


FRAMEWORK_TEST(sixty_resizes_inside_four_frames_configure_and_allocate_once_per_frame) {
    @autoreleasepool {
        LayerFrames f;
        REQUIRE(f.open(400, 300));
        uint32_t w = 0, h = 0;
        REQUIRE(f.frame(&w, &h));
        const uint64_t configures = f.gpu->swapchain_stats().configure_count;
        const uint64_t allocations = f.skia->render_target_allocations();

        int frames = 0;
        for (uint32_t frame = 0; frame < 4; ++frame) {
            for (uint32_t i = 1; i <= 15; ++i)
                f.resize(400 + frame * 15 + i, 300 + i);
            // Resizes alone touch neither the swapchain nor the Graphite targets.
            REQUIRE(f.gpu->swapchain_stats().configure_count == configures + frame);
            REQUIRE(f.skia->render_target_allocations() == allocations + frame);
            REQUIRE(f.frame(&w, &h));
            ++frames;
        }
        const uint64_t configured = f.gpu->swapchain_stats().configure_count - configures;
        CHECK(configured == static_cast<uint64_t>(frames));
        CHECK(f.skia->render_target_allocations() - allocations == configured);
        CHECK(w == 460);
        CHECK(h == 315);
    }
}

FRAMEWORK_TEST(presentable_gpusurface_settles_at_exactly_the_final_size_after_a_drag) {
    @autoreleasepool {
        LayerFrames f;
        REQUIRE(f.open(400, 300));
        uint32_t w = 0, h = 0;
        REQUIRE(f.frame(&w, &h));
        REQUIRE(f.frame(&w, &h));
        REQUIRE(f.frame(&w, &h));
        REQUIRE(f.gpu->swapchain_stats().settled);

        for (uint32_t i = 1; i <= 6; ++i) {
            f.resize(400 + 7 * i, 300 + 3 * i);
            REQUIRE(f.frame(&w, &h));
            REQUIRE_FALSE(f.gpu->swapchain_stats().settled);
        }
        REQUIRE(f.frame(&w, &h));
        REQUIRE_FALSE(f.gpu->swapchain_stats().settled);
        REQUIRE(f.frame(&w, &h));
        REQUIRE(f.gpu->swapchain_stats().settled);
        CHECK(w == 442);
        CHECK(h == 318);
        CHECK(f.layer.drawableSize.width == 442);
        CHECK(f.layer.drawableSize.height == 318);
    }
}


// A host may size the swapchain from the platform's physical client rect, which
// at a fractional scale is a pixel more than logical x scale (1001 px is 667
// logical at 1.5x, and 667 x 1.5 is 1000); a readback must not crop it.
FRAMEWORK_TEST(a_presentable_frame_reads_back_at_the_swapchains_own_size) {
    @autoreleasepool {
        CAMetalLayer* layer = [CAMetalLayer layer];
        layer.drawableSize = CGSizeMake(1001, 751);
        auto gpu = GpuSurface::create_dawn();
        REQUIRE(gpu != nullptr);
        GpuSurface::Config config{};
        config.width = 1001;
        config.height = 751;
        config.native_surface_handle = (__bridge void*)layer;
        REQUIRE(gpu->initialize(config) && gpu->has_surface());
        auto skia = SkiaSurface::create(*gpu, {.width = 667, .height = 501, .scale_factor = 1.5f});
        REQUIRE(skia && skia->is_available());

        REQUIRE(gpu->begin_frame());
        auto* canvas = skia->begin_frame();
        REQUIRE(canvas != nullptr);
        canvas->fill_rect(0, 0, 1001.0f / 1.5f, 751.0f / 1.5f);
        std::vector<uint8_t> pixels;
        uint32_t w = 0, h = 0;
        REQUIRE(skia->read_current_rgba(pixels, w, h));
        CHECK(w == 1001);
        CHECK(h == 751);
        CHECK(pixels.size() == static_cast<size_t>(w) * h * 4);
        skia->end_frame();
        gpu->end_frame();
    }
}

FRAMEWORK_TEST(the_persistent_scene_matches_a_fractional_dpi_swapchains_size) {
    @autoreleasepool {
        CAMetalLayer* layer = [CAMetalLayer layer];
        layer.drawableSize = CGSizeMake(1001, 751);
        auto gpu = GpuSurface::create_dawn();
        REQUIRE(gpu != nullptr);
        GpuSurface::Config config{};
        config.width = 1001;
        config.height = 751;
        config.native_surface_handle = (__bridge void*)layer;
        REQUIRE(gpu->initialize(config) && gpu->has_surface());
        auto skia = SkiaSurface::create(*gpu, {.width = 667, .height = 501, .scale_factor = 1.5f});
        REQUIRE(skia && skia->is_available());
        REQUIRE(skia->set_persistent_scene(true));
        // The retained scene is blitted 1:1 onto the drawable; derived from
        // 667 x 1.5 it would be 1000 wide under a 1001 px swapchain.
        REQUIRE(gpu->begin_frame());
        auto* canvas = skia->begin_frame();
        REQUIRE(canvas != nullptr);
        canvas->fill_rect(0, 0, 1001.0f / 1.5f, 751.0f / 1.5f);
        std::vector<uint8_t> pixels;
        uint32_t w = 0, h = 0;
        REQUIRE(skia->read_current_rgba(pixels, w, h));
        CHECK(w == 1001);
        CHECK(h == 751);
        CHECK(pixels.size() == static_cast<size_t>(w) * h * 4);
        skia->end_frame();
        gpu->end_frame();
    }
}

} // namespace

int main() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    @autoreleasepool {
        LayerSurface probe;
        if (!probe.open(64, 64)) {
            std::printf("SKIP GpuSurface would not initialize on a CAMetalLayer\n");
            return 77;
        }
    }
    return framework_test::run_all();
}

#else

int main() {
    return 77;
}

#endif // TARGET_OS_OSX
