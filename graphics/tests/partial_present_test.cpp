#include "../src/damage_testing.hpp"
#include "../src/skia_dawn_surface_test_access.hpp"
#include "../src/skia_dawn_surface_testing.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;
namespace hooks = vellum::graphics::testing;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

std::string describe(const FrameStats& stats) {
    std::ostringstream out;
    out << "frame=" << stats.frame << " full=" << stats.full_repaint
        << " reason=" << to_string(stats.full_repaint_reason)
        << " damage_px=" << stats.damage_area_pixels << '/' << stats.frame_area_pixels
        << " fraction=" << stats.damage_fraction
        << " rects=" << stats.damage_rects_before_coalescing << "->"
        << stats.damage_rects_after_coalescing
        << " commands=" << stats.paint_commands_recorded << '/' << stats.paint_commands_total
        << " draws=" << stats.paint_command_draws;
    return out.str();
}

SceneNode* find_mutable(SceneNode& node, const std::string& id) {
    if (node.id == id) return &node;
    for (auto& child : node.children) {
        if (auto* found = find_mutable(child, id); found != nullptr) return found;
    }
    return nullptr;
}

SceneNode& node(Scene& scene, const std::string& id) {
    auto* found = find_mutable(scene.root, id);
    if (found == nullptr) throw std::runtime_error("fixture node missing: " + id);
    return *found;
}

Scene base_scene(float width, float height) {
    Scene scene = harness::fixtures::combined(width, height);
    scene.scene_version = kDamageModelSceneVersion;
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width * 0.08F, height * 0.62F, 36.0F, 24.0F},
        .fill = Color::hex(0xF472B6),
        .corner_radius = 5.0F,
    });
    scene.root.children.push_back({
        .id = "swatch",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width * 0.80F, height * 0.80F, 28.0F, 28.0F},
        .fill = Color::hex(0x22C55E),
        .box_shadows = {{.offset_x = 2.0F, .offset_y = 3.0F, .blur_radius = 6.0F,
                         .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)}},
    });
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .bounds = {width * 0.05F, height * 0.90F, width * 0.5F, 16.0F},
        .text_runs = {{.text = "Frame 1",
                       .style = {.font_family = "Inter", .font_size = 13.0F,
                                 .color = Color::hex(0xE5E7EB)}}},
    });
    return scene;
}

struct Step final {
    std::string name;
    std::function<void(Scene&)> edit;
    bool expect_partial = true;
};

std::vector<Step> scripted_steps() {
    return {
        {"move a rectangle", [](Scene& scene) {
             auto& mover = node(scene, "mover");
             mover.bounds.x += 7.3F;
             mover.bounds.y -= 3.1F;
         }},
        {"move it over shadowed content", [](Scene& scene) {
             auto& mover = node(scene, "mover");
             mover.bounds.x += 41.7F;
             mover.bounds.y -= 52.4F;
         }},
        {"change a colour", [](Scene& scene) {
             node(scene, "swatch").fill = Color::hex(0x3B82F6);
         }},
        {"change text", [](Scene& scene) {
             node(scene, "label").text_runs.front().text = "Frame 2, longer";
         }},
        {"add a node", [](Scene& scene) {
             scene.root.children.push_back({
                 .id = "badge",
                 .kind = SceneNode::Kind::rectangle,
                 .bounds = {scene.width * 0.45F, scene.height * 0.70F, 30.0F, 18.0F},
                 .fill = Color::hex(0xFACC15),
                 .box_shadows = {{.offset_x = 1.0F, .offset_y = 2.0F, .blur_radius = 4.0F,
                                  .spread_radius = 1.0F,
                                  .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.6F)}},
                 .corner_radius = 9.0F,
             });
         }},
        {"remove a node", [](Scene& scene) {
             auto& children = scene.root.children;
             std::erase_if(children, [](const SceneNode& child) { return child.id == "badge"; });
         }},
        {"change nothing", [](Scene&) {}},
    };
}

struct Capture final {
    bool ok = false;
    std::string error;
    harness::CaptureResult result;
};

Capture capture(SkiaDawnSurface& surface) {
    Capture out;
    out.result.evidence = surface.evidence();
    out.ok = surface.capture_rgba(out.result.rgba, out.result.pixel_width,
                                  out.result.pixel_height, &out.error);
    out.result.ok = out.ok;
    return out;
}

std::unique_ptr<SkiaDawnSurface> make_surface(std::uint32_t width, std::uint32_t height,
                                              float scale, bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = scale}, &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    if (surface && !layer_cache) {
        LayerCachePolicy policy;
        policy.enabled = false;
        surface->set_layer_cache_policy(policy);
    }
    return surface;
}

bool compare(const std::string& what, SkiaDawnSurface& full, SkiaDawnSurface& partial) {
    const auto expected = capture(full);
    const auto actual = capture(partial);
    if (!expected.ok || !actual.ok) {
        expect(false, what + " captures", expected.error + actual.error);
        return false;
    }
    const auto verdict = harness::assert_identical(expected.result, actual.result);
    expect(static_cast<bool>(verdict), what + " is byte-identical to a full redraw",
           verdict.message);
    return static_cast<bool>(verdict);
}

bool render_both(SkiaDawnSurface& full, SkiaDawnSurface& partial, const Scene& scene) {
    std::string error;
    const bool full_ok = full.render(scene, &error);
    const bool partial_ok = full_ok && partial.render_incremental(scene, &error);
    expect(full_ok && partial_ok, "both paths render", error);
    return full_ok && partial_ok;
}

void scripted_sequence(std::uint32_t width, std::uint32_t height, float scale) {
    const std::string size = std::to_string(width) + "x" + std::to_string(height) + "@" +
        std::to_string(static_cast<int>(scale)) + " ";
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale);
    if (!full || !partial) return;

    Scene scene = base_scene(static_cast<float>(width), static_cast<float>(height));
    if (!render_both(*full, *partial, scene)) return;
    const auto first = partial->frame_stats();
    expect(first.full_repaint && first.full_repaint_reason == FullRepaintReason::first_frame,
           size + "the first frame repaints everything", describe(first));
    expect(full->frame_stats().full_repaint_reason == FullRepaintReason::full_render,
           size + "render(scene) reports a full render", describe(full->frame_stats()));
    compare(size + "the first frame", *full, *partial);

    for (const auto& step : scripted_steps()) {
        step.edit(scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        if (step.expect_partial) {
            const bool nothing = step.name == "change nothing";
            expect(!stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::none,
                   size + step.name + " repaints only its damage", describe(stats));
            expect(stats.damage_fraction < 0.5 &&
                       stats.paint_commands_recorded <= stats.paint_commands_total &&
                       stats.paint_commands_total == full->frame_stats().paint_commands_total,
                   size + step.name + " reports bounded damage", describe(stats));
            if (nothing) {
                expect(stats.damage_area_pixels == 0 && stats.paint_commands_recorded == 0 &&
                           stats.damage_rects_after_coalescing == 0,
                       size + "an unchanged scene records no paint commands", describe(stats));
            } else {
                expect(stats.damage_area_pixels > 0 && stats.damage_rects_after_coalescing > 0 &&
                           stats.paint_commands_recorded > 0 &&
                           stats.paint_commands_recorded < stats.paint_commands_total,
                       size + step.name + " re-records a subset of the paint commands",
                       describe(stats));
            }
        }
        compare(size + step.name, *full, *partial);
    }

    std::string error;
    const std::uint32_t grown_width = width + 40;
    const std::uint32_t grown_height = height + 24;
    expect(full->resize(grown_width, grown_height, scale, &error) &&
               partial->resize(grown_width, grown_height, scale, &error),
           size + "both paths resize", error);
    scene.width = static_cast<float>(grown_width);
    scene.height = static_cast<float>(grown_height);
    node(scene, "mover").bounds.x += 5.0F;
    if (!render_both(*full, *partial, scene)) return;
    const auto resized = partial->frame_stats();
    expect(resized.full_repaint &&
               resized.full_repaint_reason == FullRepaintReason::surface_recreated &&
               resized.damage_fraction == 1.0,
           size + "a resize mid-sequence repaints everything", describe(resized));
    compare(size + "the resized frame", *full, *partial);

    node(scene, "mover").bounds.y += 9.5F;
    if (!render_both(*full, *partial, scene)) return;
    expect(!partial->frame_stats().full_repaint,
           size + "the frame after a resize is partial again", describe(partial->frame_stats()));
    compare(size + "a move after the resize", *full, *partial);
}

void host_invalidation() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    if (!render_both(*full, *partial, scene)) return;

    partial->invalidate(InvalidationReason::reload);
    if (!render_both(*full, *partial, scene)) return;
    const auto reload = partial->frame_stats();
    expect(reload.full_repaint && reload.full_repaint_reason == FullRepaintReason::reload &&
               reload.paint_commands_recorded == reload.paint_commands_total,
           "reload repaints the frame", describe(reload));
    compare("the reloaded frame", *full, *partial);

    if (!render_both(*full, *partial, scene)) return;
    expect(!partial->frame_stats().full_repaint && partial->frame_stats().damage_area_pixels == 0,
           "a reload applies to one frame only", describe(partial->frame_stats()));

    partial->invalidate(InvalidationReason::state, "swatch");
    if (!render_both(*full, *partial, scene)) return;
    const auto state = partial->frame_stats();
    expect(!state.full_repaint && state.damage_area_pixels > 0 &&
               state.paint_commands_recorded > 0,
           "a state invalidation repaints the named node", describe(state));
    compare("the state-invalidated frame", *full, *partial);

    partial->invalidate(InvalidationReason::timer);
    if (!render_both(*full, *partial, scene)) return;
    const auto timer = partial->frame_stats();
    expect(timer.full_repaint && timer.full_repaint_reason == FullRepaintReason::full_damage,
           "a timer invalidation without a node repaints the frame", describe(timer));

    partial->invalidate(InvalidationReason::promise, "no-such-node");
    if (!render_both(*full, *partial, scene)) return;
    expect(partial->frame_stats().full_repaint_reason == FullRepaintReason::full_damage,
           "an unknown node id repaints the frame", describe(partial->frame_stats()));
    compare("the promise-invalidated frame", *full, *partial);
}

void recreation_and_failure() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    if (!render_both(*full, *partial, scene)) return;

    std::string error;
    expect(partial->resize(width, height, 2.0F, &error),
           "resizing to the same size recreates the retained surface", error);
    node(scene, "mover").bounds.x += 3.0F;
    if (!render_both(*full, *partial, scene)) return;
    expect(partial->frame_stats().full_repaint_reason == FullRepaintReason::surface_recreated,
           "the first frame after recreation repaints everything",
           describe(partial->frame_stats()));
    compare("the frame after recreation", *full, *partial);

    expect(partial->resize(width, height, 2.0F, &error),
           "the retained surface is recreated again", error);
    compare("a capture of a recreated surface redraws the last scene", *full, *partial);
    node(scene, "mover").bounds.x += 2.0F;
    if (!render_both(*full, *partial, scene)) return;
    expect(!partial->frame_stats().full_repaint,
           "the redraw for that capture makes the next frame partial",
           describe(partial->frame_stats()));
    compare("the frame after a capture redraw", *full, *partial);

    Scene broken = scene;
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {{.x = 0.0F, .y = 0.0F}, {.x = 10.0F, .y = 0.0F}, {.x = 0.0F, .y = 10.0F}};
    mesh->indices = {0, 1, 7};
    node(broken, "mover").children.push_back({
        .id = "broken-mesh",
        .kind = SceneNode::Kind::mesh,
        .bounds = {0.0F, 0.0F, 10.0F, 10.0F},
        .mesh = mesh,
    });
    expect(!partial->render_incremental(broken, &error), "an invalid mesh fails the frame");
    node(scene, "mover").bounds.x += 4.0F;
    if (!render_both(*full, *partial, scene)) return;
    expect(partial->frame_stats().full_repaint_reason == FullRepaintReason::previous_frame_failed,
           "the frame after a failure repaints everything", describe(partial->frame_stats()));
    compare("the frame after a failure", *full, *partial);

    Scene refused = scene;
    refused.scene_version = kSceneVersion + 1U;
    expect(!partial->render_incremental(refused, &error), "an unsupported version is refused");
    node(scene, "mover").bounds.y += 4.0F;
    if (!render_both(*full, *partial, scene)) return;
    expect(!partial->frame_stats().full_repaint,
           "a refused scene leaves the retained surface valid", describe(partial->frame_stats()));
    compare("the frame after a refusal", *full, *partial);
}

void mismatched_damage_frame() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    if (!render_both(*full, *partial, scene)) return;
    const Scene previous = scene;
    node(scene, "mover").bounds.x += 80.0F;
    node(scene, "mover").bounds.y -= 40.0F;
    const auto wrong_scale = compute_scene_damage(previous, scene, 1.0F);
    std::string error;
    expect(full->render(scene, &error) && partial->render(scene, wrong_scale, &error),
           "a damage region computed at another scale renders", error);
    const auto stats = partial->frame_stats();
    expect(stats.full_repaint &&
               stats.full_repaint_reason == FullRepaintReason::damage_frame_mismatch,
           "a damage region for another frame size or scale repaints the whole frame",
           describe(stats));
    compare("a damage region computed at another scale", *full, *partial);
}

void device_loss() {
    auto surface = make_surface(400, 300, 2.0F);
    if (!surface) return;
    const Scene scene = base_scene(400, 300);
    std::string error;
    expect(surface->render(scene, &error) && capture(*surface).ok,
           "a frame renders and captures before the device is lost", error);
    surface->simulate_device_loss();
    error.clear();
    std::vector<std::uint8_t> rgba;
    std::uint32_t pixel_width = 0;
    std::uint32_t pixel_height = 0;
    expect(!surface->capture_rgba(rgba, pixel_width, pixel_height, &error) &&
               error.find("lost") != std::string::npos && rgba.empty(),
           "a capture after device loss fails closed without submitting", error);
    error.clear();
    expect(!surface->render_incremental(scene, &error) &&
               error.find("lost") != std::string::npos,
           "an incremental frame after device loss fails closed", error);
    error.clear();
    expect(!surface->render(scene, &error) && error.find("lost") != std::string::npos,
           "a full frame after device loss fails closed", error);
    error.clear();
    expect(!surface->finish(&error) && error.find("lost") != std::string::npos,
           "finish reports the lost device", error);
}

void capture_after_failure() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    const Scene a = base_scene(width, height);
    if (!render_both(*full, *partial, a)) return;

    Scene c = a;
    node(c, "mover").bounds.x += 60.0F;
    node(c, "swatch").fill = Color::hex(0xF97316);
    Scene b = c;
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {{.x = 0.0F, .y = 0.0F}, {.x = 10.0F, .y = 0.0F}, {.x = 0.0F, .y = 10.0F}};
    mesh->indices = {0, 1, 9};
    b.root.children.push_back({
        .id = "broken-mesh",
        .kind = SceneNode::Kind::mesh,
        .bounds = {300.0F, 40.0F, 10.0F, 10.0F},
        .mesh = mesh,
    });
    std::string error;
    expect(!partial->render_incremental(b, &error), "scene B with an invalid mesh fails");
    const auto redrawn = capture(*partial);
    expect(redrawn.ok, "a capture after the failure redraws the last good scene", redrawn.error);
    if (!render_both(*full, *partial, c)) return;
    expect(!partial->frame_stats().full_repaint,
           "the frame after that capture is partial", describe(partial->frame_stats()));
    compare("render A, fail B, capture, render C", *full, *partial);
}

void command_extents() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    auto& mover = node(scene, "mover");
    scene.root.children.insert(scene.root.children.begin(), {
        .id = "inverted",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {mover.bounds.x + 70.0F, mover.bounds.y - 10.0F, -50.0F, 40.0F},
        .fill = Color::hex(0x0EA5E9),
    });
    if (!render_both(*full, *partial, scene)) return;
    for (int step = 0; step < 4; ++step) {
        node(scene, "mover").bounds.x += 9.0F;
        if (!render_both(*full, *partial, scene)) return;
        expect(!partial->frame_stats().full_repaint,
               "a move across a command with an unbounded extent is partial",
               describe(partial->frame_stats()));
        compare("a command with an unbounded extent is always redrawn, step " +
                    std::to_string(step), *full, *partial);
    }
}

Scene grouping_scene(float width, float height);

void damage_model_version_gate() {
    expect(kDamageModelSceneVersion == 8U && partial_redraw_supported(3U) &&
               partial_redraw_supported(4U) && partial_redraw_supported(5U) &&
               partial_redraw_supported(6U) && partial_redraw_supported(7U) &&
               partial_redraw_supported(8U) &&
               partial_redraw_supported(kDamageModelSceneVersion) &&
               !partial_redraw_supported(kDamageModelSceneVersion + 1U) &&
               !partial_redraw_supported(9U),
           "partial redraw covers scene versions up to 8 and refuses version 9");
    expect(to_string(FullRepaintReason::damage_model_version) == "damage_model_version",
           "the damage-model version fallback has a telemetry name");
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    scene.scene_version = kDamageModelSceneVersion;
    if (!render_both(*full, *partial, scene)) return;
    for (int step = 0; step < 2; ++step) {
        node(scene, "mover").bounds.x += 6.0F;
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        expect(!stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::none &&
                   stats.paint_commands_recorded < stats.paint_commands_total,
               "a default version 8 scene takes the partial path, step " + std::to_string(step),
               describe(stats));
        compare("a default version 8 scene, step " + std::to_string(step), *full, *partial);
    }
    Scene newer = scene;
    newer.scene_version = kSceneVersion + 1U;
    std::string error;
    const auto before = partial->frame_stats().frame;
    expect(!partial->render_incremental(newer, &error) &&
               partial->frame_stats().frame == before,
           "a scene version this build cannot draw is refused before any redraw",
           describe(partial->frame_stats()));

    auto older = make_surface(width, height, 2.0F);
    if (!older) return;
    SkiaDawnSurfaceTestAccess::set_damage_model_version(*older, 4U);
    Scene grouped = grouping_scene(width, height);
    if (!render_both(*full, *older, grouped)) return;
    node(grouped, "mover").bounds.x += 6.0F;
    if (!render_both(*full, *older, grouped)) return;
    const auto refused = older->frame_stats();
    expect(refused.full_repaint &&
               refused.full_repaint_reason == FullRepaintReason::damage_model_version,
           "a version 8 scene past a version 4 damage model repaints fully",
           describe(refused));
    compare("a version 8 scene past a version 4 damage model", *full, *older);
}

Affine2D rotation_degrees(float degrees, float scale = 1.0F) {
    const float radians = degrees * 3.14159265358979323846F / 180.0F;
    const float cosine = std::cos(radians) * scale;
    const float sine = std::sin(radians) * scale;
    return {.a = cosine, .b = sine, .c = -sine, .d = cosine};
}

SceneNode card_front() {
    return {
        .id = "card-front",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {24.0F, 18.0F, 60.0F, 36.0F},
        .fill = Color::hex(0xF59E0B),
    };
}

SceneNode card_label() {
    return {
        .id = "card-label",
        .kind = SceneNode::Kind::text,
        .bounds = {6.0F, 8.0F, 70.0F, 16.0F},
        .text_runs = {{.text = "Card",
                       .style = {.font_family = "Inter", .font_size = 13.0F,
                                 .color = Color::hex(0xF9FAFB)}}},
    };
}

void remove_child(Scene& scene, const std::string& parent, const std::string& id) {
    std::erase_if(node(scene, parent).children,
                  [&](const SceneNode& child) { return child.id == id; });
}

std::shared_ptr<const PathGeometry> zigzag(float stroke_width, Color stroke_color) {
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line,
                   PathVerb::close};
    path->values = {0.0F, 0.0F, 14.0F, 36.0F, 28.0F, 0.0F, 42.0F, 36.0F};
    path->stroke = {.color = stroke_color, .width = stroke_width,
                    .join = StrokeJoin::miter, .cap = StrokeCap::butt, .miter_limit = 4.0F};
    return path;
}

SceneNode path_node(std::string id, Rect bounds, Color fill, float stroke_width,
                    Color stroke_color) {
    return {
        .id = std::move(id),
        .kind = SceneNode::Kind::path,
        .bounds = bounds,
        .fill = fill,
        .path = zigzag(stroke_width, stroke_color),
    };
}

PathGeometry& edit_path(Scene& scene, const std::string& id) {
    auto& target = node(scene, id);
    auto copy = std::make_shared<PathGeometry>(*target.path);
    target.path = copy;
    return *copy;
}

Scene compositing_scene(float width, float height) {
    Scene scene = base_scene(width, height);
    SceneNode card;
    card.id = "card";
    card.bounds = {width * 0.55F, height * 0.18F, 90.0F, 60.0F};
    card.children.push_back({
        .id = "card-back",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 70.0F, 44.0F},
        .fill = Color::hex(0x6366F1),
        .box_shadows = {{.offset_x = 3.0F, .offset_y = 4.0F, .blur_radius = 6.0F,
                         .spread_radius = 1.0F, .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)}},
        .corner_radius = 6.0F,
    });
    card.children.push_back(card_front());
    scene.root.children.push_back(card);
    SceneNode panel;
    panel.id = "panel";
    panel.bounds = {width * 0.30F, height * 0.40F, 110.0F, 40.0F};
    panel.opacity = 0.6F;
    panel.children.push_back({
        .id = "panel-back",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 100.0F, 34.0F},
        .fill = Color::hex(0x0EA5E9),
        .corner_radius = 4.0F,
    });
    panel.children.push_back(card_label());
    scene.root.children.push_back(panel);
    scene.root.children.push_back({
        .id = "stretched",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {60.0F, 60.0F, 40.0F, 20.0F},
        .fill = Color::hex(0x14B8A6),
        .box_shadows = {{.blur_radius = 20.0F, .color = Color::rgba(0.0F, 0.0F, 0.0F, 1.0F)}},
    });
    scene.root.children.push_back({
        .id = "neighbour",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {80.0F, 88.0F, 20.0F, 8.0F},
        .fill = Color::hex(0xE11D48),
    });
    scene.root.children.push_back(path_node("glyph",
                                            {width * 0.42F, height * 0.77F, 42.0F, 36.0F},
                                            Color::hex(0x38BDF8), 6.0F, Color::hex(0xFDE68A)));
    SceneNode veil;
    veil.id = "veil";
    veil.bounds = {width * 0.62F, height * 0.52F, 100.0F, 50.0F};
    veil.opacity = 0.5F;
    veil.children.push_back({
        .id = "veil-back",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 20.0F, 20.0F},
        .fill = Color::hex(0xA3E635),
    });
    veil.children.push_back(path_node("veil-path", {50.0F, 6.0F, 42.0F, 36.0F},
                                      Color::hex(0xFB7185), 5.0F, Color::hex(0x1E293B)));
    scene.root.children.push_back(veil);
    SceneNode halo;
    halo.id = "halo";
    halo.bounds = {width * 0.85F, height * 0.37F, 50.0F, 40.0F};
    halo.opacity = 0.6F;
    halo.children.push_back(path_node("halo-path", {0.0F, 0.0F, 42.0F, 36.0F},
                                      Color::hex(0xC084FC), 4.0F, Color::hex(0xF8FAFC)));
    scene.root.children.push_back(halo);
    return scene;
}

std::vector<Step> compositing_steps() {
    return {
        {"rotate a rectangle", [](Scene& scene) {
             node(scene, "mover").transform = rotation_degrees(30.0F);
         }},
        {"rotate and scale it", [](Scene& scene) {
             node(scene, "mover").transform = rotation_degrees(-20.0F, 1.4F);
         }},
        {"move the rotated rectangle", [](Scene& scene) {
             node(scene, "mover").bounds.x += 23.5F;
         }},
        {"scale a group", [](Scene& scene) {
             node(scene, "card").transform = Affine2D{.a = 1.25F, .d = 0.8F};
         }},
        {"fade a group", [](Scene& scene) { node(scene, "card").opacity = 0.55F; }},
        {"recolor a child of the scaled faded group", [](Scene& scene) {
             node(scene, "card-front").fill = Color::hex(0x10B981);
         }},
        {"move a child over its siblings in the scaled faded group", [](Scene& scene) {
             node(scene, "card-front").bounds.x -= 9.0F;
             node(scene, "card-front").bounds.y -= 6.0F;
         }},
        {"remove a child of the faded group, folding its opacity", [](Scene& scene) {
             remove_child(scene, "card", "card-front");
         }},
        {"re-add the child, compositing a layer again", [](Scene& scene) {
             node(scene, "card").children.push_back(card_front());
         }},
        {"remove a faded panel's text child, folding its opacity", [](Scene& scene) {
             remove_child(scene, "panel", "card-label");
         }},
        {"re-add the text child, compositing a layer again", [](Scene& scene) {
             node(scene, "panel").children.push_back(card_label());
         }},
        {"re-round a corner set", [](Scene& scene) {
             node(scene, "swatch").corner_radii = CornerRadii{10.0F, 0.0F, 6.0F, 3.0F};
         }},
        {"re-round one corner", [](Scene& scene) {
             node(scene, "swatch").corner_radii->top_right = 8.0F;
         }},
        {"fade the group out", [](Scene& scene) { node(scene, "card").opacity = 0.0F; }},
        {"move inside the hidden group", [](Scene& scene) {
             node(scene, "card-front").bounds.x -= 12.0F;
         }},
        {"fade the group back and unscale it", [](Scene& scene) {
             auto& card = node(scene, "card");
             card.opacity = 1.0F;
             card.transform.reset();
         }},
        {"move a path", [](Scene& scene) {
             node(scene, "glyph").bounds.x += 13.5F;
             node(scene, "glyph").bounds.y += 4.25F;
         }},
        {"restroke a path", [](Scene& scene) {
             auto& path = edit_path(scene, "glyph");
             path.stroke.width = 10.0F;
             path.stroke.color = Color::hex(0xF97316);
         }},
        {"re-dash a path", [](Scene& scene) {
             edit_path(scene, "glyph").stroke.dash_array = {9.0F, 5.0F};
         }},
        {"shift a path's dash phase", [](Scene& scene) {
             edit_path(scene, "glyph").stroke.dash_offset = 4.0F;
         }},
        {"fill a path with a gradient", [](Scene& scene) {
             node(scene, "glyph").fill_gradients = {{
                 .angle_degrees = 90.0F,
                 .stops = {{.position = 0.0F, .color = Color::hex(0xEF4444)},
                           {.position = 1.0F, .color = Color::hex(0x3B82F6)}},
             }};
         }},
        {"rotate a path", [](Scene& scene) {
             node(scene, "glyph").transform = rotation_degrees(25.0F);
         }},
        {"move the rotated path", [](Scene& scene) { node(scene, "glyph").bounds.x -= 7.0F; }},
        {"move a path inside a faded group", [](Scene& scene) {
             node(scene, "veil-path").bounds.x -= 6.0F;
             node(scene, "veil-path").bounds.y += 3.0F;
         }},
        {"restroke a path inside a faded group", [](Scene& scene) {
             edit_path(scene, "veil-path").stroke.width = 9.0F;
         }},
        {"restroke the lone path of a faded group", [](Scene& scene) {
             auto& path = edit_path(scene, "halo-path");
             path.stroke.width = 8.0F;
             path.stroke.color = Color::hex(0x0F172A);
         }},
        {"move the lone path of a faded group", [](Scene& scene) {
             node(scene, "halo-path").bounds.y += 5.0F;
         }},
        {"remove the paths and their faded groups", [](Scene& scene) {
             std::erase_if(scene.root.children, [](const SceneNode& child) {
                 return child.id == "glyph" || child.id == "veil" || child.id == "halo";
             });
         }},
        {"stretch a blurred shadow", [](Scene& scene) {
             node(scene, "stretched").transform = Affine2D{.a = 2.0F, .d = 0.5F};
         }},
        {"damage beside the stretched shadow", [](Scene& scene) {
             node(scene, "neighbour").fill = Color::hex(0x7C3AED);
         }},
        {"rotate the label", [](Scene& scene) {
             node(scene, "label").transform = rotation_degrees(4.0F);
         }, false},
        {"change nothing", [](Scene&) {}},
    };
}

bool crosses_threshold(const std::string& step, float scale) {
    static const std::set<std::string> both = {
        "scale a group",
        "fade a group",
        "recolor a child of the scaled faded group",
        "move a child over its siblings in the scaled faded group",
        "remove a child of the faded group, folding its opacity",
        "re-add the child, compositing a layer again",
        "remove a faded panel's text child, folding its opacity",
        "re-add the text child, compositing a layer again",
        "fade the group out",
        "fade the group back and unscale it",
        "rotate a path",
        "move a path inside a faded group",
        "restroke a path inside a faded group",
        "restroke the lone path of a faded group",
        "move the lone path of a faded group",
        "remove the paths and their faded groups",
        "stretch a blurred shadow",
        "damage beside the stretched shadow",
    };
    return both.contains(step) || (scale == 1.0F && step == "move the rotated path");
}

void compositing_sequence(std::uint32_t width, std::uint32_t height, float scale) {
    const std::string size = std::to_string(width) + "x" + std::to_string(height) + "@" +
        std::to_string(static_cast<int>(scale)) + " ";
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale);
    if (!full || !partial) return;
    Scene scene = compositing_scene(static_cast<float>(width), static_cast<float>(height));
    if (!render_both(*full, *partial, scene)) return;
    compare(size + "the first compositing frame", *full, *partial);
    for (const auto& step : compositing_steps()) {
        step.edit(scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        if (step.expect_partial && crosses_threshold(step.name, scale)) {
            expect(stats.full_repaint &&
                       stats.full_repaint_reason == FullRepaintReason::damage_threshold,
                   size + step.name + " crosses the repaint threshold once enclosed",
                   describe(stats));
        } else if (step.expect_partial) {
            expect(!stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::none,
                   size + step.name + " repaints only its damage", describe(stats));
            if (step.name != "change nothing" && step.name != "move inside the hidden group") {
                expect(stats.damage_area_pixels > 0 &&
                           stats.paint_commands_recorded < stats.paint_commands_total,
                       size + step.name + " re-records a subset of the paint commands",
                       describe(stats));
            } else {
                expect(stats.damage_area_pixels == 0 && stats.paint_commands_recorded == 0,
                       size + step.name + " records no paint commands", describe(stats));
            }
        } else {
            expect(stats.damage_fraction == 1.0,
                   size + step.name + " damages the whole frame", describe(stats));
        }
        compare(size + step.name, *full, *partial);
    }
}

void cached_compositing_sequence(std::uint32_t width, std::uint32_t height, float scale) {
    const std::string size = "cache on " + std::to_string(width) + "x" + std::to_string(height) +
        "@" + std::to_string(static_cast<int>(scale)) + " ";
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale, true);
    if (!full || !partial) return;
    Scene scene = compositing_scene(static_cast<float>(width), static_cast<float>(height));
    if (!render_both(*full, *partial, scene)) return;
    compare(size + "the first compositing frame", *full, *partial);
    for (const auto& step : compositing_steps()) {
        step.edit(scene);
        if (!render_both(*full, *partial, scene)) return;
        compare(size + step.name, *full, *partial);
    }
}

void path_scenes() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    auto full = make_surface(width, height, 2.0F);
    auto partial = make_surface(width, height, 2.0F);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    for (auto& path : harness::fixtures::path_fill_rules(200.0F, 150.0F).root.children) {
        scene.root.children.push_back(std::move(path));
    }
    if (!render_both(*full, *partial, scene)) return;
    for (int step = 0; step < 2; ++step) {
        auto& star = node(scene, "star-evenodd");
        auto recoloured = std::make_shared<PathGeometry>(*star.path);
        recoloured->stroke.color = step == 0 ? Color::hex(0xF43F5E) : Color::hex(0x22C55E);
        star.path = recoloured;
        node(scene, "mover").bounds.x += 6.0F;
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        expect(!stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::none &&
                   stats.paint_commands_recorded < stats.paint_commands_total,
               "a version 3 scene with paths repaints only its damage, step " +
                   std::to_string(step),
               describe(stats));
        compare("a scene with paths, step " + std::to_string(step), *full, *partial);
    }
    Scene legacy = scene;
    legacy.scene_version = 2U;
    std::string error;
    const auto before = partial->frame_stats().frame;
    expect(!partial->render_incremental(legacy, &error) &&
               error == "scene version 2 cannot carry the path node star-nonzero" &&
               partial->frame_stats().frame == before,
           "a version 2 scene with a path is refused before any redraw", error);
}

void whole_path_enclosure() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    constexpr float scale = 2.0F;
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale);
    if (!full || !partial) return;
    Scene scene = compositing_scene(width, height);
    node(scene, "veil").bounds = {240.0F, 205.0F, 100.0F, 50.0F};
    node(scene, "glyph").transform = rotation_degrees(25.0F);
    node(scene, "veil-path").transform = rotation_degrees(15.0F);
    if (!render_both(*full, *partial, scene)) return;

    Scene previous = scene;
    node(scene, "glyph").bounds.x += 9.0F;
    if (!render_both(*full, *partial, scene)) return;
    auto modelled = compute_scene_damage(previous, scene, scale);
    auto stats = partial->frame_stats();
    expect(!stats.full_repaint && modelled.area() > 0 &&
               stats.damage_area_pixels > modelled.area(),
           "a rotated path outside a layer is redrawn whole",
           describe(stats) + " modelled_px=" + std::to_string(modelled.area()));
    compare("a moved rotated path", *full, *partial);

    previous = scene;
    node(scene, "veil-path").bounds.x -= 4.0F;
    if (!render_both(*full, *partial, scene)) return;
    modelled = compute_scene_damage(previous, scene, scale);
    stats = partial->frame_stats();
    expect(!stats.full_repaint && modelled.area() > 0 &&
               stats.damage_area_pixels > modelled.area(),
           "a rotated path inside a faded group is enclosed with its whole layer",
           describe(stats) + " modelled_px=" + std::to_string(modelled.area()));
    compare("a moved rotated path inside a faded group", *full, *partial);
}

struct RulesOverride final {
    explicit RulesOverride(const hooks::PartialRedrawRules& rules)
        : saved(hooks::partial_redraw_rules()) {
        hooks::set_partial_redraw_rules(rules);
    }
    ~RulesOverride() { hooks::set_partial_redraw_rules(saved); }
    RulesOverride(const RulesOverride&) = delete;
    RulesOverride& operator=(const RulesOverride&) = delete;
    hooks::PartialRedrawRules saved;
};

struct SampleCountOverride final {
    explicit SampleCountOverride(std::uint32_t count) : saved(hooks::internal_sample_count()) {
        hooks::set_internal_sample_count(count);
    }
    ~SampleCountOverride() { hooks::set_internal_sample_count(saved); }
    SampleCountOverride(const SampleCountOverride&) = delete;
    SampleCountOverride& operator=(const SampleCountOverride&) = delete;
    std::uint32_t saved;
};

Scene plain_scene(float width, float height) {
    Scene scene;
    scene.scene_version = kDamageModelSceneVersion;
    scene.width = width;
    scene.height = height;
    scene.background = Color::hex(0x000000);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, width, height}};
    return scene;
}

bool identical(SkiaDawnSurface& full, SkiaDawnSurface& partial, std::string& message) {
    const auto expected = capture(full);
    const auto actual = capture(partial);
    if (!expected.ok || !actual.ok) {
        message = expected.error + actual.error;
        return false;
    }
    const auto verdict = harness::assert_identical(expected.result, actual.result);
    message = verdict.message;
    return static_cast<bool>(verdict);
}

struct EditOutcome final {
    bool rendered = false;
    bool identical = false;
    std::string message;
    FrameStats stats;
    std::int64_t modelled = 0;
};

EditOutcome redraw_edit(const Scene& before, const Scene& after, float scale) {
    EditOutcome outcome;
    auto full = make_surface(400, 300, scale);
    auto partial = make_surface(400, 300, scale);
    if (!full || !partial) return outcome;
    if (!render_both(*full, *partial, before) || !render_both(*full, *partial, after)) {
        return outcome;
    }
    outcome.rendered = true;
    outcome.stats = partial->frame_stats();
    outcome.modelled = compute_scene_damage(before, after, scale).area();
    outcome.identical = identical(*full, *partial, outcome.message);
    return outcome;
}

std::string outcome_detail(const EditOutcome& outcome) {
    return describe(outcome.stats) + " modelled_px=" + std::to_string(outcome.modelled) + " " +
           outcome.message;
}

void path_corner_enclosure() {
    const auto build = [](Color nudge_fill) {
        Scene scene = plain_scene(400.0F, 300.0F);
        SceneNode zig = path_node("zig", {120.0F, 80.0F, 0.0F, 0.0F}, Color::hex(0x38BDF8),
                                  6.0F, Color::hex(0xFDE68A));
        zig.transform = rotation_degrees(25.0F);
        auto large = std::make_shared<PathGeometry>(*zig.path);
        for (auto& value : large->values) value *= 2.5F;
        zig.path = large;
        scene.root.children.push_back(zig);
        scene.root.children.push_back({
            .id = "nudge",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {110.0F, 70.0F, 14.0F, 14.0F},
            .fill = nudge_fill,
        });
        return scene;
    };
    const Scene before = build(Color::hex(0xF59E0B));
    const Scene after = build(Color::hex(0x10B981));
    const auto enclosed = redraw_edit(before, after, 2.0F);
    expect(enclosed.rendered && !enclosed.stats.full_repaint && enclosed.identical &&
               enclosed.stats.damage_area_pixels > 4 * enclosed.modelled,
           "damage meeting a corner of an unchanged path redraws the whole path",
           outcome_detail(enclosed));
    RulesOverride off({.enclose_paths = false});
    const auto cut = redraw_edit(before, after, 2.0F);
    expect(cut.rendered && !cut.stats.full_repaint && !cut.identical &&
               cut.stats.damage_area_pixels == cut.modelled,
           "negative control: without whole-path enclosure the cut path is not byte-identical",
           outcome_detail(cut));
}

void self_layer_enclosure() {
    const auto build = [](Color nudge_fill) {
        Scene scene = plain_scene(400.0F, 300.0F);
        scene.root.children.push_back({
            .id = "layered",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {120.0F, 80.0F, 100.0F, 80.0F},
            .fill = Color::hex(0x38BDF8),
            .box_shadows = {{.offset_x = 4.0F, .offset_y = 4.0F, .blur_radius = 6.0F,
                             .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.5F)},
                            {.offset_x = -3.0F, .offset_y = 2.0F, .blur_radius = 10.0F,
                             .color = Color::rgba(1.0F, 0.0F, 0.0F, 0.5F)}},
            .corner_radius = 9.0F,
            .opacity = 0.55F,
        });
        scene.root.children.push_back({
            .id = "nudge",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {90.0F, 90.0F, 14.0F, 14.0F},
            .fill = nudge_fill,
        });
        return scene;
    };
    const Scene before = build(Color::hex(0xF59E0B));
    const Scene after = build(Color::hex(0x10B981));
    const auto enclosed = redraw_edit(before, after, 2.0F);
    expect(enclosed.rendered && !enclosed.stats.full_repaint && enclosed.identical &&
               enclosed.stats.damage_area_pixels > enclosed.modelled,
           "damage cutting a self-layered rectangle redraws its whole layer",
           outcome_detail(enclosed));
    RulesOverride off({.enclose_own_layers = false});
    const auto cut = redraw_edit(before, after, 2.0F);
    expect(cut.rendered && !cut.stats.full_repaint && !cut.identical,
           "negative control: a cut self-layered rectangle is not byte-identical",
           outcome_detail(cut));
}

void empty_path_extent() {
    const auto build = [](Color nudge_fill) {
        Scene scene = plain_scene(400.0F, 300.0F);
        scene.root.children.push_back({
            .id = "empty",
            .kind = SceneNode::Kind::path,
            .bounds = {40.0F, 40.0F, 0.0F, 0.0F},
            .fill = Color::hex(0x38BDF8),
            .path = std::make_shared<const PathGeometry>(),
        });
        scene.root.children.push_back({
            .id = "nudge",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {30.0F, 30.0F, 20.0F, 20.0F},
            .fill = nudge_fill,
        });
        return scene;
    };
    const auto outcome = redraw_edit(build(Color::hex(0xF59E0B)), build(Color::hex(0x10B981)),
                                     2.0F);
    expect(outcome.rendered && !outcome.stats.full_repaint && outcome.identical &&
               outcome.stats.damage_area_pixels == outcome.modelled,
           "a path with no commands encloses nothing", outcome_detail(outcome));
}

void interpolated_fill_sweep() {
    const auto sweep = [](std::string& first) {
        auto full = make_surface(400, 300, 2.0F);
        auto partial = make_surface(400, 300, 2.0F);
        if (!full || !partial) return -1;
        const Scene scene = base_scene(400.0F, 300.0F);
        std::string error;
        if (!full->render(scene, &error)) return -1;
        int differing = 0;
        for (std::int32_t edge = 20; edge <= 280; ++edge) {
            DamageRegion damage(frame_rect(scene, 2.0F), {.full_repaint_fraction = 2.0});
            damage.add({edge, 0, 800, 600});
            if (!partial->render(scene, &error) || !partial->render(scene, damage, &error)) {
                return -1;
            }
            std::string message;
            if (!identical(*full, *partial, message)) {
                if (differing == 0) first = "edge " + std::to_string(edge) + ": " + message;
                ++differing;
            }
        }
        return differing;
    };
    std::string first;
    const int enclosed = sweep(first);
    expect(enclosed == 0,
           "a damage edge swept across gradient and mesh fills leaves every frame byte-identical",
           std::to_string(enclosed) + " of 261 edges differ " + first);
    RulesOverride off({.enclose_interpolated_fills = false});
    first.clear();
    const int cut = sweep(first);
    expect(cut > 0,
           "negative control: without fill enclosure some swept edges are not byte-identical",
           std::to_string(cut) + " of 261 edges differ; first " + first);
}

struct SweepCount final {
    int differing = 0;
    int partial = 0;
    int edges = 0;
    std::string first;
};

// Cuts `scene` with a damage rectangle whose left edge, then top edge, steps
// across the frame one device pixel at a time, comparing each frame with a
// full redraw.
SweepCount sweep_damage_edges(const Scene& scene, float scale, bool layer_cache = false) {
    SweepCount count;
    const auto width = static_cast<std::uint32_t>(scene.width);
    const auto height = static_cast<std::uint32_t>(scene.height);
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale, layer_cache);
    if (!full || !partial) return {.differing = -1};
    std::string error;
    if (!full->render(scene, &error)) return {.differing = -1, .first = error};
    const PixelRect frame = frame_rect(scene, scale);
    for (const bool vertical : {true, false}) {
        const std::int32_t extent = vertical ? frame.right : frame.bottom;
        for (std::int32_t edge = 1; edge < extent; ++edge) {
            DamageRegion damage(frame, {.full_repaint_fraction = 2.0});
            damage.add(vertical ? PixelRect{edge, 0, frame.right, frame.bottom}
                                : PixelRect{0, edge, frame.right, frame.bottom});
            if (!partial->render(scene, &error) || !partial->render(scene, damage, &error)) {
                return {.differing = -1, .first = error};
            }
            ++count.edges;
            if (!partial->frame_stats().full_repaint) ++count.partial;
            std::string message;
            if (!identical(*full, *partial, message)) {
                if (count.differing == 0) {
                    count.first = std::string(vertical ? "x=" : "y=") + std::to_string(edge) +
                                  ": " + message;
                }
                ++count.differing;
            }
        }
    }
    return count;
}

std::string sweep_detail(const SweepCount& count) {
    return std::to_string(count.differing) + " of " + std::to_string(count.edges) +
           " edges differ, " + std::to_string(count.partial) + " partial; first " + count.first;
}

Scene grouped_version(Scene scene) {
    scene.scene_version = kDamageModelSceneVersion;
    return scene;
}

Scene plain_mask_scene(float width, float height) {
    Scene scene = plain_scene(width, height);
    SceneNode masked{.id = "masked", .kind = SceneNode::Kind::group,
                     .bounds = {60.0F, 50.0F, 220.0F, 180.0F}};
    masked.children.push_back({.id = "masked/back", .kind = SceneNode::Kind::rectangle,
                               .bounds = {0.0F, 0.0F, 200.0F, 160.0F},
                               .fill = Color::rgba(0.22F, 0.74F, 0.97F, 0.8F)});
    masked.children.push_back({.id = "masked/front", .kind = SceneNode::Kind::rectangle,
                               .bounds = {40.5F, 30.25F, 150.0F, 120.0F},
                               .fill = Color::rgba(0.98F, 0.45F, 0.09F, 0.7F),
                               .corner_radius = 18.0F});
    masked.children.push_back({.id = "masked/mask", .kind = SceneNode::Kind::rectangle,
                               .bounds = {20.25F, 15.5F, 170.0F, 130.0F},
                               .fill = Color::rgba(1.0F, 1.0F, 1.0F, 0.6F),
                               .corner_radius = 40.0F,
                               .mask = MaskMode::alpha});
    scene.root.children.push_back(std::move(masked));
    return scene;
}

// The image fixture plus opaque linear-sampled images that are rotated,
// skewed, upscaled at fractional offsets and rounded, none of which composite
// their own layer.
Scene image_sweep_scene(float width, float height) {
    Scene scene = grouped_version(harness::fixtures::image(width, height));
    const auto& hash = scene.images.front()->content_hash;
    const auto image = [&](std::string id, Rect bounds) {
        return SceneNode{.id = std::move(id), .kind = SceneNode::Kind::image, .bounds = bounds,
                         .image = ImageReference{.content_hash = hash,
                                                 .sampling = ImageSampling::linear}};
    };
    auto turned = image("turned-opaque", {250.3F, 175.6F, 52.7F, 41.2F});
    turned.transform = rotation_degrees(17.0F);
    scene.root.children.push_back(std::move(turned));
    auto skewed = image("skewed", {320.4F, 170.2F, 44.0F, 44.0F});
    skewed.transform = Affine2D{.a = 1.1F, .b = 0.15F, .c = 0.35F, .d = 0.9F};
    scene.root.children.push_back(std::move(skewed));
    scene.root.children.push_back(image("upscaled", {12.37F, 236.81F, 97.3F, 51.9F}));
    auto rounded = image("rounded-linear", {130.6F, 232.2F, 88.0F, 60.0F});
    rounded.corner_radii = CornerRadii{14.0F, 3.0F, 22.0F, 9.5F};
    scene.root.children.push_back(std::move(rounded));
    return scene;
}

struct SweepCase final {
    std::string what;
    Scene scene;
    hooks::PartialRedrawRules off;
};

Scene gradient_kind_scene(GradientKind kind, GradientSpread spread) {
    Scene scene = plain_scene(400.0F, 300.0F);
    scene.scene_version = 8U;
    LinearGradient gradient;
    gradient.kind = kind;
    gradient.spread = spread;
    // Keep the sweep asymmetric so ellipse, focal and conic-angle lowering are
    // exercised instead of falling through the circular/default cases.
    gradient.angle_degrees = kind == GradientKind::conic ? 47.0F : 103.0F;
    gradient.center_x = 0.37F;
    gradient.center_y = 0.62F;
    gradient.radius_x = 0.31F;
    gradient.radius_y = 0.57F;
    gradient.focal_x = 0.19F;
    gradient.focal_y = 0.74F;
    gradient.repeating = spread == GradientSpread::repeat;
    gradient.repeat_length = spread == GradientSpread::repeat ? 30.0F : 0.0F;
    gradient.stops = {{.position = 0.0F, .color = Color::hex(0xEF4444)},
                      {.position = 0.5F, .color = Color::hex(0xFACC15)},
                      {.position = 1.0F, .color = Color::hex(0x3B82F6)}};
    scene.root.children.push_back({
        .id = "gradient",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {60.0F, 50.0F, 220.0F, 160.0F},
        .fill = Color::hex(0x111827),
        .fill_gradients = {gradient},
    });
    return scene;
}

void gradient_kind_sweeps() {
    const struct { GradientKind kind; GradientSpread spread; const char* name; } cases[] = {
        {GradientKind::linear, GradientSpread::pad, "linear"},
        {GradientKind::radial, GradientSpread::pad, "radial"},
        {GradientKind::radial, GradientSpread::reflect, "radial reflect"},
        {GradientKind::two_circle_radial, GradientSpread::pad, "two-circle radial"},
        {GradientKind::conic, GradientSpread::pad, "conic"},
        {GradientKind::conic, GradientSpread::repeat, "conic repeat"},
    };
    for (const auto& item : cases) {
        for (const float scale : {1.0F, 2.0F}) {
            for (const bool cache : {false, true}) {
                const Scene scene = gradient_kind_scene(item.kind, item.spread);
                const auto count = sweep_damage_edges(scene, scale, cache);
                expect(count.differing == 0 && count.partial > 0,
                       std::string("a damage edge swept across a ") + item.name +
                           " gradient uses true partial redraw and leaves every frame byte-identical at " +
                           (scale == 1.0F ? "1x" : "2x") + (cache ? " with the layer cache" : ""),
                       std::to_string(count.differing) + " of " + std::to_string(count.edges) +
                           " edges differ " + count.first);
            }
        }
    }
    int cut_total = 0;
    int partial_total = 0;
    {
        RulesOverride off({.enclose_interpolated_fills = false});
        for (const auto& item : cases) {
            const Scene scene = gradient_kind_scene(item.kind, item.spread);
            const auto cut = sweep_damage_edges(scene, 2.0F, false);
            cut_total += std::max(cut.differing, 0);
            partial_total += cut.partial;
        }
    }
    expect(partial_total > 0 && cut_total > 0,
           "negative control: without fill enclosure a swept edge cuts some gradient kind",
           std::to_string(cut_total) + " edges differ across " + std::to_string(partial_total) +
               " partial frames");
}

void grouping_sweeps() {
    const std::vector<SweepCase> cases = {
        {"images", image_sweep_scene(400.0F, 300.0F),
         {.enclose_images = false}},
        {"nested clips", grouped_version(harness::fixtures::clip_nested(400.0F, 300.0F)),
         {.enclose_clips = false}},
        {"an alpha mask", plain_mask_scene(400.0F, 300.0F), {.enclose_group_layers = false}},
        {"a luminance mask", grouped_version(harness::fixtures::mask_luminance(400.0F, 300.0F)),
         {.enclose_interpolated_fills = false, .enclose_group_layers = false}},
        {"an isolated group", grouped_version(harness::fixtures::isolated_overlap(400.0F, 300.0F)),
         {.enclose_group_layers = false}},
    };
    for (const float scale : {1.0F, 2.0F}) {
        const std::string size = "@" + std::to_string(static_cast<int>(scale)) + " ";
        for (const auto& sweep : cases) {
            const auto enclosed = sweep_damage_edges(sweep.scene, scale);
            expect(enclosed.differing == 0 && enclosed.partial > 0,
                   size + "a damage edge swept across " + sweep.what +
                       " leaves every frame byte-identical",
                   sweep_detail(enclosed));
            RulesOverride off(sweep.off);
            const auto cut = sweep_damage_edges(sweep.scene, scale);
            expect(cut.differing > 0,
                   size + "negative control: without its enclosure rule a swept edge cuts " +
                       sweep.what + " visibly",
                   sweep_detail(cut));
        }
    }
}

std::shared_ptr<const ImageAsset> checker(char digit, std::uint8_t red, std::uint8_t blue) {
    std::vector<std::uint8_t> texels;
    for (int index = 0; index < 4; ++index) {
        const bool lit = index == 0 || index == 3;
        texels.insert(texels.end(), {lit ? red : std::uint8_t{20}, 60, lit ? blue : std::uint8_t{30},
                                     255});
    }
    return make_image_asset("sha256:" + std::string(64, digit), 2, 2, texels,
                            ImageAlpha::premultiplied);
}

std::shared_ptr<const PathGeometry> triangle_path(float width, float height) {
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close};
    path->values = {width * 0.5F, 0.0F, width, height, 0.0F, height};
    return path;
}

NodeClip viewport_clip() {
    return {.rect = {4.0F, 3.0F, 112.0F, 74.0F}, .radii = uniform_corner_radii(10.0F)};
}

SceneNode translucent_box(std::string id, Rect bounds, Color color, float alpha,
                          float radius = 0.0F) {
    color.alpha = alpha;
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = color, .corner_radius = radius};
}

Scene with_grouping_nodes(Scene scene) {
    scene.images = {checker('a', 240, 40), checker('b', 30, 220)};

    SceneNode viewport{.id = "viewport", .kind = SceneNode::Kind::rectangle,
                       .bounds = {30.0F, 30.0F, 120.0F, 80.0F}, .fill = Color::hex(0x1E293B),
                       .clip = viewport_clip()};
    for (int row = 0; row < 3; ++row) {
        viewport.children.push_back(translucent_box(
            "viewport/row-" + std::to_string(row),
            {-20.0F + 9.5F * static_cast<float>(row), 8.0F + 22.0F * static_cast<float>(row),
             170.0F, 14.0F},
            Color::hex(row % 2 == 0 ? 0x22D3EE : 0xFACC15), 0.85F, 4.0F));
    }
    SceneNode inner{.id = "viewport/inner", .kind = SceneNode::Kind::group,
                    .bounds = {60.0F, 20.0F, 60.0F, 44.0F},
                    .transform = rotation_degrees(20.0F),
                    .clip = NodeClip{.rect = {0.0F, 0.0F, 60.0F, 44.0F}}};
    inner.children.push_back(translucent_box("viewport/inner/bar", {-15.0F, 12.0F, 90.0F, 18.0F},
                                             Color::hex(0xF43F5E), 1.0F));
    viewport.children.push_back(std::move(inner));
    scene.root.children.push_back(std::move(viewport));

    SceneNode masked{.id = "masked", .kind = SceneNode::Kind::group,
                     .bounds = {205.0F, 28.0F, 150.0F, 100.0F}};
    masked.children.push_back(translucent_box("masked/back", {0.0F, 0.0F, 150.0F, 100.0F},
                                              Color::hex(0x38BDF8), 0.8F));
    masked.children.push_back(translucent_box("masked/front", {30.5F, 20.25F, 100.0F, 60.0F},
                                              Color::hex(0xF97316), 0.9F, 14.0F));
    auto mask = translucent_box("masked/mask", {12.25F, 10.5F, 120.0F, 76.0F},
                                Color::hex(0xFFFFFF), 0.7F, 30.0F);
    mask.mask = MaskMode::alpha;
    masked.children.push_back(std::move(mask));
    scene.root.children.push_back(std::move(masked));

    SceneNode isolated{.id = "isolated", .kind = SceneNode::Kind::group,
                       .bounds = {225.0F, 145.0F, 120.0F, 70.0F}, .isolate = true};
    const std::uint32_t colours[]{0xEF4444, 0x22C55E, 0x3B82F6};
    for (int index = 0; index < 3; ++index) {
        isolated.children.push_back(translucent_box(
            "isolated/disc-" + std::to_string(index),
            {28.0F * static_cast<float>(index), 9.0F * static_cast<float>(index), 56.0F, 50.0F},
            Color::hex(colours[index]), 0.6F, 24.0F));
    }
    scene.root.children.push_back(std::move(isolated));

    scene.root.children.push_back({
        .id = "photo",
        .kind = SceneNode::Kind::image,
        .bounds = {40.0F, 140.0F, 64.0F, 48.0F},
        .image = ImageReference{.content_hash = scene.images[0]->content_hash},
    });
    return scene;
}

Scene grouping_scene(float width, float height) {
    return with_grouping_nodes(base_scene(width, height));
}

std::vector<Step> grouping_steps() {
    return {
        {"move a clip", [](Scene& scene) { node(scene, "viewport").clip->rect.x += 7.5F; }},
        {"resize a clip", [](Scene& scene) {
             auto& clip = *node(scene, "viewport").clip;
             clip.rect.width -= 18.25F;
             clip.rect.height += 6.0F;
         }},
        {"move content past the clip edge", [](Scene& scene) {
             node(scene, "viewport/row-1").bounds.x += 23.0F;
         }},
        {"rotate a clipped node", [](Scene& scene) {
             node(scene, "viewport").transform = rotation_degrees(8.0F);
         }},
        {"move a nested clip under rotation", [](Scene& scene) {
             node(scene, "viewport/inner").clip->rect.y += 5.5F;
         }},
        {"move content inside the nested clip", [](Scene& scene) {
             node(scene, "viewport/inner/bar").bounds.x -= 9.0F;
         }},
        {"clip only the subtree", [](Scene& scene) {
             node(scene, "viewport").clip->clips_own_paint = false;
         }},
        {"remove a clip", [](Scene& scene) { node(scene, "viewport").clip.reset(); }},
        {"restore the clip", [](Scene& scene) { node(scene, "viewport").clip = viewport_clip(); }},
        {"clip to a path under rotation", [](Scene& scene) {
             node(scene, "viewport/inner").clip = NodeClip{.path = triangle_path(60.0F, 44.0F)};
         }},
        {"recolour the mask", [](Scene& scene) { node(scene, "masked/mask").fill.alpha = 0.4F; }},
        {"move the mask", [](Scene& scene) { node(scene, "masked/mask").bounds.x += 11.0F; }},
        {"fade the mask", [](Scene& scene) { node(scene, "masked/mask").opacity = 0.5F; }},
        {"recolour masked content", [](Scene& scene) {
             node(scene, "masked/front").fill = Color::hex(0x10B981);
         }},
        {"switch to a luminance mask", [](Scene& scene) {
             node(scene, "masked/mask").mask = MaskMode::luminance;
         }},
        {"stop masking", [](Scene& scene) { node(scene, "masked/mask").mask.reset(); }},
        {"mask again", [](Scene& scene) { node(scene, "masked/mask").mask = MaskMode::alpha; }},
        {"fade the masked group", [](Scene& scene) { node(scene, "masked").opacity = 0.7F; }},
        {"move inside an isolated group", [](Scene& scene) {
             node(scene, "isolated/disc-1").bounds.x += 6.0F;
         }},
        {"stop isolating", [](Scene& scene) { node(scene, "isolated").isolate = false; }},
        {"isolate again", [](Scene& scene) { node(scene, "isolated").isolate = true; }},
        {"move an image", [](Scene& scene) {
             node(scene, "photo").bounds.x += 5.5F;
             node(scene, "photo").bounds.y += 3.25F;
         }},
        {"swap an image's asset", [](Scene& scene) {
             node(scene, "photo").image->content_hash = scene.images[1]->content_hash;
         }},
        {"replace an asset's pixels", [](Scene& scene) { scene.images[1] = checker('b', 200, 90); }},
        {"rotate an image", [](Scene& scene) {
             node(scene, "photo").transform = rotation_degrees(12.0F);
         }},
        {"sample an image nearest", [](Scene& scene) {
             node(scene, "photo").image->sampling = ImageSampling::nearest;
         }},
        {"change nothing", [](Scene&) {}},
    };
}

void grouping_sequence(float scale, bool layer_cache) {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    const std::string size = std::string(layer_cache ? "cache on " : "cache off ") + "@" +
                             std::to_string(static_cast<int>(scale)) + " ";
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale, layer_cache);
    if (!full || !partial) return;
    Scene scene = grouping_scene(static_cast<float>(width), static_cast<float>(height));
    expect(scene.scene_version == kDamageModelSceneVersion &&
               partial_redraw_supported(scene.scene_version),
           size + "the grouping scene is a version 8 scene partial redraw serves");
    if (!render_both(*full, *partial, scene)) return;
    compare(size + "the first grouping frame", *full, *partial);
    // Removing or restoring the viewport's clip changes enough of this dense
    // frame, once enclosed, to cross the repaint threshold.
    const std::set<std::string> crossing = {"remove a clip", "restore the clip"};
    for (const auto& step : grouping_steps()) {
        const Scene previous = scene;
        step.edit(scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        const auto modelled = compute_scene_damage(previous, scene, scale).stats();
        const std::string percentages =
            "modelled " + std::to_string(modelled.frame_fraction * 100.0) + "%, drawn " +
            std::to_string(stats.damage_fraction * 100.0) + "% " + describe(stats);
        if (crossing.contains(step.name)) {
            expect(stats.full_repaint &&
                       stats.full_repaint_reason == FullRepaintReason::damage_threshold,
                   size + step.name + " crosses the repaint threshold once enclosed",
                   percentages);
        } else {
            expect(!stats.full_repaint && stats.full_repaint_reason == FullRepaintReason::none,
                   size + step.name + " repaints only its damage", percentages);
        }
        compare(size + step.name, *full, *partial);
    }
}

bool redraws_identically(const Scene& before, const Scene& after, const DamageRegion& damage,
                         float scale, std::string& message) {
    auto full = make_surface(400, 300, scale);
    auto partial = make_surface(400, 300, scale);
    if (!full || !partial) return false;
    std::string error;
    if (!full->render(before, &error) || !partial->render(before, &error) ||
        !full->render(after, &error) || !partial->render(after, damage, &error)) {
        message = error;
        return false;
    }
    return identical(*full, *partial, message);
}

PixelRect outset_pixels(const Rect& logical, float scale, std::int32_t margin) {
    const auto pixels = to_pixel_rect(logical, scale).value_or(PixelRect{});
    return {pixels.left - margin, pixels.top - margin, pixels.right + margin,
            pixels.bottom + margin};
}

Rect clip_box(const SceneNode& clipped) {
    return {clipped.bounds.x + clipped.clip->rect.x, clipped.bounds.y + clipped.clip->rect.y,
            clipped.clip->rect.width, clipped.clip->rect.height};
}

bool contains(const PixelRect& outer, const PixelRect& inner) {
    return outer.left <= inner.left && outer.top <= inner.top && outer.right >= inner.right &&
           outer.bottom >= inner.bottom;
}

// Each damage rule for clips and masks, with a control that drops the area
// the rule adds and leaves stale pixels.
void grouping_damage_controls() {
    constexpr float scale = 2.0F;
    Scene before = with_grouping_nodes(plain_scene(400.0F, 300.0F));

    Scene moved = before;
    node(moved, "viewport").clip->rect.x += 30.0F;
    const auto clip_damage = compute_scene_damage(before, moved, scale);
    std::string message;
    expect(redraws_identically(before, moved, clip_damage, scale, message),
           "moving a clip damages its old and new extents", message);
    DamageRegion new_extent_only = clip_damage;
    new_extent_only.intersect(outset_pixels(clip_box(node(moved, "viewport")), scale, 2));
    expect(new_extent_only.area() < clip_damage.area() &&
               !redraws_identically(before, moved, new_extent_only, scale, message),
           "negative control: damage limited to the new clip extent leaves stale pixels",
           message);

    Scene slid = before;
    node(slid, "viewport/row-1").bounds.x += 23.0F;
    const auto clip_pixels = outset_pixels(clip_box(node(before, "viewport")), scale, 2);
    const auto slid_damage = compute_scene_damage(before, slid, scale);
    expect(!slid_damage.empty() && contains(clip_pixels, slid_damage.bounds()) &&
               redraws_identically(before, slid, slid_damage, scale, message),
           "content moved past a clip edge damages only what the clip shows", message);
    Scene unclipped = before;
    node(unclipped, "viewport").clip.reset();
    Scene unclipped_slid = unclipped;
    node(unclipped_slid, "viewport/row-1").bounds.x += 23.0F;
    expect(!contains(clip_pixels, compute_scene_damage(unclipped, unclipped_slid, scale).bounds()),
           "negative control: without the clip the same move damages past the clip extent");

    Scene unmasked = before;
    node(unmasked, "masked/mask").mask.reset();
    const auto mask_damage = compute_scene_damage(before, unmasked, scale);
    expect(redraws_identically(before, unmasked, mask_damage, scale, message),
           "a mask that stops masking damages the siblings it revealed", message);
    const auto& mask = node(before, "masked/mask");
    DamageRegion own_only(frame_rect(before, scale));
    own_only.add(outset_pixels({node(before, "masked").bounds.x + mask.bounds.x,
                                node(before, "masked").bounds.y + mask.bounds.y,
                                mask.bounds.width, mask.bounds.height}, scale, 2));
    expect(own_only.area() < mask_damage.area() &&
               !redraws_identically(before, unmasked, own_only, scale, message),
           "negative control: damage limited to the mask's own box leaves its siblings stale",
           message);
}

Scene cross_pass_scene(bool with_path) {
    Scene scene = plain_scene(400.0F, 300.0F);
    scene.root.children.push_back({
        .id = "turned",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {50.0F, 50.0F, 90.0F, 50.0F},
        .fill = Color::hex(0xF472B6),
        .transform = rotation_degrees(23.0F),
    });
    scene.root.children.push_back({
        .id = "shadowed",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {210.0F, 50.0F, 70.0F, 40.0F},
        .fill = Color::hex(0x22C55E),
        .box_shadows = {{.offset_x = 3.0F, .offset_y = 4.0F, .blur_radius = 8.0F,
                         .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.6F)}},
    });
    scene.root.children.push_back({
        .id = "rounded",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {60.0F, 170.0F, 100.0F, 70.0F},
        .fill = Color::hex(0x38BDF8),
        .corner_radius = 17.5F,
    });
    if (with_path) {
        scene.root.children.push_back(path_node("distant", {300.0F, 200.0F, 0.0F, 0.0F},
                                                Color::hex(0xFACC15), 5.0F,
                                                Color::hex(0xF8FAFC)));
    }
    return scene;
}

int pixels_differing_outside_path(float scale) {
    auto without = make_surface(400, 300, scale);
    auto with = make_surface(400, 300, scale);
    if (!without || !with) return -1;
    const Scene plain = cross_pass_scene(false);
    const Scene pathed = cross_pass_scene(true);
    std::string error;
    if (!without->render(plain, &error) || !with->render(pathed, &error)) return -1;
    const auto a = capture(*without);
    const auto b = capture(*with);
    if (!a.ok || !b.ok) return -1;
    const auto box = path_paint_box(*pathed.root.children.back().path);
    const auto pixels = to_pixel_rect(Rect{300.0F + box->x, 200.0F + box->y, box->width,
                                           box->height}, scale);
    const auto width = static_cast<std::int32_t>(a.result.pixel_width);
    int differing = 0;
    for (std::size_t offset = 0; offset + 3 < a.result.rgba.size(); offset += 4) {
        const auto index = static_cast<std::int32_t>(offset / 4);
        const std::int32_t x = index % width;
        const std::int32_t y = index / width;
        if (x >= pixels->left - 2 && x < pixels->right + 2 && y >= pixels->top - 2 &&
            y < pixels->bottom + 2) {
            continue;
        }
        if (!std::equal(a.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset),
                        a.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset + 4),
                        b.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset))) {
            ++differing;
        }
    }
    return differing;
}

void multisampling_cross_pass() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string size = "@" + std::to_string(static_cast<int>(scale)) + " ";
        const int single = pixels_differing_outside_path(scale);
        expect(single == 0,
               size + "a distant path leaves every pixel outside its extent unchanged",
               std::to_string(single) + " pixels differ");
        SampleCountOverride msaa(4);
        const int multi = pixels_differing_outside_path(scale);
        expect(multi > 0,
               size + "negative control: at four samples a distant path changes other content",
               std::to_string(multi) + " pixels differ outside the path extent");
    }
}

std::shared_ptr<const PathGeometry> ellipse(float width, float height, float stroke_width) {
    constexpr float k = 0.5522847F;
    const float rx = width * 0.5F;
    const float ry = height * 0.5F;
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::cubic, PathVerb::cubic, PathVerb::cubic,
                   PathVerb::cubic, PathVerb::close};
    path->values = {
        rx * 2.0F, ry,
        rx * 2.0F, ry + k * ry, rx + k * rx, ry * 2.0F, rx, ry * 2.0F,
        rx - k * rx, ry * 2.0F, 0.0F, ry + k * ry, 0.0F, ry,
        0.0F, ry - k * ry, rx - k * rx, 0.0F, rx, 0.0F,
        rx + k * rx, 0.0F, rx * 2.0F, ry - k * ry, rx * 2.0F, ry,
    };
    path->stroke = {.color = Color::hex(0xFFFFFF), .width = stroke_width,
                    .join = StrokeJoin::miter, .cap = StrokeCap::butt};
    return path;
}

void large_path() {
    const auto build = [](float nudge_x) {
        Scene scene = plain_scene(640.0F, 480.0F);
        scene.root.children.push_back({
            .id = "large",
            .kind = SceneNode::Kind::path,
            .bounds = {20.0F, 20.0F, 0.0F, 0.0F},
            .fill = Color::hex(0x808080),
            .path = ellipse(360.0F, 260.0F, 8.0F),
        });
        scene.root.children.push_back({
            .id = "nudge",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {nudge_x, 140.0F, 16.0F, 16.0F},
            .fill = Color::hex(0xEF4444),
        });
        return scene;
    };
    auto full = make_surface(640, 480, 2.0F);
    auto partial = make_surface(640, 480, 2.0F);
    if (!full || !partial) return;
    const Scene before = build(8.0F);
    if (!render_both(*full, *partial, before)) return;
    const auto image = capture(*full);
    std::int64_t filled = 0;
    std::int64_t edge = 0;
    for (std::size_t offset = 0; offset + 3 < image.result.rgba.size(); offset += 4) {
        const auto red = image.result.rgba[offset];
        if (red == 0x80) ++filled;
        if (red > 0 && red < 0x80) ++edge;
    }
    expect(image.ok && filled > 200000 && edge > 1000,
           "a large stroked and filled path draws with anti-aliased edges",
           "filled_px=" + std::to_string(filled) + " edge_px=" + std::to_string(edge));
    const Scene after = build(14.0F);
    if (!render_both(*full, *partial, after)) return;
    const auto stats = partial->frame_stats();
    std::string message;
    expect(!stats.full_repaint && stats.damage_area_pixels > 4 * 32 * 32 &&
               identical(*full, *partial, message),
           "a large path redrawn whole under partial damage is byte-identical",
           describe(stats) + " " + message);
}

Scene without_stroke_reach(Scene scene, const std::string& id) {
    edit_path(scene, id).stroke.width = 0.0F;
    return scene;
}

void stroke_outset_negative_control() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    constexpr float scale = 2.0F;
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale);
    if (!full || !partial) return;
    const std::vector<Step> edits = {
        {"move a path", [](Scene& scene) { node(scene, "glyph").bounds.x += 13.5F; }},
        {"narrow and recolour a path's stroke", [](Scene& scene) {
             auto& path = edit_path(scene, "glyph");
             path.stroke.width = 2.0F;
             path.stroke.color = Color::hex(0x22D3EE);
         }},
    };
    Scene scene = compositing_scene(width, height);
    std::string error;
    for (const auto& edit : edits) {
        const Scene previous = scene;
        edit.edit(scene);
        const auto real = compute_scene_damage(previous, scene, scale);
        const auto stripped = compute_scene_damage(without_stroke_reach(previous, "glyph"),
                                                   without_stroke_reach(scene, "glyph"), scale);
        expect(full->render(previous, &error) && partial->render(previous, &error) &&
                   full->render(scene, &error) && partial->render(scene, real, &error),
               "negative control setup renders " + edit.name, error);
        const bool real_identical = compare("control: " + edit.name + " with the stroke outset",
                                            *full, *partial);
        expect(full->render(previous, &error) && partial->render(previous, &error) &&
                   full->render(scene, &error) && partial->render(scene, stripped, &error),
               "negative control renders " + edit.name + " without the stroke outset", error);
        const auto expected = capture(*full);
        const auto stale = capture(*partial);
        const auto verdict = harness::assert_identical(expected.result, stale.result);
        expect(real_identical && expected.ok && stale.ok && !verdict &&
                   stripped.area() < real.area(),
               "negative control: " + edit.name +
                   " without the stroke outset fails the byte-identity check",
               verdict.message + " damage_px=" + std::to_string(stripped.area()) + "/" +
                   std::to_string(real.area()));
    }
}

void explicit_damage() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    constexpr float scale = 2.0F;
    auto full = make_surface(width, height, scale);
    auto partial = make_surface(width, height, scale);
    if (!full || !partial) return;
    Scene scene = base_scene(width, height);
    std::string error;
    if (!full->render(scene, &error) || !partial->render(scene, &error)) {
        expect(false, "explicit damage setup renders", error);
        return;
    }
    const Scene previous = scene;
    node(scene, "swatch").fill = Color::hex(0xA855F7);
    const auto damage = compute_scene_damage(previous, scene, scale);
    expect(full->render(scene, &error) && partial->render(scene, damage, &error),
           "a caller-supplied damage region renders", error);
    const auto stats = partial->frame_stats();
    expect(!stats.full_repaint && stats.damage_area_pixels == damage.area(),
           "a caller-supplied damage region is the frame's damage", describe(stats));
    compare("a caller-supplied damage region", *full, *partial);

    const DamageRegion nothing{};
    expect(partial->render(scene, nothing, &error), "an empty damage region renders", error);
    expect(partial->frame_stats().paint_commands_recorded == 0 &&
               !partial->frame_stats().full_repaint,
           "an empty damage region records no paint commands", describe(partial->frame_stats()));
    compare("an empty damage region", *full, *partial);

    node(scene, "swatch").fill = Color::hex(0x14B8A6);
    partial->invalidate(InvalidationReason::state, "swatch");
    expect(full->render(scene, &error) && partial->render(scene, nothing, &error),
           "a caller region with a pending host invalidation renders", error);
    expect(!partial->frame_stats().full_repaint && partial->frame_stats().damage_area_pixels > 0,
           "a pending host invalidation joins the caller's region",
           describe(partial->frame_stats()));
    compare("a caller region that misses an invalidated node", *full, *partial);

    node(scene, "swatch").fill = Color::hex(0xEF4444);
    expect(full->render(scene, &error) && partial->render(scene, nothing, &error),
           "a stale damage region renders", error);
    const auto expected = capture(*full);
    const auto stale = capture(*partial);
    expect(expected.ok && stale.ok &&
               !harness::assert_identical(expected.result, stale.result),
           "negative control: damage that misses a change leaves the retained pixels stale");
    expect(partial->finish(&error), "finish waits for the GPU", error);
}

struct BlurReachOverride final {
    explicit BlurReachOverride(float sigmas) : saved(hooks::transformed_blur_sigmas()) {
        hooks::set_transformed_blur_sigmas(sigmas);
    }
    ~BlurReachOverride() { hooks::set_transformed_blur_sigmas(saved); }
    BlurReachOverride(const BlurReachOverride&) = delete;
    BlurReachOverride& operator=(const BlurReachOverride&) = delete;
    float saved;
};

Scene blurred_turn_scene(float angle, float turn_scale, Color nudge_fill, int nudge_x,
                         int nudge_y) {
    Scene scene = plain_scene(400.0F, 300.0F);
    scene.root.children.push_back({
        .id = "turned",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {120.0F, 80.0F, 100.0F, 80.0F},
        .fill = Color::hex(0x38BDF8),
        .box_shadows = {{.offset_x = 4.0F, .offset_y = 4.0F, .blur_radius = 6.0F,
                         .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.5F)},
                        {.offset_x = -3.0F, .offset_y = 2.0F, .blur_radius = 10.0F,
                         .color = Color::rgba(1.0F, 0.0F, 0.0F, 0.5F)}},
        .corner_radius = 9.0F,
        .transform = angle == 0.0F ? Affine2D{} : rotation_degrees(angle, turn_scale),
        .opacity = 0.55F,
    });
    scene.root.children.push_back({
        .id = "nudge",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {static_cast<float>(nudge_x), static_cast<float>(nudge_y), 14.0F, 14.0F},
        .fill = nudge_fill,
    });
    return scene;
}

struct TurnConfig final {
    float angle;
    float turn_scale;
    float device_scale;
};

int blurred_turn_differing(const TurnConfig& config, std::string& first) {
    auto full = make_surface(400, 300, config.device_scale);
    auto partial = make_surface(400, 300, config.device_scale);
    if (!full || !partial) return -1;
    int differing = 0;
    std::string error;
    for (int y = 60; y <= 170; y += 10) {
        for (int x = 90; x <= 230; x += 10) {
            const Scene before = blurred_turn_scene(config.angle, config.turn_scale,
                                                    Color::hex(0xF59E0B), x, y);
            const Scene after = blurred_turn_scene(config.angle, config.turn_scale,
                                                   Color::hex(0x10B981), x, y);
            if (!full->render(after, &error) || !partial->render(before, &error) ||
                !partial->render_incremental(after, &error)) {
                return -1;
            }
            std::string message;
            if (!identical(*full, *partial, message) && differing++ == 0) {
                first = "first at " + std::to_string(x) + "," + std::to_string(y) + ": " + message;
            }
        }
    }
    return differing;
}

std::int64_t blurred_turn_damage_area(const TurnConfig& config) {
    auto partial = make_surface(400, 300, config.device_scale);
    if (!partial) return -1;
    std::string error;
    const Scene before = blurred_turn_scene(config.angle, config.turn_scale,
                                            Color::hex(0xF59E0B), 90, 60);
    const Scene after = blurred_turn_scene(config.angle, config.turn_scale,
                                           Color::hex(0x10B981), 90, 60);
    if (!partial->render(before, &error) || !partial->render_incremental(after, &error)) {
        return -1;
    }
    return partial->frame_stats().damage_area_pixels;
}

void transformed_blur_reach_sweep() {
    std::vector<TurnConfig> configs;
    for (const float device_scale : {1.0F, 2.0F}) {
        for (const float turn_scale : {0.7F, 1.0F, 1.3F, 2.0F}) {
            for (const float angle : {9.0F, 30.0F, 45.0F, 120.0F}) {
                configs.push_back({angle, turn_scale, device_scale});
            }
        }
    }
    const auto sweep = [&configs](int& swept, std::string& first) {
        int bad = 0;
        swept = 0;
        for (const auto& config : configs) {
            std::string detail;
            const int differing = blurred_turn_differing(config, detail);
            ++swept;
            if (differing != 0 && bad++ == 0) {
                first = "angle " + std::to_string(config.angle) + " turn " +
                    std::to_string(config.turn_scale) + " @" +
                    std::to_string(static_cast<int>(config.device_scale)) + "x: " + detail;
            }
        }
        return bad;
    };
    int swept = 0;
    std::string first;
    const int enclosed = sweep(swept, first);
    expect(enclosed == 0,
           "damage cutting a rotated blurred-shadow rectangle's tail leaves every frame "
           "byte-identical",
           std::to_string(enclosed) + " of " + std::to_string(swept) + " configurations differ " +
               first);
    for (const float device_scale : {1.0F, 2.0F}) {
        const TurnConfig issue{9.0F, 1.0F, device_scale};
        const auto area = blurred_turn_damage_area(issue);
        BlurReachOverride legacy(3.0F);
        const auto legacy_area = blurred_turn_damage_area(issue);
        std::cout << "INFO damage area at " << static_cast<int>(device_scale)
                  << "x, 9 degrees: " << area << " px, old 1.5 x radius reach " << legacy_area
                  << " px\n";
    }
    {
        BlurReachOverride legacy(3.0F);
        std::string legacy_first;
        const int cut = sweep(swept, legacy_first);
        expect(cut > 0,
               "negative control: with the old 1.5 x radius reach some rotated blurred-shadow "
               "rectangles are not byte-identical",
               std::to_string(cut) + " of " + std::to_string(swept) +
                   " configurations differ " + legacy_first);
    }
    for (const float device_scale : {1.0F, 2.0F}) {
        std::string unturned;
        const int differing = blurred_turn_differing({0.0F, 1.0F, device_scale}, unturned);
        expect(differing == 0,
               "an untransformed blurred-shadow rectangle stays byte-identical under a damage "
               "sweep @" + std::to_string(static_cast<int>(device_scale)) + "x",
               std::to_string(differing) + " of 180 positions differ " + unturned);
    }
}

struct FadedShadowShape final {
    const char* name;
    float angle;
    float turn_scale;
};

Scene faded_shadow_scene(const FadedShadowShape& shape, float blur, bool padded) {
    Scene scene = plain_scene(220.0F, 170.0F);
    SceneNode group{.id = "faded", .kind = SceneNode::Kind::group,
                    .bounds = {70.0F, 40.0F, 120.0F, 100.0F}, .opacity = 0.55F};
    group.children.push_back({.id = "faded/back", .kind = SceneNode::Kind::rectangle,
                              .bounds = {0.0F, 0.0F, 30.0F, 24.0F},
                              .fill = Color::hex(0x6366F1)});
    if (padded) {
        group.children.push_back({.id = "faded/pad", .kind = SceneNode::Kind::rectangle,
                                  .bounds = {-70.0F, -40.0F, 220.0F, 170.0F},
                                  .fill = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F)});
    }
    group.children.push_back({
        .id = "faded/shadowed",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {20.0F, 14.0F, 56.0F, 40.0F},
        .fill = Color::hex(0x38BDF8),
        .box_shadows = {{.offset_x = 3.0F, .offset_y = 4.0F, .blur_radius = blur,
                         .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.9F)},
                        {.offset_x = -2.0F, .offset_y = 2.0F, .blur_radius = blur * 0.6F,
                         .color = Color::rgba(1.0F, 0.0F, 0.0F, 0.9F)}},
        .corner_radius = 7.0F,
        .transform = shape.angle == 0.0F && shape.turn_scale == 1.0F
            ? Affine2D{} : rotation_degrees(shape.angle, shape.turn_scale),
    });
    scene.root.children.push_back(std::move(group));
    return scene;
}

// Renders the group with room to spare and counts the painted pixels that fall
// outside the layer the group would get from `layer_blur`'s paint extents.
// Those are the pixels a layer sized that tightly cuts off.
int faded_shadow_clipped_pixels(const FadedShadowShape& shape, float blur, float layer_blur,
                                float device_scale, std::string& message) {
    const auto commands = make_paint_commands(faded_shadow_scene(shape, layer_blur, false));
    const auto layer = std::find_if(commands.begin(), commands.end(), [](const PaintCommand& command) {
        return command.kind == PaintCommand::Kind::begin_layer && command.node_id == "faded";
    });
    if (layer == commands.end() || layer->layer_unbounded) {
        message = "the faded group has no bounded layer";
        return -1;
    }
    const auto layer_pixels = to_pixel_rect(
        {layer->bounds.x - 1.0F, layer->bounds.y - 1.0F, layer->bounds.width + 2.0F,
         layer->bounds.height + 2.0F},
        device_scale);
    auto surface = make_surface(220, 170, device_scale);
    std::string error;
    if (!layer_pixels || !surface ||
        !surface->render(faded_shadow_scene(shape, blur, true), &error)) {
        message = error;
        return -1;
    }
    const auto result = capture(*surface);
    if (!result.ok) {
        message = result.error;
        return -1;
    }
    const auto width = static_cast<std::int32_t>(result.result.pixel_width);
    int clipped = 0;
    for (std::size_t i = 0; i + 3 < result.result.rgba.size(); i += 4) {
        const auto pixel = static_cast<std::int32_t>(i / 4);
        const std::int32_t x = pixel % width;
        const std::int32_t y = pixel / width;
        const bool inside = x >= layer_pixels->left && x < layer_pixels->right &&
                            y >= layer_pixels->top && y < layer_pixels->bottom;
        const bool painted = result.result.rgba[i] != 0 || result.result.rgba[i + 1] != 0 ||
                             result.result.rgba[i + 2] != 0;
        if (!inside && painted && clipped++ == 0) {
            message = "first at (" + std::to_string(x) + ", " + std::to_string(y) + ")";
        }
    }
    return clipped;
}

int faded_shadow_clip_sweep(float device_scale, float layer_fraction, int& swept,
                            std::string& first) {
    const FadedShadowShape shapes[]{
        {"untransformed", 0.0F, 1.0F}, {"rotated 9", 9.0F, 1.0F},
        {"rotated 30", 30.0F, 1.0F},   {"rotated 45", 45.0F, 1.0F},
        {"scaled", 0.0F, 1.7F},        {"rotated 20 and scaled", 20.0F, 1.4F},
        {"rotated 45 and shrunk", 45.0F, 0.7F},
    };
    int clipped = 0;
    for (const auto& shape : shapes) {
        for (const float blur : {6.0F, 16.0F, 30.0F}) {
            ++swept;
            std::string message;
            if (faded_shadow_clipped_pixels(shape, blur, blur * layer_fraction, device_scale,
                                            message) != 0 &&
                clipped++ == 0) {
                first = std::string(shape.name) + " blur " +
                        std::to_string(static_cast<int>(blur)) + ": " + message;
            }
        }
    }
    return clipped;
}

void faded_group_shadow_tails() {
    const FadedShadowShape shapes[]{
        {"untransformed", 0.0F, 1.0F},
        {"rotated", 30.0F, 1.0F},
        {"scaled", 0.0F, 1.7F},
        {"rotated and scaled", 20.0F, 1.4F},
    };
    for (const float device_scale : {1.0F, 2.0F}) {
        const std::string scale_label = "@" + std::to_string(static_cast<int>(device_scale)) + "x";
        for (const bool cached : {false, true}) {
            const std::string cache_label = std::string("cache ") + (cached ? "on " : "off ");
            for (const auto& shape : shapes) {
                const auto sweep = sweep_damage_edges(faded_shadow_scene(shape, 16.0F, false),
                                                      device_scale, cached);
                const bool stays_partial = shape.turn_scale != 1.7F && shape.turn_scale != 1.4F;
                expect(sweep.differing == 0 && (!stays_partial || sweep.partial > 0),
                       std::string(shape.name) + " blurred shadow in a faded group, " +
                           cache_label + scale_label +
                           ": damage cutting the tail stays byte-identical to a full redraw",
                       sweep_detail(sweep));
            }
        }
        int swept = 0;
        std::string first;
        const int clipped = faded_shadow_clip_sweep(device_scale, 1.0F, swept, first);
        expect(clipped == 0,
               "a faded group's layer holds every painted pixel of its blurred shadows " +
                   scale_label,
               std::to_string(clipped) + " of " + std::to_string(swept) +
                   " shapes paint outside the layer; first " + first);
        int control_swept = 0;
        std::string control_first;
        const int control = faded_shadow_clip_sweep(device_scale, 2.0F / 3.0F, control_swept,
                                                    control_first);
        expect(control > 0,
               "negative control: a layer sized by two sigma leaves painted shadow outside it " +
                   scale_label,
               std::to_string(control) + " of " + std::to_string(control_swept) +
                   " shapes paint outside it");
    }
}

}  // namespace

int main() {
    try {
        std::string error;
        if (!harness::prepare_device(&error)) {
            std::cerr << "FAIL device bootstrap: " << error << '\n';
            return 1;
        }
        scripted_sequence(400, 300, 1.0F);
        scripted_sequence(400, 300, 2.0F);
        host_invalidation();
        recreation_and_failure();
        explicit_damage();
        capture_after_failure();
        mismatched_damage_frame();
        device_loss();
        command_extents();
        damage_model_version_gate();
        compositing_sequence(400, 300, 1.0F);
        compositing_sequence(400, 300, 2.0F);
        cached_compositing_sequence(400, 300, 1.0F);
        cached_compositing_sequence(400, 300, 2.0F);
        path_scenes();
        whole_path_enclosure();
        stroke_outset_negative_control();
        path_corner_enclosure();
        empty_path_extent();
        self_layer_enclosure();
        interpolated_fill_sweep();
        gradient_kind_sweeps();
        multisampling_cross_pass();
        large_path();
        transformed_blur_reach_sweep();
        grouping_sweeps();
        faded_group_shadow_tails();
        for (const bool cached : {false, true}) {
            grouping_sequence(1.0F, cached);
            grouping_sequence(2.0F, cached);
        }
        grouping_damage_controls();
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "partial presentation " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
