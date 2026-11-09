#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#import <QuartzCore/CAMetalLayer.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
using Clock = std::chrono::steady_clock;

constexpr float kScale = 2.0F;
constexpr int kWarmup = 10;
constexpr int kFrames = 60;
constexpr float kTileWidth = 400.0F;
constexpr float kTileHeight = 300.0F;

Scene tiled_scene(float width, float height) {
    Scene scene;
    scene.width = width;
    scene.height = height;
    scene.background = harness::fixtures::combined(kTileWidth, kTileHeight).background;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, width, height}};
    int tile = 0;
    for (float y = 0.0F; y < height; y += kTileHeight) {
        for (float x = 0.0F; x < width; x += kTileWidth) {
            auto content = harness::fixtures::combined(kTileWidth, kTileHeight).root;
            content.id = "tile-" + std::to_string(tile++);
            content.bounds.x = x;
            content.bounds.y = y;
            scene.root.children.push_back(std::move(content));
        }
    }
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {24.0F, height * 0.5F, 40.0F, 40.0F},
        .fill = Color::hex(0xF472B6),
        .corner_radius = 6.0F,
    });
    return scene;
}

void move(Scene& scene, int frame) {
    auto& mover = scene.root.children.back();
    mover.bounds.x = 24.0F + static_cast<float>(frame % 60) * 4.0F;
}

double median(std::vector<double> values) {
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    const auto middle = values.size() / 2U;
    return values.size() % 2U == 0U ? (values[middle - 1U] + values[middle]) / 2.0
                                    : values[middle];
}

struct Series final {
    std::vector<double> wall;
    std::vector<double> cpu;
    std::vector<double> gpu;
    std::vector<double> wait;
    FrameStats last;
};

enum class Mode { full, partial, blit };

bool run(SkiaDawnSurface& surface, Scene scene, Mode mode, Series& series, std::string* error) {
    for (int frame = 0; frame < kWarmup + kFrames; ++frame) {
        if (mode != Mode::blit) move(scene, frame);
        const auto start = Clock::now();
        bool ok = false;
        if (mode == Mode::full) {
            ok = surface.render(scene, error);
        } else if (mode == Mode::partial) {
            ok = surface.render_incremental(scene, error);
        } else {
            ok = surface.render(scene, DamageRegion(frame_rect(scene, kScale)), error);
        }
        if (!ok || !surface.finish(error)) return false;
        const auto stop = Clock::now();
        if (frame < kWarmup) continue;
        const auto stats = surface.frame_stats();
        const double wait = stats.present_wait_milliseconds;
        series.wall.push_back(
            std::chrono::duration<double, std::milli>(stop - start).count() - wait);
        series.cpu.push_back(stats.cpu_milliseconds - wait);
        series.wait.push_back(wait);
        if (stats.gpu_time_available) series.gpu.push_back(stats.gpu_milliseconds);
        series.last = stats;
    }
    return true;
}

std::string cell(const Series& series) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3) << median(series.wall) << " / "
        << median(series.cpu) << " / ";
    if (series.gpu.empty()) {
        out << "n/a";
    } else {
        out << median(series.gpu);
    }
    return out.str();
}

}  // namespace

static std::vector<double> last_wait;

constexpr int kSkip = 77;

int check_native() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    std::string error;
    CAMetalLayer* layer = [CAMetalLayer layer];
    layer.contentsScale = kScale;
    layer.drawableSize = CGSizeMake(width * kScale, height * kScale);
    layer.frame = CGRectMake(0, 0, width, height);
    auto native = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = kScale,
         .native_surface_handle = (__bridge void*)layer, .vsync = false},
        &error);
    auto reference = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = kScale}, &error);
    if (!reference) {
        std::cerr << "FAIL offscreen surface: " << error << '\n';
        return 1;
    }
    if (!native || !native->evidence().native_surface) {
        std::cout << "SKIP no usable CAMetalLayer surface: " << error << '\n';
        return kSkip;
    }
    Scene scene = tiled_scene(static_cast<float>(width), static_cast<float>(height));
    int failures = 0;
    for (int frame = 0; frame < 8; ++frame) {
        move(scene, frame * 7 + 1);
        if (frame == 4) native->invalidate(InvalidationReason::reload);
        harness::CaptureResult expected;
        harness::CaptureResult actual;
        if (frame == 0 && !native->render_incremental(scene, &error)) {
            if (error.find("acquire the native surface texture") != std::string::npos) {
                std::cout << "SKIP no drawable from a CAMetalLayer outside a window: " << error
                          << '\n';
                return kSkip;
            }
            std::cerr << "FAIL first native frame: " << error << '\n';
            return 1;
        }
        if ((frame != 0 && !native->render_incremental(scene, &error)) ||
            !reference->render(scene, &error) ||
            !native->capture_rgba(actual.rgba, actual.pixel_width, actual.pixel_height, &error) ||
            !reference->capture_rgba(expected.rgba, expected.pixel_width, expected.pixel_height,
                                     &error)) {
            std::cerr << "FAIL frame " << frame << ": " << error << '\n';
            return 1;
        }
        expected.ok = actual.ok = true;
        const auto stats = native->frame_stats();
        const bool expected_full = frame == 0 || frame == 4;
        const auto verdict = harness::assert_identical(expected, actual);
        const bool ok = static_cast<bool>(verdict) && stats.presented &&
            stats.full_repaint == expected_full;
        std::cout << (ok ? "PASS" : "FAIL") << " native frame " << frame
                  << " full=" << stats.full_repaint << " reason="
                  << to_string(stats.full_repaint_reason) << " presented=" << stats.presented
                  << ' ' << verdict.message << '\n';
        if (!ok) ++failures;
    }
    std::cout << "native partial presentation " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    const bool offscreen = mode == "--offscreen";
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "device bootstrap failed: " << error << '\n';
        return 1;
    }
    if (mode == "--check-native") return check_native();
    const std::vector<std::pair<std::uint32_t, std::uint32_t>> sizes{
        {400, 300}, {1280, 800}, {2560, 1600}};
    std::cout << "| logical size | device pixels | target | full redraw | partial + blit | "
                 "blit alone | partial damage | commands re-recorded |\n"
                 "|---|---|---|---|---|---|---|---|\n";
    for (const auto& [width, height] : sizes) {
        @autoreleasepool {
            CAMetalLayer* layer = nil;
            if (!offscreen) {
                layer = [CAMetalLayer layer];
                layer.contentsScale = kScale;
                layer.drawableSize = CGSizeMake(width * kScale, height * kScale);
                layer.frame = CGRectMake(0, 0, width, height);
            }
            auto surface = SkiaDawnSurface::create(
                {.width = width, .height = height, .scale = kScale,
                 .native_surface_handle = offscreen ? nullptr : (__bridge void*)layer,
                 .vsync = false},
                &error);
            if (!surface) {
                std::cerr << "surface creation failed: " << error << '\n';
                return 1;
            }
            const auto scene = tiled_scene(static_cast<float>(width), static_cast<float>(height));
            Series full;
            Series partial;
            Series blit;
            if (!run(*surface, scene, Mode::full, full, &error) ||
                !run(*surface, scene, Mode::partial, partial, &error) ||
                !run(*surface, scene, Mode::blit, blit, &error)) {
                std::cerr << "frame failed: " << error << '\n';
                return 1;
            }
            std::cout << "| " << width << "x" << height << " | "
                      << width * static_cast<std::uint32_t>(kScale) << "x"
                      << height * static_cast<std::uint32_t>(kScale) << " | "
                      << (surface->evidence().native_surface ? "CAMetalLayer" : "offscreen")
                      << " | " << cell(full) << " | " << cell(partial) << " | " << cell(blit)
                      << " | " << std::setprecision(4) << partial.last.damage_fraction * 100.0
                      << "% in " << partial.last.damage_rects_after_coalescing << " rects | "
                      << partial.last.paint_commands_recorded << " of "
                      << partial.last.paint_commands_total << " |\n";
            last_wait = partial.wait;
            if (partial.last.full_repaint) {
                std::cerr << "partial frames repainted fully: "
                          << to_string(partial.last.full_repaint_reason) << '\n';
                return 1;
            }
        }
    }
    std::cout << "\nCells are median wall / CPU record / GPU milliseconds over " << kFrames
              << " frames after " << kWarmup
              << " warm-up frames. Wall time runs from the render call until the GPU has\n"
                 "finished; wall and CPU exclude the wait for a swapchain texture, which is\n"
                 "paced by the display (median " << std::fixed << std::setprecision(3)
              << median(last_wait) << " ms).\n";
    return 0;
}
