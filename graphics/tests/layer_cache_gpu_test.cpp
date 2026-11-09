#include "skia_dawn_surface_test_access.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/layer_cache.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
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

std::string describe(const LayerCacheStats& stats) {
    std::ostringstream out;
    out << "candidates=" << stats.candidates << " layers=" << stats.layers
        << " pixels=" << stats.layer_pixels << " hits=" << stats.hits
        << " builds=" << stats.builds << " from_cache=" << stats.commands_from_cache
        << " refused=" << stats.refused_over_budget << " invalidations={";
    for (std::size_t index = 0; index < kLayerInvalidationCount; ++index) {
        if (stats.invalidations[index] == 0U) continue;
        out << to_string(static_cast<LayerInvalidation>(index)) << ':'
            << stats.invalidations[index] << ' ';
    }
    out << '}';
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

// The policy with the static and expensive triggers on.
LayerCachePolicy inexact() {
    LayerCachePolicy policy;
    policy.allow_inexact = true;
    return policy;
}

LayerCachePolicy uncached() {
    LayerCachePolicy policy;
    policy.enabled = false;
    return policy;
}

std::unique_ptr<SkiaDawnSurface> make_surface(std::uint32_t width, std::uint32_t height,
                                              float scale, const LayerCachePolicy& policy) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = scale}, &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    if (surface) surface->set_layer_cache_policy(policy);
    return surface;
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

harness::PixelComparison difference(SkiaDawnSurface& expected, SkiaDawnSurface& actual,
                                    int tolerance) {
    const auto left = capture(expected);
    const auto right = capture(actual);
    if (!left.ok || !right.ok) {
        expect(false, "captures succeed", left.error + right.error);
        return {};
    }
    return harness::compare_pixels(left.result.rgba, left.result.pixel_width,
                                   left.result.pixel_height, right.result.rgba,
                                   right.result.pixel_width, right.result.pixel_height,
                                   tolerance);
}

std::string describe(const harness::PixelComparison& comparison) {
    std::ostringstream out;
    out << "same_size=" << comparison.same_size << " differing=" << comparison.differing_pixels
        << '/' << comparison.pixel_count << " max_delta=" << comparison.max_channel_delta;
    if (comparison.first) {
        out << " first=(" << comparison.first->x << ',' << comparison.first->y << ')';
    }
    return out.str();
}

bool render(SkiaDawnSurface& surface, const Scene& scene, bool incremental = false) {
    std::string error;
    const bool ok = incremental ? surface.render_incremental(scene, &error)
                                : surface.render(scene, &error);
    if (!ok) expect(false, "render succeeds", error);
    return ok;
}

// The per-channel difference the layered scene's static and expensive images
// measure against drawing their commands directly. It is a measurement of
// this scene, not a bound: the error grows with the number of overlapping
// translucent draws (see `inexact_error_grows_with_overlap`).
constexpr int kCompositeTolerance = 2;

std::shared_ptr<const TriangleMesh> triangle_mesh(std::uint32_t colour) {
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {
        {.x = 0.0F, .y = 0.0F, .premultiplied_rgba = colour},
        {.x = 60.0F, .y = 10.0F, .premultiplied_rgba = 0x00FF00FFU},
        {.x = 20.0F, .y = 70.0F, .premultiplied_rgba = 0x0000FFFFU},
    };
    mesh->indices = {0, 1, 2};
    return mesh;
}

// Groups the policy caches: `panel` (shadows, a gradient, text that fits its
// box and text that overflows it, and rounded rectangles) and `meter` (a rectangle and a mesh) once static, and from
// version 2 the faded group `fade` from its first frame. `mover` is a lone
// rectangle outside them, which is never cached.
Scene layered_scene(float width, float height, std::uint32_t version) {
    Scene scene = harness::fixtures::empty(width, height);
    scene.scene_version = version;
    SceneNode panel{.id = "panel", .bounds = {16.0F, 16.0F, 180.0F, 120.0F}};
    panel.children.push_back({
        .id = "panel/bg",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 180.0F, 120.0F},
        .fill = Color::hex(0x1F2937),
        .fill_gradients = {{.angle_degrees = 135.0F,
                            .stops = {{.position = 0.0F, .color = Color::rgba(1, 1, 1, 0.15F)},
                                      {.position = 1.0F, .color = Color::rgba(0, 0, 0, 0.0F)}}}},
        .box_shadows = {{.offset_x = 3.0F, .offset_y = 4.0F, .blur_radius = 8.0F,
                         .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.6F)}},
        .corner_radius = 10.0F,
    });
    panel.children.push_back({
        .id = "panel/title",
        .kind = SceneNode::Kind::text,
        .bounds = {12.0F, 10.0F, 150.0F, 18.0F},
        .text_runs = {{.text = "Cached panel",
                       .style = {.font_family = "Inter", .font_size = 14.0F,
                                 .color = Color::hex(0xF9FAFB)}}},
    });
    panel.children.push_back({
        .id = "panel/chip-a",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {12.0F, 44.0F, 50.0F, 24.0F},
        .fill = Color::hex(0x38BDF8),
        .corner_radius = 6.0F,
    });
    panel.children.push_back({
        .id = "panel/chip-b",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {72.0F, 44.0F, 50.0F, 24.0F},
        .fill = Color::hex(0xA3E635),
        .box_shadows = {{.offset_x = 1.0F, .offset_y = 2.0F, .blur_radius = 4.0F,
                         .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)}},
        .corner_radius = 4.0F,
    });
    // Text overflows its box; the image's extent must come from measuring it.
    panel.children.push_back({
        .id = "panel/overflow",
        .kind = SceneNode::Kind::text,
        .bounds = {150.0F, 84.0F, 24.0F, 14.0F},
        .text_runs = {{.text = "Text far wider than its box",
                       .style = {.font_family = "Inter", .font_size = 12.0F,
                                 .color = Color::hex(0xE5E7EB)}}},
    });
    scene.root.children.push_back(std::move(panel));

    SceneNode meter{.id = "meter", .bounds = {214.0F, 16.0F, 80.0F, 120.0F}};
    meter.children.push_back({
        .id = "meter/track",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 80.0F, 120.0F},
        .fill = Color::hex(0x374151),
        .corner_radius = 8.0F,
    });
    meter.children.push_back({
        .id = "meter/mesh",
        .kind = SceneNode::Kind::mesh,
        .bounds = {8.0F, 20.0F, 0.0F, 0.0F},
        .mesh = triangle_mesh(0xFF0000FFU),
    });
    scene.root.children.push_back(std::move(meter));

    if (version >= 2U) {
        SceneNode fade{.id = "fade", .bounds = {16.0F, 156.0F, 170.0F, 60.0F}, .opacity = 0.6F};
        fade.children.push_back({
            .id = "fade/bg",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 170.0F, 60.0F},
            .fill = Color::hex(0xF472B6),
            .box_shadows = {{.offset_y = 3.0F, .blur_radius = 6.0F,
                             .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)}},
            .corner_radius = 12.0F,
        });
        fade.children.push_back({
            .id = "fade/label",
            .kind = SceneNode::Kind::text,
            .bounds = {14.0F, 20.0F, 140.0F, 20.0F},
            .text_runs = {{.text = "Faded group",
                           .style = {.font_family = "Inter", .font_size = 16.0F,
                                     .color = Color::hex(0x111827)}}},
        });
        scene.root.children.push_back(std::move(fade));
    }
    scene.root.children.push_back({
        .id = "mover",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {width - 70.0F, height - 50.0F, 30.0F, 20.0F},
        .fill = Color::hex(0xFACC15),
        .corner_radius = 4.0F,
    });
    return scene;
}

void equality_with_and_without_caching() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = "@" + std::to_string(static_cast<int>(scale));
        for (const auto& fixture : harness::fixtures::all(width, height)) {
            auto plain = make_surface(width, height, scale, uncached());
            auto cached = make_surface(width, height, scale, {});
            if (!plain || !cached) return;
            int worst = 0;
            for (int frame = 1; frame <= 5; ++frame) {
                if (!render(*plain, fixture.scene) || !render(*cached, fixture.scene)) return;
                worst = std::max(worst, difference(*plain, *cached, 0).max_channel_delta);
            }
            // The fixtures are flat apart from the compositing fixture's
            // faded group, whose cached image is exact.
            expect(worst == 0, fixture.name + at + " is byte-identical with and without caching",
                   "max_delta=" + std::to_string(worst) + " " +
                       describe(cached->layer_cache_stats()));
        }

        const Scene scene = layered_scene(static_cast<float>(width), static_cast<float>(height), 2U);
        auto plain = make_surface(width, height, scale, uncached());
        auto cached = make_surface(width, height, scale, inexact());
        if (!plain || !cached) return;
        for (int frame = 1; frame <= 5; ++frame) {
            if (!render(*plain, scene) || !render(*cached, scene)) return;
            const auto stats = cached->layer_cache_stats();
            const auto exact = difference(*plain, *cached, 0);
            const auto bounded = difference(*plain, *cached, kCompositeTolerance);
            const std::string what = "layered" + at + " frame " + std::to_string(frame);
            if (frame == 1) {
                expect(stats.layers == 1U && stats.builds == 1U,
                       what + " caches only the faded group", describe(stats));
                expect(exact.differing_pixels == 0U,
                       what + " is byte-identical: a faded group's image is exact",
                       describe(exact));
            }
            if (frame == 4) {
                expect(stats.layers == 3U && stats.builds == 2U,
                       what + " caches the static panel and meter", describe(stats));
            }
            if (frame == 5) {
                expect(stats.hits == 3U && stats.builds == 0U && stats.commands_from_cache == 9U,
                       what + " draws every layer from its image", describe(stats));
            }
            expect(bounded.same_size && bounded.differing_pixels == 0U,
                   what + " matches the uncached draw within two levels per channel",
                   describe(exact));
        }
    }
}

// The default policy caches only faded groups, so a scene rendered many times
// is byte-identical to the uncached render on every frame.
void default_policy_is_exact() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    const Scene scene = layered_scene(width, height, 2U);
    auto plain = make_surface(width, height, 2.0F, uncached());
    auto cached = make_surface(width, height, 2.0F, {});
    if (!plain || !cached) return;
    int worst = 0;
    std::size_t layers = 0;
    for (int frame = 0; frame < 8; ++frame) {
        if (!render(*plain, scene) || !render(*cached, scene)) return;
        worst = std::max(worst, difference(*plain, *cached, 0).max_channel_delta);
        layers = std::max(layers, cached->layer_cache_stats().layers);
    }
    expect(layers == 1U && worst == 0,
           "the default policy caches the faded group and stays byte-identical",
           "layers=" + std::to_string(layers) + " max_delta=" + std::to_string(worst));
}

// Sixteen overlapping translucent rectangles in one static group: the error
// an inexact image adds grows with the overlap, which is why those triggers
// are opt-in.
void inexact_error_grows_with_overlap() {
    constexpr std::uint32_t width = 200;
    constexpr std::uint32_t height = 200;
    Scene scene = harness::fixtures::empty(width, height);
    scene.scene_version = 2U;
    SceneNode stack{.id = "stack", .bounds = {10.0F, 10.0F, 180.0F, 180.0F}};
    for (int index = 0; index < 16; ++index) {
        const float offset = static_cast<float>(index) * 6.0F;
        stack.children.push_back({
            .id = "stack/" + std::to_string(index),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {offset, offset, 80.0F, 80.0F},
            .fill = Color::rgba(0.9F, 0.4F + 0.03F * static_cast<float>(index), 0.2F, 0.13F),
            .corner_radius = 9.0F,
        });
    }
    scene.root.children.push_back(std::move(stack));
    auto plain = make_surface(width, height, 1.0F, uncached());
    auto cached = make_surface(width, height, 1.0F, inexact());
    if (!plain || !cached) return;
    for (int frame = 0; frame < 5; ++frame) {
        if (!render(*plain, scene) || !render(*cached, scene)) return;
    }
    const auto measured = difference(*plain, *cached, 0);
    std::cout << "INFO 16 overlapping translucent draws: " << describe(measured) << '\n';
    expect(cached->layer_cache_stats().layers == 1U && measured.max_channel_delta > 0 &&
               measured.max_channel_delta <= 8,
           "an inexact image differs by at most half a level per overlapping draw",
           describe(measured));
}

void cached_frames_are_deterministic() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    const Scene scene = layered_scene(width, height, 2U);
    auto first = make_surface(width, height, 2.0F, inexact());
    auto second = make_surface(width, height, 2.0F, inexact());
    if (!first || !second) return;
    for (int frame = 1; frame <= 4; ++frame) {
        if (!render(*first, scene) || !render(*second, scene)) return;
    }
    const auto built = capture(*first);
    if (!render(*first, scene)) return;
    const auto reused = capture(*first);
    expect(first->layer_cache_stats().hits == 3U, "the frame after a build reuses every image",
           describe(first->layer_cache_stats()));
    const auto same_frame = harness::assert_identical(built.result, reused.result);
    expect(static_cast<bool>(same_frame), "a frame drawn from images matches the frame that built them",
           same_frame.message);
    if (!render(*second, scene)) return;
    expect(difference(*first, *second, 0).differing_pixels == 0U,
           "two surfaces with the same history are byte-identical");
}

struct Edit final {
    std::string name;
    LayerInvalidation reason;
    std::function<void(Scene&)> edit_scene;
    std::function<void(SkiaDawnSurface&, SkiaDawnSurface&)> edit_surfaces;
};

void invalidation_matrix() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    float scale = 2.0F;
    Scene scene = layered_scene(width, height, 2U);
    auto plain = make_surface(width, height, scale, uncached());
    auto cached = make_surface(width, height, scale, inexact());
    if (!plain || !cached) return;
    const auto warm = [&]() {
        for (int frame = 0; frame < 4; ++frame) {
            if (!render(*plain, scene) || !render(*cached, scene)) return false;
        }
        return cached->layer_cache_stats().layers >= 2U;
    };
    std::string error;
    const std::vector<Edit> edits{
        {"a recoloured member", LayerInvalidation::content,
         [](Scene& s) { node(s, "panel/chip-a").fill = Color::hex(0xEF4444); }, {}},
        {"a moved member", LayerInvalidation::bounds,
         [](Scene& s) { node(s, "panel/chip-b").bounds.x += 5.0F; }, {}},
        {"a new member", LayerInvalidation::structure,
         [](Scene& s) {
             node(s, "panel").children.push_back({
                 .id = "panel/chip-c",
                 .kind = SceneNode::Kind::rectangle,
                 .bounds = {132.0F, 44.0F, 36.0F, 24.0F},
                 .fill = Color::hex(0xF97316),
             });
         }, {}},
        {"a replaced mesh", LayerInvalidation::resource,
         [](Scene& s) { node(s, "meter/mesh").mesh = triangle_mesh(0xFFFFFFFFU); }, {}},
        {"a recoloured faded member", LayerInvalidation::content,
         [](Scene& s) { node(s, "fade/bg").fill = Color::hex(0x22D3EE); }, {}},
        {"a removed subtree", LayerInvalidation::visibility,
         [](Scene& s) {
             std::erase_if(s.root.children, [](const SceneNode& child) {
                 return child.id == "meter";
             });
         }, {}},
        {"a host invalidation of a member", LayerInvalidation::host_invalidate, {},
         [](SkiaDawnSurface&, SkiaDawnSurface& surface) {
             surface.invalidate(InvalidationReason::state, "panel/title");
         }},
        {"a reload", LayerInvalidation::reload, {},
         [](SkiaDawnSurface&, SkiaDawnSurface& surface) {
             surface.invalidate(InvalidationReason::reload);
         }},
        {"a resize at the same scale", LayerInvalidation::resize, {},
         [&](SkiaDawnSurface& left, SkiaDawnSurface& right) {
             expect(left.resize(width, height, scale, &error) &&
                        right.resize(width, height, scale, &error),
                    "both surfaces resize", error);
         }},
        {"a new scale", LayerInvalidation::scale, {},
         [&](SkiaDawnSurface& left, SkiaDawnSurface& right) {
             scale = 1.0F;
             expect(left.resize(width, height, scale, &error) &&
                        right.resize(width, height, scale, &error),
                    "both surfaces rescale", error);
         }},
        {"a new policy", LayerInvalidation::policy, {},
         [](SkiaDawnSurface&, SkiaDawnSurface& surface) {
             surface.set_layer_cache_policy(inexact());
         }},
    };
    if (!warm()) {
        expect(false, "the layered scene caches before the matrix",
               describe(cached->layer_cache_stats()));
        return;
    }
    for (const auto& edit : edits) {
        const auto before = cached->layer_cache_stats().invalidated(edit.reason);
        if (edit.edit_scene) edit.edit_scene(scene);
        if (edit.edit_surfaces) edit.edit_surfaces(*plain, *cached);
        if (!render(*plain, scene) || !render(*cached, scene)) return;
        const auto stats = cached->layer_cache_stats();
        expect(stats.invalidated(edit.reason) > before,
               edit.name + " drops an image as " + std::string(to_string(edit.reason)),
               describe(stats));
        const auto bounded = difference(*plain, *cached, kCompositeTolerance);
        expect(bounded.same_size && bounded.differing_pixels == 0U,
               edit.name + " leaves no stale pixels", describe(bounded));
        if (!warm()) {
            expect(false, edit.name + ": the scene caches again", describe(stats));
            return;
        }
    }

    const auto fading = cached->layer_cache_stats();
    node(scene, "fade").opacity = 0.3F;
    if (!render(*plain, scene) || !render(*cached, scene)) return;
    const auto faded = cached->layer_cache_stats();
    expect(faded.hits == fading.layers && faded.builds == 0U &&
               faded.invalidations == fading.invalidations,
           "an opacity change alone reuses the faded group's image", describe(faded));
    expect(difference(*plain, *cached, kCompositeTolerance).differing_pixels == 0U,
           "the re-faded group draws at its new opacity");

    // A member whose paint fails fails the frame; every image is dropped
    // because one recorded in the failed frame may be incomplete.
    const auto failed_before = cached->layer_cache_stats().invalidated(LayerInvalidation::frame_failed);
    Scene broken = scene;
    node(broken, "panel").children.push_back({
        .id = "panel/broken",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 10.0F, 10.0F},
        .fill_gradients = {{.stops = {{.position = 0.0F, .color = Color::hex(0)}}}},
    });
    expect(!cached->render(broken, &error), "a one-stop gradient fails the frame");
    const auto after_failure = cached->layer_cache_stats();
    expect(after_failure.invalidated(LayerInvalidation::frame_failed) > failed_before &&
               after_failure.layers == 0U,
           "a failed frame drops every image", describe(after_failure));
    if (!render(*plain, scene) || !render(*cached, scene)) return;
    expect(difference(*plain, *cached, kCompositeTolerance).differing_pixels == 0U,
           "the frame after a failure has no stale pixels");
}

// A lost device never recovers: the surface fails every later frame. The host
// recreates the surface, which rebuilds the layers from scratch.
void device_loss_rebuild() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    const Scene scene = layered_scene(width, height, 2U);
    auto lost = make_surface(width, height, 2.0F, inexact());
    if (!lost) return;
    for (int frame = 0; frame < 5; ++frame) {
        if (!render(*lost, scene)) return;
    }
    const auto before = capture(*lost);
    const auto warm = lost->layer_cache_stats();
    lost->simulate_device_loss();
    std::string error;
    expect(!lost->render(scene, &error), "a lost device fails the frame", error);
    const auto gone = lost->layer_cache_stats();
    expect(gone.layers == 0U && gone.invalidated(LayerInvalidation::device_lost) == warm.layers,
           "device loss drops every image", describe(gone));

    auto rebuilt = make_surface(width, height, 2.0F, inexact());
    if (!rebuilt) return;
    for (int frame = 0; frame < 5; ++frame) {
        if (!render(*rebuilt, scene)) return;
    }
    const auto again = rebuilt->layer_cache_stats();
    expect(again.layers == warm.layers && again.hits == warm.layers,
           "a recreated surface rebuilds the same layers", describe(again));
    const auto after = capture(*rebuilt);
    if (!before.ok || !after.ok) {
        expect(false, "device-loss captures succeed", before.error + after.error);
        return;
    }
    const auto verdict = harness::assert_identical(before.result, after.result);
    expect(static_cast<bool>(verdict),
           "the rebuilt layers draw the frame the lost device drew", verdict.message);
}

// A scene partial redraw serves caches only exact images, so partial frames
// stay byte-identical to full repaints even when the two surfaces' cache
// histories differ (here a reload on one of them).
//
// Partial redraw serves scenes up to `kDamageModelSceneVersion`. While that is
// version 1, the scene here carries no layer at all: the only image that
// composites exactly is a faded group's, and opacity is a version-2 field, so
// `layered_scene` adds its faded group only from version 2. This test then
// checks that partial frames stay byte-identical with caching on and that no
// static or expensive subtree is cached; `exact_layer_inside_damage_rect`
// covers compositing an exact image inside a damage rectangle.
void partial_presentation_with_layers() {
    constexpr std::uint32_t width = 400;
    constexpr std::uint32_t height = 300;
    Scene scene = layered_scene(width, height, kDamageModelSceneVersion);
    auto full = make_surface(width, height, 2.0F, inexact());
    auto partial = make_surface(width, height, 2.0F, inexact());
    if (!full || !partial) return;
    std::size_t partial_frames = 0;
    std::size_t most_layers = 0;
    const auto frame = [&](const std::string& what) {
        if (!render(*full, scene) || !render(*partial, scene, true)) return;
        const auto stats = partial->frame_stats();
        if (!stats.full_repaint) ++partial_frames;
        most_layers = std::max({most_layers, partial->layer_cache_stats().layers,
                                full->layer_cache_stats().layers});
        const auto comparison = difference(*full, *partial, 0);
        expect(comparison.same_size && comparison.differing_pixels == 0U,
               what + " is byte-identical to a full redraw",
               describe(comparison) + " full=" + std::to_string(stats.full_repaint) + " " +
                   describe(partial->layer_cache_stats()));
    };
    for (int step = 1; step <= 6; ++step) {
        node(scene, "mover").bounds.x -= 9.0F;
        if (step == 4) partial->invalidate(InvalidationReason::reload);
        frame("moving a node outside the subtrees, frame " + std::to_string(step));
    }
    node(scene, "panel/chip-a").fill = Color::hex(0xEF4444);
    frame("recolouring a static member");
    for (int step = 1; step <= 4; ++step) {
        node(scene, "mover").bounds.y -= 7.0F;
        frame("moving again, frame " + std::to_string(step));
    }
    expect(partial_frames >= 8U, "most frames were partial",
           std::to_string(partial_frames));
    if constexpr (kDamageModelSceneVersion < 2U) {
        expect(most_layers == 0U,
               "a scene partial redraw serves carries no layer while it cannot carry opacity",
               "layers=" + std::to_string(most_layers));
    } else {
        expect(most_layers == 1U,
               "a scene partial redraw serves caches only its faded group",
               "layers=" + std::to_string(most_layers));
    }
}

// An exact image composited inside a damage rectangle is byte-identical to
// drawing its group directly. A scene partial redraw serves cannot carry
// opacity yet (see `partial_presentation_with_layers`), so this raises the
// surface's damage-model version to 2 and supplies the damage itself.
void exact_layer_inside_damage_rect() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    constexpr float scale = 2.0F;
    Scene scene = layered_scene(width, height, 2U);
    auto plain = make_surface(width, height, scale, uncached());
    auto partial = make_surface(width, height, scale, {});
    if (!plain || !partial) return;
    SkiaDawnSurfaceTestAccess::set_damage_model_version(*partial, 2U);
    if (!render(*plain, scene) || !render(*partial, scene)) return;
    expect(partial->layer_cache_stats().layers == 1U,
           "the faded group is cached from its first frame",
           describe(partial->layer_cache_stats()));

    // Device pixels at scale 2. The first rectangle covers the faded group and
    // its shadow, the second cuts through the group, and the third misses it.
    struct Step final {
        std::string what;
        PixelRect rect;
        float opacity = 0.0F;
    };
    const std::vector<Step> steps{
        {"a damage rectangle covering a re-faded group", {0, 280, 420, 472}, 0.3F},
        {"a damage rectangle cutting through the faded group", {100, 300, 240, 400}, 0.3F},
        {"a damage rectangle beside the faded group", {440, 40, 600, 200}, 0.3F},
        {"a damage rectangle covering a group faded back up", {0, 280, 420, 472}, 0.85F},
    };
    std::string error;
    for (const auto& step : steps) {
        node(scene, "fade").opacity = step.opacity;
        DamageRegion damage(frame_rect(scene, scale));
        damage.add(step.rect);
        if (!render(*plain, scene)) return;
        if (!partial->render(scene, damage, &error)) {
            expect(false, step.what + " renders", error);
            return;
        }
        const auto frame = partial->frame_stats();
        const auto layers = partial->layer_cache_stats();
        expect(!frame.full_repaint, step.what + " redraws only the damage",
               std::string(to_string(frame.full_repaint_reason)));
        expect(layers.layers == 1U && layers.hits == 1U && layers.builds == 0U,
               step.what + " reuses the group's image", describe(layers));
        const auto comparison = difference(*plain, *partial, 0);
        expect(comparison.same_size && comparison.differing_pixels == 0U,
               step.what + " is byte-identical to the uncached draw", describe(comparison));
    }
}

void budget_on_the_gpu() {
    constexpr std::uint32_t width = 320;
    constexpr std::uint32_t height = 240;
    const Scene scene = layered_scene(width, height, 2U);
    auto plain = make_surface(width, height, 2.0F, uncached());
    auto cached = make_surface(width, height, 2.0F, {.allow_inexact = true, .max_layers = 1});
    if (!plain || !cached) return;
    std::size_t refused = 0;
    for (int frame = 0; frame < 5; ++frame) {
        if (!render(*plain, scene) || !render(*cached, scene)) return;
        refused += cached->layer_cache_stats().refused_over_budget;
    }
    const auto stats = cached->layer_cache_stats();
    expect(stats.layers == 1U && refused == 2U,
           "max_layers holds one image and draws the rest directly", describe(stats));
    expect(difference(*plain, *cached, kCompositeTolerance).differing_pixels == 0U,
           "subtrees refused by the budget still draw");
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "SKIP no GPU device: " << error << '\n';
        return 77;
    }
    equality_with_and_without_caching();
    default_policy_is_exact();
    inexact_error_grows_with_overlap();
    cached_frames_are_deterministic();
    invalidation_matrix();
    device_loss_rebuild();
    partial_presentation_with_layers();
    exact_layer_inside_damage_rect();
    budget_on_the_gpu();
    std::cout << (failures == 0 ? "layer cache: all checks passed\n"
                                : "layer cache: " + std::to_string(failures) + " failed\n");
    return failures == 0 ? 0 : 1;
}
