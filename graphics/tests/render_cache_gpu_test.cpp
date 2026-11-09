#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

constexpr std::uint32_t kWidth = 320;
constexpr std::uint32_t kHeight = 200;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

constexpr RenderCacheBudgets kNoCaches{{0, 0}, {0, 0}, {0, 0}};

std::unique_ptr<SkiaDawnSurface> make_surface(bool cached) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = kWidth, .height = kHeight, .scale = 1.0F,
         .native_surface_handle = nullptr, .vsync = false},
        &error);
    if (!surface) throw std::runtime_error("surface creation failed: " + error);
    if (!cached) surface->set_cache_budgets(kNoCaches);
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

void render(SkiaDawnSurface& surface, const Scene& scene) {
    std::string error;
    if (!surface.render(scene, &error)) throw std::runtime_error("render failed: " + error);
}

// The uncached reference: `scene` drawn once on a fresh surface with every
// cache off.
std::vector<std::uint8_t> reference(const Scene& scene) {
    auto surface = make_surface(false);
    render(*surface, scene);
    return capture(*surface);
}

std::string first_difference(const std::vector<std::uint8_t>& expected,
                             const std::vector<std::uint8_t>& actual) {
    if (expected.size() != actual.size()) return "sizes differ";
    for (std::size_t index = 0; index < expected.size(); ++index) {
        if (expected[index] != actual[index]) {
            const auto pixel = index / 4U;
            std::ostringstream out;
            out << "first at (" << pixel % kWidth << ", " << pixel / kWidth << ") channel "
                << index % 4U << ": " << int(expected[index]) << " vs " << int(actual[index]);
            return out.str();
        }
    }
    return {};
}

void expect_identical(const std::vector<std::uint8_t>& expected,
                      const std::vector<std::uint8_t>& actual, const std::string& what) {
    const auto difference = first_difference(expected, actual);
    expect(difference.empty(), what, difference);
}

std::string describe(const CacheStats& stats) {
    std::ostringstream out;
    out << "hits=" << stats.hits << " misses=" << stats.misses
        << " frame_hits=" << stats.frame_hits << " frame_misses=" << stats.frame_misses
        << " evictions=" << stats.evictions << " trimmed=" << stats.trimmed
        << " refused=" << stats.refused << " entries=" << stats.entries
        << " bytes=" << stats.bytes;
    return out.str();
}

SceneNode text_node(const std::string& id, const std::string& text, float x, float y,
                    Color color = Color::hex(0xE5E7EB)) {
    return {
        .id = id,
        .kind = SceneNode::Kind::text,
        .bounds = {x, y, 140.0F, 18.0F},
        .text_runs = {{.text = text,
                       .style = {.font_family = "Inter", .font_size = 14.0F, .color = color}}},
    };
}

SceneNode gradient_node(const std::string& id, Rect bounds) {
    return {
        .id = id,
        .kind = SceneNode::Kind::rectangle,
        .bounds = bounds,
        .fill_gradients = {{.angle_degrees = 90.0F,
                            .stops = {{0.0F, Color::hex(0xF97316)},
                                      {1.0F, Color::hex(0x6366F1)}}}},
        .corner_radius = 6.0F,
    };
}

std::shared_ptr<MeshTexture> checker_texture(std::uint8_t tint) {
    auto texture = std::make_shared<MeshTexture>();
    texture->width = 8;
    texture->height = 8;
    texture->premultiplied_rgba.resize(8U * 8U * 4U);
    for (std::uint32_t y = 0; y < 8; ++y) {
        for (std::uint32_t x = 0; x < 8; ++x) {
            const bool on = ((x + y) % 2U) == 0U;
            auto* texel = &texture->premultiplied_rgba[(y * 8U + x) * 4U];
            texel[0] = on ? tint : 20;
            texel[1] = on ? 40 : 200;
            texel[2] = on ? 220 : static_cast<std::uint8_t>(255 - tint);
            texel[3] = 255;
        }
    }
    return texture;
}

SceneNode textured_quad(const std::string& id, std::shared_ptr<const MeshTexture> texture,
                        float x, float y) {
    auto quad = std::make_shared<TriangleMesh>();
    quad->vertices = {
        {.x = 0.0F, .y = 0.0F, .u = 0.0F, .v = 0.0F},
        {.x = 48.0F, .y = 0.0F, .u = 1.0F, .v = 0.0F},
        {.x = 48.0F, .y = 48.0F, .u = 1.0F, .v = 1.0F},
        {.x = 0.0F, .y = 48.0F, .u = 0.0F, .v = 1.0F},
    };
    quad->indices = {0, 1, 2, 0, 2, 3};
    quad->texture = std::move(texture);
    return {
        .id = id,
        .kind = SceneNode::Kind::mesh,
        .bounds = {x, y, 48.0F, 48.0F},
        .mesh = quad,
    };
}

Scene empty_scene(std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = static_cast<float>(kWidth);
    scene.height = static_cast<float>(kHeight);
    scene.background = Color::hex(0x111827);
    scene.root.id = "root";
    scene.scene_version = version;
    return scene;
}

// Three texts, one gradient, one textured mesh, and a faded group of two
// rectangles that opens one layer.
Scene telemetry_scene(const std::shared_ptr<const MeshTexture>& texture, bool all = true) {
    auto scene = empty_scene();
    scene.root.children.push_back(text_node("alpha", "Alpha", 12.0F, 12.0F));
    scene.root.children.push_back(text_node("beta", "Beta", 12.0F, 40.0F));
    if (all) {
        scene.root.children.push_back(text_node("gamma", "Gamma", 12.0F, 68.0F));
        scene.root.children.push_back(gradient_node("ramp", {170.0F, 12.0F, 120.0F, 40.0F}));
    }
    scene.root.children.push_back(textured_quad("quad", texture, 170.0F, 70.0F));
    SceneNode faded{
        .id = "faded",
        .kind = SceneNode::Kind::group,
        .bounds = {20.0F, 120.0F, 120.0F, 60.0F},
        .opacity = 0.5F,
    };
    faded.children.push_back({.id = "faded-a", .kind = SceneNode::Kind::rectangle,
                              .bounds = {0.0F, 0.0F, 80.0F, 40.0F},
                              .fill = Color::hex(0x22C55E)});
    faded.children.push_back({.id = "faded-b", .kind = SceneNode::Kind::rectangle,
                              .bounds = {40.0F, 20.0F, 80.0F, 40.0F},
                              .fill = Color::hex(0xEF4444)});
    scene.root.children.push_back(std::move(faded));
    return scene;
}

void fixtures_with_and_without_caches() {
    for (const auto& [name, scene] : harness::fixtures::all(kWidth, kHeight)) {
        auto cached = make_surface(true);
        render(*cached, scene);
        const auto cold = capture(*cached);
        render(*cached, scene);
        const auto warm = capture(*cached);
        const auto telemetry = cached->telemetry();
        const auto expected = reference(scene);
        expect_identical(expected, cold, name + ": a cold cached frame matches the uncached one");
        expect_identical(expected, warm, name + ": a warm cached frame matches the uncached one");
        const auto commands = make_paint_commands(scene);
        const bool has_text = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return c.kind == PaintCommand::Kind::text;
        });
        const bool has_gradient = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return !c.fill_gradients.empty();
        });
        const bool has_texture = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return c.mesh && c.mesh->texture;
        });
        expect((!has_text || (telemetry.paragraphs.frame_hits > 0 &&
                              telemetry.paragraphs.frame_misses == 0)) &&
                   (!has_gradient || (telemetry.gradients.frame_hits > 0 &&
                                      telemetry.gradients.frame_misses == 0)) &&
                   (!has_texture || (telemetry.textures.frame_hits > 0 &&
                                     telemetry.textures.frame_misses == 0)),
               name + ": the warm frame is drawn entirely from the caches",
               to_json(telemetry));
    }
}

// A second frame whose inputs differ from the first in one way a key must
// see. The cached surface draws the first frame and then the second; its
// second frame must match the second scene drawn without caches.
void expect_second_frame_exact(const std::string& what, const Scene& first,
                               const Scene& second) {
    auto cached = make_surface(true);
    render(*cached, first);
    render(*cached, second);
    expect_identical(reference(second), capture(*cached), what);
}

void keys_see_every_input() {
    {
        auto first = empty_scene();
        first.root.children.push_back(text_node("label", "Level", 30.0F, 30.0F));
        auto second = first;
        second.root.children[0].text_runs[0].style.color = Color::hex(0x38BDF8);
        expect_second_frame_exact("a text colour change redraws the paragraph", first, second);
        auto faded = first;
        faded.root.children[0].opacity = 0.4F;
        expect_second_frame_exact("a folded text opacity redraws the paragraph", first, faded);
        auto moved = first;
        moved.root.children[0].bounds.x = 140.0F;
        moved.root.children[0].bounds.y = 120.0F;
        auto cached = make_surface(true);
        render(*cached, first);
        render(*cached, moved);
        const auto paragraphs = cached->telemetry().paragraphs;
        expect_identical(reference(moved), capture(*cached),
                         "a moved paragraph is drawn from the cache at its new place");
        expect(paragraphs.frame_hits == 1 && paragraphs.frame_misses == 0,
               "a moved paragraph is a cache hit", describe(paragraphs));
        auto subpixel = moved;
        subpixel.root.children[0].bounds.x += 0.5F;
        subpixel.root.children[0].bounds.y += 0.25F;
        expect_second_frame_exact("a paragraph moved by a fraction of a pixel is exact", moved,
                                  subpixel);
    }
    {
        auto first = empty_scene();
        first.root.children.push_back(gradient_node("ramp", {40.0F, 40.0F, 160.0F, 80.0F}));
        auto faded = first;
        faded.root.children[0].opacity = 0.5F;
        expect_second_frame_exact("a folded gradient opacity rebuilds the shader", first, faded);
        auto moved = first;
        moved.root.children[0].bounds.x += 13.5F;
        expect_second_frame_exact("a moved gradient rebuilds the shader", first, moved);
        auto turned = first;
        turned.root.children[0].fill_gradients[0].angle_degrees = 45.0F;
        expect_second_frame_exact("a turned gradient rebuilds the shader", first, turned);
        auto repeating = first;
        repeating.root.children[0].fill_gradients[0].repeating = true;
        repeating.root.children[0].fill_gradients[0].repeat_length = 30.0F;
        expect_second_frame_exact("a repeating gradient rebuilds the shader", first, repeating);
    }
    {
        // A producer may rewrite texels in place behind the scene's const view.
        auto texture = checker_texture(200);
        auto first = empty_scene();
        first.root.children.push_back(textured_quad("quad", texture, 60.0F, 60.0F));
        auto cached = make_surface(true);
        render(*cached, first);
        texture->premultiplied_rgba[0] = 3;
        texture->premultiplied_rgba[1] = 250;
        render(*cached, first);
        const auto textures = cached->telemetry().textures;
        expect_identical(reference(first), capture(*cached),
                         "texels rewritten in place are uploaded again");
        expect(textures.frame_misses == 1 && textures.entries == 2,
               "rewritten texels are a different cache key", describe(textures));

        auto copy = std::make_shared<MeshTexture>(*texture);
        auto second = empty_scene();
        second.root.children.push_back(textured_quad("quad", copy, 90.0F, 30.0F));
        render(*cached, second);
        const auto again = cached->telemetry().textures;
        expect_identical(reference(second), capture(*cached),
                         "an equal texture in a new allocation is drawn from the cache");
        expect(again.frame_hits == 1 && again.frame_misses == 0,
               "an equal texture in a new allocation is a cache hit", describe(again));
    }
}

void partial_redraw_with_and_without_caches() {
    auto texture = checker_texture(120);
    auto scene = empty_scene(kDamageModelSceneVersion);
    scene.root.children.push_back(text_node("title", "Partial", 10.0F, 10.0F));
    scene.root.children.push_back(gradient_node("ramp", {150.0F, 10.0F, 140.0F, 50.0F}));
    scene.root.children.push_back(textured_quad("quad", texture, 40.0F, 90.0F));
    scene.root.children.push_back({.id = "mover", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {250.0F, 150.0F, 30.0F, 30.0F},
                                   .fill = Color::hex(0xF472B6)});
    // The mover's damage crosses the gradient at step 1 and the textured quad
    // at step 2, so those partial frames redraw them from the caches.
    const Rect path[] = {{250.0F, 150.0F, 30.0F, 30.0F},
                         {200.0F, 30.0F, 30.0F, 30.0F},
                         {50.0F, 100.0F, 30.0F, 30.0F},
                         {120.0F, 160.0F, 30.0F, 30.0F}};
    auto cached = make_surface(true);
    auto uncached = make_surface(false);
    std::string error;
    for (int step = 0; step < 4; ++step) {
        scene.root.children[3].bounds = path[step];
        if (step == 3) scene.root.children[0].text_runs[0].text = "Partial, again";
        const bool drawn = cached->render_incremental(scene, &error) &&
                           uncached->render_incremental(scene, &error);
        expect(drawn, "step " + std::to_string(step) + " renders incrementally", error);
        const auto stats = cached->frame_stats();
        const auto telemetry = cached->telemetry();
        if (step > 0) {
            expect(!stats.full_repaint && stats.damage_fraction > 0.0 &&
                       stats.damage_fraction < 1.0,
                   "step " + std::to_string(step) + " repaints only its damage",
                   to_json(telemetry));
        }
        expect(telemetry.frame == stats.frame &&
                   telemetry.full_repaint == stats.full_repaint &&
                   telemetry.full_repaint_reason == to_string(stats.full_repaint_reason) &&
                   telemetry.damage_area_pixels == stats.damage_area_pixels &&
                   telemetry.frame_area_pixels == stats.frame_area_pixels &&
                   telemetry.damage_fraction == stats.damage_fraction &&
                   telemetry.paint_commands_total == stats.paint_commands_total &&
                   telemetry.paint_commands_recorded == stats.paint_commands_recorded,
               "step " + std::to_string(step) + " telemetry carries the frame's damage");
        if (step == 1) {
            expect(telemetry.gradients.frame_hits == 1 && telemetry.gradients.frame_misses == 0,
                   "step 1 redraws the gradient from the cache", describe(telemetry.gradients));
        }
        if (step == 2) {
            expect(telemetry.textures.frame_hits == 1 && telemetry.textures.frame_misses == 0,
                   "step 2 redraws the texture from the cache", describe(telemetry.textures));
        }
        const auto expected = reference(scene);
        expect_identical(expected, capture(*uncached),
                         "step " + std::to_string(step) + " uncached partial redraw is exact");
        expect_identical(expected, capture(*cached),
                         "step " + std::to_string(step) + " cached partial redraw is exact");
    }
}

void budgets_are_enforced() {
    auto scene = empty_scene();
    for (int index = 0; index < 5; ++index) {
        scene.root.children.push_back(text_node("t" + std::to_string(index),
                                                "Label " + std::to_string(index), 10.0F,
                                                10.0F + 30.0F * static_cast<float>(index)));
    }
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.paragraphs = {.max_entries = 2, .max_bytes = 1U << 20U};
        surface->set_cache_budgets(budgets);
        render(*surface, scene);
        const auto paragraphs = surface->telemetry().paragraphs;
        expect(paragraphs.frame_misses == 5 && paragraphs.entries == 2 &&
                   paragraphs.evictions == 3,
               "the paragraph count budget keeps the two most recent", describe(paragraphs));
        expect_identical(reference(scene), capture(*surface),
                         "evicting paragraphs mid-frame draws the same pixels");
        render(*surface, scene);
        const auto again = surface->telemetry().paragraphs;
        expect(again.entries == 2 && again.frame_misses + again.frame_hits == 5,
               "a frame larger than the budget still keeps it", describe(again));
    }

    const std::size_t texture_bytes = 8U * 8U * 4U;
    auto textures = empty_scene();
    textures.root.children.push_back(textured_quad("a", checker_texture(10), 10.0F, 10.0F));
    textures.root.children.push_back(textured_quad("b", checker_texture(90), 80.0F, 10.0F));
    textures.root.children.push_back(textured_quad("c", checker_texture(170), 150.0F, 10.0F));
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.textures = {.max_entries = 64, .max_bytes = texture_bytes * 2U};
        surface->set_cache_budgets(budgets);
        render(*surface, textures);
        const auto telemetry = surface->telemetry();
        expect(telemetry.textures.entries == 2 && telemetry.textures.evictions == 1 &&
                   telemetry.textures.bytes == texture_bytes * 2U &&
                   telemetry.atlas_bytes == texture_bytes * 2U,
               "the texture byte budget evicts the least recently used texture",
               describe(telemetry.textures));
        expect_identical(reference(textures), capture(*surface),
                         "evicting textures draws the same pixels");
    }
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.textures = {.max_entries = 64, .max_bytes = texture_bytes - 1U};
        surface->set_cache_budgets(budgets);
        render(*surface, textures);
        const auto stats = surface->telemetry().textures;
        expect(stats.refused == 3 && stats.entries == 0 && stats.bytes == 0,
               "a texture larger than the byte budget is refused", describe(stats));
        expect_identical(reference(textures), capture(*surface),
                         "a refused texture is still drawn");
    }
}

// A trim on a settling partial frame keeps what any frame since the resize
// used, and a capture's internal redraw does not count toward settling.
void settle_counts_presented_frames() {
    auto texture = checker_texture(30);
    auto scene = empty_scene(kDamageModelSceneVersion);
    scene.root.children.push_back(text_node("title", "Settle", 10.0F, 10.0F));
    scene.root.children.push_back(gradient_node("ramp", {150.0F, 10.0F, 140.0F, 50.0F}));
    scene.root.children.push_back({.id = "mover", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {20.0F, 150.0F, 20.0F, 20.0F},
                                   .fill = Color::hex(0xF472B6)});
    auto surface = make_surface(true);
    std::string error;
    expect(surface->render_incremental(scene, &error), "settle scene renders", error);
    expect(surface->resize(kWidth, kHeight, 1.0F, &error), "settle resize", error);
    expect(surface->render_incremental(scene, &error), "the resize frame repaints", error);
    for (int step = 0; step < 2; ++step) {
        scene.root.children[2].bounds.x += 25.0F;
        expect(surface->render_incremental(scene, &error), "a partial settling frame", error);
    }
    auto t = surface->telemetry();
    expect(t.resize_settled && t.last_trim == CacheTrimReason::resize_settled && !t.full_repaint &&
               t.paragraphs.entries == 1 && t.gradients.entries == 1,
           "a partial settling frame keeps what the resize frame drew", to_json(t));

    auto bad = scene;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 120.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    expect(surface->resize(kWidth, kHeight, 1.0F, &error), "second resize", error);
    expect(surface->render_incremental(scene, &error), "the second resize frame", error);
    expect(!surface->render_incremental(bad, &error), "a one-stop gradient fails the frame");
    const auto before = surface->telemetry();
    (void)capture(*surface);
    const auto after = surface->telemetry();
    expect(after.paragraphs.frame_hits == before.paragraphs.frame_hits &&
               after.paragraphs.frame_misses == before.paragraphs.frame_misses &&
               after.gradients.frame_hits == before.gradients.frame_hits &&
               after.gradients.frame_misses == before.gradients.frame_misses &&
               after.layer_count == before.layer_count && after.frame == before.frame,
           "a capture's internal redraw leaves the frame telemetry alone", to_json(after));
    expect(surface->render_incremental(scene, &error), "the frame after the capture", error);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 1,
           "a capture's internal redraw does not advance the settle count", to_json(t));
}

// A frame that fails after uploading a texture never submits the upload, so
// the texture must not stay cached.
void failed_frame_drops_its_uploads() {
    auto texture = checker_texture(140);
    auto good = empty_scene();
    good.root.children.push_back(textured_quad("quad", texture, 60.0F, 60.0F));
    auto bad = good;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 60.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    auto surface = make_surface(true);
    std::string error;
    expect(!surface->render(bad, &error), "a frame with a one-stop gradient fails");
    auto t = surface->telemetry();
    expect(t.textures.entries == 0 && t.atlas_bytes == 0,
           "a failed frame keeps none of its uploads", describe(t.textures));
    render(*surface, good);
    t = surface->telemetry();
    expect(t.textures.frame_misses == 1 && t.textures.entries == 1,
           "the next frame uploads the texture again", describe(t.textures));
    expect_identical(reference(good), capture(*surface),
                     "the frame after a failed one draws the texture exactly");
}

void scripted_telemetry() {
    auto texture = checker_texture(60);
    const auto full = telemetry_scene(texture);
    const auto reduced = telemetry_scene(texture, false);
    const auto commands = make_paint_commands(full);
    const auto layers = static_cast<std::size_t>(std::count_if(
        commands.begin(), commands.end(),
        [](const auto& command) { return command.kind == PaintCommand::Kind::begin_layer; }));
    const std::size_t texture_bytes = 8U * 8U * 4U;

    auto surface = make_surface(true);
    render(*surface, full);
    auto t = surface->telemetry();
    expect(t.frame == 1 && t.full_repaint && t.full_repaint_reason == "full_render" &&
               t.damage_fraction == 1.0 &&
               t.damage_area_pixels == static_cast<std::int64_t>(kWidth) * kHeight,
           "frame 1 is a full repaint of the whole frame", to_json(t));
    expect(layers == 1 && t.layer_count == layers, "frame 1 opens the faded group's layer",
           to_json(t));
    expect(t.paragraphs.frame_misses == 3 && t.paragraphs.frame_hits == 0 &&
               t.paragraphs.entries == 3 && t.gradients.frame_misses == 1 &&
               t.gradients.entries == 1 && t.textures.frame_misses == 1 &&
               t.textures.entries == 1 && t.atlas_bytes == texture_bytes,
           "frame 1 misses every cache and fills it", to_json(t));
    expect(t.trims == 0 && t.last_trim == CacheTrimReason::none && !t.resize_settled,
           "frame 1 trims nothing", to_json(t));

    render(*surface, full);
    t = surface->telemetry();
    expect(t.frame == 2 && t.paragraphs.frame_hits == 3 && t.paragraphs.frame_misses == 0 &&
               t.gradients.frame_hits == 1 && t.textures.frame_hits == 1 &&
               hit_rate(t.paragraphs) == 0.5 && frame_hit_rate(t.paragraphs) == 1.0,
           "frame 2 is drawn from the caches", to_json(t));

    std::string error;
    expect(surface->finish(&error), "frame 2 finishes on the GPU", error);
    const auto stats = surface->frame_stats();
    t = surface->telemetry();
    expect(t.gpu_time_available == stats.gpu_time_available &&
               t.gpu_milliseconds == stats.gpu_milliseconds &&
               (!t.gpu_time_available || t.gpu_milliseconds > 0.0),
           "telemetry carries Graphite's GPU time", to_json(t));

    expect(surface->resize(kWidth + 10U, kHeight + 10U, 1.0F, &error), "resize", error);
    render(*surface, reduced);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 0, "the frame after a resize has not settled",
           to_json(t));
    render(*surface, reduced);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 0, "one stable frame has not settled", to_json(t));
    render(*surface, reduced);
    t = surface->telemetry();
    expect(t.resize_settled && t.trims == 1 && t.last_trim == CacheTrimReason::resize_settled,
           "the second stable frame settles the resize and trims", to_json(t));
    expect(t.paragraphs.entries == 2 && t.paragraphs.trimmed == 1 && t.gradients.entries == 0 &&
               t.gradients.trimmed == 1 && t.textures.entries == 1 &&
               t.atlas_bytes == texture_bytes,
           "the settle trim drops what no frame drew since the resize", to_json(t));
    render(*surface, full);
    t = surface->telemetry();
    expect(t.trims == 1 && t.paragraphs.frame_misses == 1 && t.gradients.frame_misses == 1,
           "a settled surface trims once per resize", to_json(t));

    auto budgets = surface->cache_budgets();
    budgets.paragraphs.max_entries = 1;
    surface->set_cache_budgets(budgets);
    t = surface->telemetry();
    expect(t.trims == 2 && t.last_trim == CacheTrimReason::budget_changed &&
               t.paragraphs.entries == 1,
           "lowering a budget trims to it", to_json(t));

    surface->simulate_device_loss();
    expect(!surface->render(full, &error), "rendering after a device loss fails");
    t = surface->telemetry();
    expect(t.trims == 3 && t.last_trim == CacheTrimReason::device_lost &&
               t.paragraphs.entries == 0 && t.gradients.entries == 0 &&
               t.textures.entries == 0 && t.atlas_bytes == 0 && t.textures.bytes == 0,
           "a device loss drops every cache", to_json(t));
    expect(!surface->render(full, &error) && surface->telemetry().trims == 3,
           "a lost device trims once");
}

}  // namespace

int main() {
    try {
        std::string error;
        if (!harness::prepare_device(&error)) {
            std::cerr << "FAIL Dawn bootstrap: " << error << '\n';
            return 1;
        }
        fixtures_with_and_without_caches();
        keys_see_every_input();
        partial_redraw_with_and_without_caches();
        budgets_are_enforced();
        scripted_telemetry();
        settle_counts_presented_frames();
        failed_frame_drops_its_uploads();
    } catch (const std::exception& exception) {
        std::cout << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "render cache gpu " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
