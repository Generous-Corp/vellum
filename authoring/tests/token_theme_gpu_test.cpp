#include "rendered_tree_materializer.hpp"
#include "support/scene_capture_harness.hpp"
#include "token_slot_tree.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/token_receipts.hpp>

#include <cstdint>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
using vellum::authoring::testing::SlotBindings;
using vellum::authoring::testing::SlotTheme;
using vellum::authoring::testing::slot_tree;
namespace harness = vellum::graphics::capture_harness;

constexpr std::uint32_t kWidth = 240;
constexpr std::uint32_t kHeight = 160;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

Scene materialize(const SlotTheme& theme, const SlotBindings& bound = {}) {
    vellum::authoring::RenderedApplication app;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(slot_tree(theme, bound), app, &error)) {
        throw std::runtime_error("slot tree was not materialized: " + error);
    }
    return app.scene;
}

std::unique_ptr<SkiaDawnSurface> make_surface() {
    std::string error;
    auto surface = SkiaDawnSurface::create({.width = kWidth, .height = kHeight, .scale = 1.0F},
                                           &error);
    if (!surface) throw std::runtime_error("surface creation failed: " + error);
    return surface;
}

std::vector<std::uint8_t> capture(SkiaDawnSurface& surface) {
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::string error;
    if (!surface.capture_rgba(rgba, width, height, &error)) {
        throw std::runtime_error("capture failed: " + error);
    }
    return rgba;
}

std::vector<std::uint8_t> reference(const Scene& scene) {
    auto surface = make_surface();
    std::string error;
    if (!surface->render(scene, &error)) throw std::runtime_error("render failed: " + error);
    return capture(*surface);
}

std::size_t differing(const std::vector<std::uint8_t>& left,
                      const std::vector<std::uint8_t>& right) {
    const auto comparison = harness::compare_pixels(left, kWidth, kHeight, right, kWidth,
                                                    kHeight, 0);
    return comparison.same_size ? comparison.differing_pixels : left.size();
}

struct Slot final {
    std::string name;
    SlotTheme theme;
    SlotBindings unbound;
};

// For each slot: materialize a tree, switch only that slot's token on the
// retained surface through partial redraw, and compare with materializing
// the new theme. The control leaves the slot's tokenRefs out of the tree, so
// the same switch must leave the pixels untouched.
void theme_switch_per_slot() {
    const SlotTheme base;
    const std::vector<Slot> slots = {
        {"fill", {.fill = "#ef4444"}, {.fill = false}},
        {"text run", {.run = "#38bdf8"}, {.run = false}},
        {"box shadow", {.shadow = "#a3e635cc"}, {.shadow = false}},
        {"gradient stop", {.stop = "#f97316"}, {.stop = false}},
        {"path stroke", {.stroke = "#eab308"}, {.stroke = false}},
    };
    for (const auto& slot : slots) {
        const Scene light = materialize(base);
        const Scene expected_scene = materialize(slot.theme);
        const auto before = reference(light);
        const auto expected = reference(expected_scene);
        expect(differing(before, expected) > 0U,
               "the " + slot.name + " fixture themes differ on screen");

        auto surface = make_surface();
        std::string error;
        expect(surface->render_incremental(light, &error), "the light frame renders", error);
        Scene switched = light;
        const auto result = apply_token_values(switched, expected_scene.tokens);
        expect(result.applied && result.changed.size() == 1U && result.nodes_updated == 1U,
               "a " + slot.name + " switch re-resolves one receipt on one node");
        expect(surface->render_incremental(switched, &error), "the switched frame renders",
               error);
        const auto stats = surface->frame_stats();
        expect(!stats.full_repaint && stats.damage_area_pixels > 0 &&
                   stats.damage_area_pixels < stats.frame_area_pixels,
               "a " + slot.name + " switch is a partial redraw",
               "damage=" + std::to_string(stats.damage_area_pixels) + "/" +
                   std::to_string(stats.frame_area_pixels) + " reason=" +
                   std::string(to_string(stats.full_repaint_reason)));
        const auto after = capture(*surface);
        expect(differing(before, after) > 0U, "a " + slot.name + " switch changes the pixels");
        expect(differing(expected, after) == 0U,
               "a " + slot.name + " switch draws exactly the new theme",
               std::to_string(differing(expected, after)) + " pixels differ");

        Scene control = materialize(base, slot.unbound);
        const auto control_before = reference(control);
        const auto moved = apply_token_values(control, expected_scene.tokens);
        expect(moved.applied && moved.nodes_updated == 0U &&
                   differing(control_before, reference(control)) == 0U,
               "control: without the " + slot.name + " receipt the switch changes nothing");
    }
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "SKIP no GPU device: " << error << '\n';
        return 77;
    }
    try {
        theme_switch_per_slot();
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << (failures == 0 ? "token theme: all checks passed\n"
                                : "token theme: " + std::to_string(failures) + " failed\n");
    return failures == 0 ? 0 : 1;
}
