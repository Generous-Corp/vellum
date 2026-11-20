#include "../src/skia_dawn_surface_testing.hpp"
#include "support/gpu_test_environment.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
namespace hooks = vellum::graphics::testing;

int failures = 0;

void expect(bool ok, const std::string& label, const std::string& detail = {}) {
    std::cout << (ok ? "PASS " : "FAIL ") << label;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!ok) ++failures;
}

struct RestoreBatching final {
    bool saved = hooks::rectangle_batching_enabled();
    ~RestoreBatching() { hooks::set_rectangle_batching(saved); }
};

Scene rectangles(std::size_t count = 600U) {
    Scene scene;
    scene.width = 320.0F;
    scene.height = 240.0F;
    scene.background = Color::hex(0x172033);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, scene.width, scene.height}};
    for (std::size_t index = 0; index < count; ++index) {
        scene.root.children.push_back({
            .id = "rect-" + std::to_string(index),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {static_cast<float>(index % 30U) * 10.0F,
                       static_cast<float>(index / 30U) * 10.0F, 8.0F, 8.0F},
            .fill = Color::rgba(0.123F, 0.587F, 0.941F),
        });
    }
    return scene;
}

bool capture(SkiaDawnSurface& surface, harness::CaptureResult& result, std::string& error) {
    result.ok = surface.capture_rgba(result.rgba, result.pixel_width, result.pixel_height, &error);
    return result.ok;
}

void compare(const std::string& label, const Scene& scene, float scale,
             std::uint64_t expected_rectangles, bool cache = false) {
    auto created = harness::create_gpu_surface({.width = 320U, .height = 240U, .scale = scale});
    if (!created.surface) {
        expect(false, label + " surface", created.status.message);
        return;
    }
    auto& surface = *created.surface;
    if (!cache) surface.set_layer_cache_policy({.enabled = false});
    std::string error;
    harness::CaptureResult individual;
    harness::CaptureResult batched;
    hooks::set_rectangle_batching(false);
    const auto before_individual = hooks::rectangle_batch_counters();
    if (!surface.render(scene, &error) || !capture(surface, individual, error)) {
        expect(false, label + " individual render", error);
        return;
    }
    expect(hooks::rectangle_batch_counters().batches == before_individual.batches,
           label + " disabled control");
    const auto individual_stats = surface.frame_stats();
    hooks::set_rectangle_batching(true);
    const auto before = hooks::rectangle_batch_counters();
    if (!surface.render(scene, &error) || !capture(surface, batched, error)) {
        expect(false, label + " batched render", error);
        return;
    }
    const auto after = hooks::rectangle_batch_counters();
    expect(after.rectangles - before.rectangles == expected_rectangles,
           label + " batch eligibility",
           std::to_string(after.batches - before.batches) + " batches, " +
           std::to_string(after.rectangles - before.rectangles) + " rectangles");
    const auto parity = harness::assert_identical(individual, batched);
    expect(static_cast<bool>(parity), label + " exact RGBA parity", parity.message);
    bool visible = false;
    for (std::size_t pixel = 0; pixel + 3U < batched.rgba.size(); pixel += 4U) {
        if (batched.rgba[pixel] != scene.background.red8() ||
            batched.rgba[pixel + 1U] != scene.background.green8() ||
            batched.rgba[pixel + 2U] != scene.background.blue8()) {
            visible = true;
            break;
        }
    }
    expect(visible, label + " visible paint");
    if (!cache) {
        // These existing counters count logical commands, not Skia submissions.
        expect(individual_stats.paint_command_draws == surface.frame_stats().paint_command_draws &&
                   individual_stats.paint_commands_recorded ==
                       surface.frame_stats().paint_commands_recorded,
               label + " command accounting");
    }
}

void fallback_checks() {
    const auto base = rectangles(2U);
    {
        auto scene = base;
        scene.root.children[1].fill = Color::hex(0xF07A45);
        compare("different colors", scene, 1.0F, 0U);
    }
    for (const std::string variant : {"fractional", "rounded", "corners", "transform",
                                     "alpha", "opacity", "gradient", "shadow"}) {
        auto scene = base;
        for (auto& node : scene.root.children) {
            if (variant == "fractional") node.bounds.x += 0.25F;
            if (variant == "rounded") node.corner_radius = 3.0F;
            if (variant == "corners") node.corner_radii = CornerRadii{2.0F, 0.0F, 1.0F, 0.0F};
            if (variant == "transform") node.transform = translation(2.0F, 0.0F);
            if (variant == "alpha") node.fill.alpha = 0.5F;
            if (variant == "opacity") node.opacity = 0.5F;
            if (variant == "gradient") {
                node.fill_gradients = {{.angle_degrees = 90.0F,
                    .stops = {{.position = 0.0F, .color = Color::hex(0xFA3377)},
                              {.position = 1.0F, .color = Color::hex(0x33AAFF)}}}};
            }
            if (variant == "shadow") {
                node.box_shadows = {{.offset_x = 2.0F, .offset_y = 1.0F,
                                    .blur_radius = 3.0F, .color = Color::hex(0x000000, 0.4F)}};
            }
        }
        compare(variant, scene, 1.0F, 0U);
    }
    {
        auto scene = base;
        scene.root.clip = NodeClip{.rect = {0.5F, 0.5F, 12.0F, 5.0F}};
        compare("clip", scene, 1.0F, 0U);
    }
    {
        auto scene = base;
        scene.root.opacity = 0.6F;
        compare("opacity group", scene, 1.0F, 0U);
    }
    {
        auto scene = base;
        scene.root.isolate = true;
        compare("isolated layer", scene, 1.0F, 0U);
        compare("cached layer", scene, 1.0F, 0U, true);
    }
}

void benchmark() {
    auto scene = rectangles(6000U);
    for (std::size_t index = 0; index < scene.root.children.size(); ++index) {
        scene.root.children[index].bounds = {
            static_cast<float>(index % 100U) * 3.0F,
            static_cast<float>(index / 100U) * 3.0F, 2.0F, 2.0F};
    }
    compare("timed scene", scene, 1.0F, 6000U);
    if (failures != 0) return;
    auto created = harness::create_gpu_surface({.width = 320U, .height = 240U});
    if (!created.surface) {
        expect(false, "benchmark surface", created.status.message);
        return;
    }
    created.surface->set_layer_cache_policy({.enabled = false});
    std::string error;
    std::vector<double> individual, batched;
    for (int round = 0; round < 14; ++round) {
        for (int path = 0; path < 2; ++path) {
            const bool enabled = (round + path) % 2 != 0;
            hooks::set_rectangle_batching(enabled);
            if (!created.surface->render(scene, &error)) {
                expect(false, "benchmark render", error);
                return;
            }
            if (round >= 2) {
                (enabled ? batched : individual).push_back(
                    created.surface->frame_stats().cpu_milliseconds);
            }
        }
    }
    std::sort(individual.begin(), individual.end());
    std::sort(batched.begin(), batched.end());
    std::cout << "TIMING 6000 rectangles: individual median_ms=" << individual[individual.size()/2]
              << " batched median_ms=" << batched[batched.size()/2] << '\n';
}

}  // namespace

int rectangle_batch_checks(bool run_benchmark) {
    RestoreBatching restore;
    const auto environment = harness::prepare_gpu_environment();
    if (!environment.ready()) return harness::report_gpu_status(environment);
    compare("dense scale 1", rectangles(), 1.0F, 600U);
    compare("dense scale 2", rectangles(), 2.0F, 600U);
    compare("dense fractional scale aligned", rectangles(), 1.5F, 600U);
    {
        auto scene = rectangles(4U);
        scene.root.children[0].bounds = {-4.0F, -2.0F, 30.0F, 20.0F};
        scene.root.children[1].bounds = {4.0F, 3.0F, 30.0F, 20.0F};
        scene.root.children[2].bounds = {4.0F, 3.0F, 30.0F, 20.0F};
        scene.root.children[3].bounds = {28.0F, 3.0F, 300.0F, 20.0F};
        compare("overlap duplicates offscreen", scene, 1.0F, 4U);
    }
    {
        auto scene = rectangles(5U);
        scene.root.children[2].fill.alpha = 0.4F;
        scene.root.children[2].bounds = {3.0F, 1.0F, 35.0F, 6.0F};
        compare("alpha barrier preserves order", scene, 1.0F, 4U);
    }
    {
        auto scene = rectangles(2U);
        for (auto& node : scene.root.children) node.bounds.x += 0.5F;
        compare("half pixels scale 1", scene, 1.0F, 0U);
        compare("half pixels scale 2", scene, 2.0F, 2U);
    }
    fallback_checks();
    if (failures == 0 && run_benchmark) benchmark();
    return failures == 0 ? 0 : 1;
}
