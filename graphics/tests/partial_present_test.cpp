#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

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
    expect(partial_redraw_supported(kSceneVersion) &&
               partial_redraw_supported(kDamageModelSceneVersion) &&
               !partial_redraw_supported(kDamageModelSceneVersion + 1U) &&
               !partial_redraw_supported(3U),
           "partial redraw is enabled only for scene versions the damage model understands");
    expect(to_string(FullRepaintReason::damage_model_version) == "damage_model_version",
           "the damage-model version fallback has a telemetry name");
    auto partial = make_surface(400, 300, 1.0F);
    if (!partial) return;
    Scene scene = base_scene(400, 300);
    std::string error;
    expect(partial->render_incremental(scene, &error), "a supported scene renders", error);
    Scene newer = scene;
    newer.scene_version = 3U;
    if (scene_version_is_supported(newer.scene_version)) {
        expect(partial->render_incremental(newer, &error) &&
                   partial->frame_stats().full_repaint_reason ==
                       FullRepaintReason::damage_model_version,
               "a version-3 scene repaints fully", describe(partial->frame_stats()));
    } else {
        const auto before = partial->frame_stats().frame;
        expect(!partial->render_incremental(newer, &error) &&
                   partial->frame_stats().frame == before,
               "a version-3 scene this build cannot draw is refused before any redraw",
               describe(partial->frame_stats()));
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
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "partial presentation " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
