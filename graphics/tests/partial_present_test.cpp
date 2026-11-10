#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

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
                                              float scale) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = scale}, &error);
    expect(surface != nullptr, "offscreen surface is created", error);
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

void damage_model_version_gate() {
    expect(kDamageModelSceneVersion == 3U && partial_redraw_supported(3U) &&
               partial_redraw_supported(kDamageModelSceneVersion) &&
               !partial_redraw_supported(kDamageModelSceneVersion + 1U) &&
               !partial_redraw_supported(4U),
           "partial redraw covers scene versions up to 3 and refuses version 4");
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
               "a default version 3 scene takes the partial path, step " + std::to_string(step),
               describe(stats));
        compare("a default version 3 scene, step " + std::to_string(step), *full, *partial);
    }
    Scene beyond = scene;
    beyond.scene_version = kDamageModelSceneVersion + 1U;
    if (scene_version_is_supported(beyond.scene_version)) {
        node(beyond, "mover").bounds.x += 6.0F;
        if (!render_both(*full, *partial, beyond)) return;
        const auto stats = partial->frame_stats();
        expect(stats.full_repaint &&
                   stats.full_repaint_reason == FullRepaintReason::damage_model_version,
               "a version 4 scene is refused partial redraw and repaints fully", describe(stats));
        compare("a version 4 scene", *full, *partial);
    } else {
        std::string refused;
        const auto before = partial->frame_stats().frame;
        expect(!partial->render_incremental(beyond, &refused) &&
                   partial->frame_stats().frame == before,
               "a version 4 scene is refused before any redraw", refused);
    }
    Scene newer = scene;
    newer.scene_version = kSceneVersion + 1U;
    std::string error;
    const auto before = partial->frame_stats().frame;
    expect(!partial->render_incremental(newer, &error) &&
               partial->frame_stats().frame == before,
           "a scene version this build cannot draw is refused before any redraw",
           describe(partial->frame_stats()));
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
        if (step.expect_partial) {
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
        {"restroke a path", [](Scene& scene) {
             edit_path(scene, "glyph").stroke.width = 12.0F;
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
        path_scenes();
        whole_path_enclosure();
        stroke_outset_negative_control();
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "partial presentation " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
