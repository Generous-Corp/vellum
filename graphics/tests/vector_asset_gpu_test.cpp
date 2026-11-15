#include "../src/skia_dawn_surface_test_access.hpp"
#include "../src/skia_dawn_surface_testing.hpp"
#include "canvas_shell_lowering.hpp"
#include "rendered_tree_materializer.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/vector_asset.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace hooks = vellum::graphics::testing;

int failures = 0;
// Set while printing canvas plans, whose output must be JSON lines only.
bool quiet = false;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    if (!condition) ++failures;
    if (quiet && condition) return;
    auto& out = quiet ? std::cerr : std::cout;
    out << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) out << ": " << detail;
    out << '\n';
}

std::string fixture_text(const std::string& name) {
    std::ifstream input(std::string(VELLUM_VECTOR_FIXTURE_DIR) + "/" + name + ".rendered.json",
                        std::ios::binary);
    std::ostringstream text;
    text << input.rdbuf();
    return text.str();
}

// A scene materialized from a rendered tree the authoring runtime produced
// from one of the SVG fixtures; see packages/vellum-ui/test/vector-assets.test.js.
std::optional<Scene> fixture(const std::string& name) {
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(fixture_text(name), rendered, &error)) {
        expect(false, "fixture " + name + " materializes", error);
        return std::nullopt;
    }
    std::string warnings;
    for (const auto& diagnostic : rendered.diagnostics) {
        warnings += diagnostic.code + " " + diagnostic.node_id + " " + diagnostic.message + "; ";
    }
    expect(rendered.diagnostics.empty(), "fixture " + name + " materializes without diagnostics",
           warnings);
    expect(rendered.scene.scene_version == kSceneVersion,
           "fixture " + name + " is a version " + std::to_string(kSceneVersion) + " scene");
    return rendered.scene;
}

std::unique_ptr<SkiaDawnSurface> make_surface(const Scene& scene, float scale,
                                              bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height),
         .scale = scale},
        &error);
    expect(surface != nullptr, "offscreen surface is created", error);
    if (surface && !layer_cache) {
        LayerCachePolicy policy;
        policy.enabled = false;
        surface->set_layer_cache_policy(policy);
    }
    return surface;
}

struct Pixels final {
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

Pixels capture(SkiaDawnSurface& surface) {
    Pixels pixels;
    std::string error;
    expect(surface.capture_rgba(pixels.rgba, pixels.width, pixels.height, &error),
           "the frame is captured", error);
    return pixels;
}

std::optional<Pixels> render_once(const Scene& scene, float scale = 1.0F) {
    auto surface = make_surface(scene, scale);
    if (!surface) return std::nullopt;
    std::string error;
    if (!surface->render(scene, &error)) {
        expect(false, "the scene renders", error);
        return std::nullopt;
    }
    return capture(*surface);
}

using Rgb = std::array<float, 3>;

Rgb hex(std::uint32_t value) {
    return {static_cast<float>((value >> 16U) & 0xFFU) / 255.0F,
            static_cast<float>((value >> 8U) & 0xFFU) / 255.0F,
            static_cast<float>(value & 0xFFU) / 255.0F};
}

Rgb mix(const Rgb& from, const Rgb& to, float t) {
    t = std::clamp(t, 0.0F, 1.0F);
    return {from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t,
            from[2] + (to[2] - from[2]) * t};
}

struct Expected final {
    std::uint32_t x = 0;
    std::uint32_t y = 0;
    Rgb rgb{};
    std::string where;
    float tolerance = 2.0F;
};

void expect_pixels(const std::string& name, const Pixels& pixels,
                   const std::vector<Expected>& expected) {
    for (const auto& pixel : expected) {
        const auto offset = (static_cast<std::size_t>(pixel.y) * pixels.width + pixel.x) * 4U;
        float worst = 0.0F;
        std::ostringstream detail;
        detail << "at (" << pixel.x << ", " << pixel.y << ") rgb=";
        for (std::size_t channel = 0; channel < 3U; ++channel) {
            const float actual = pixels.rgba[offset + channel];
            worst = std::max(worst, std::abs(actual - pixel.rgb[channel] * 255.0F));
            detail << (channel ? "," : "") << actual;
        }
        detail << " expected=" << pixel.rgb[0] * 255.0F << "," << pixel.rgb[1] * 255.0F << ","
               << pixel.rgb[2] * 255.0F;
        expect(worst <= pixel.tolerance, "pixel oracle " + name + ": " + pixel.where, detail.str());
    }
}

// The ramp position of a pixel centre along [start, end).
float along(std::uint32_t pixel, float start, float length) {
    return (static_cast<float>(pixel) + 0.5F - start) / length;
}

// The position t of point p on a two-circle radial gradient from the focal
// circle (f, radius 0) to the circle (c, r): the largest t with
// |p - (f + t (c - f))| = t r.
float two_circle_t(float px, float py, float fx, float fy, float cx, float cy, float r) {
    const float dx = px - fx;
    const float dy = py - fy;
    const float ex = cx - fx;
    const float ey = cy - fy;
    const float a = ex * ex + ey * ey - r * r;
    const float b = -2.0F * (dx * ex + dy * ey);
    const float c = dx * dx + dy * dy;
    const float root = std::sqrt(std::max(0.0F, b * b - 4.0F * a * c));
    const float t1 = (-b + root) / (2.0F * a);
    const float t2 = (-b - root) / (2.0F * a);
    return std::max(t1, t2);
}

constexpr float kGradientTolerance = 6.0F;
// The materializer's default scene background, which the fixtures leave
// showing.
constexpr std::uint32_t kBackground = 0xF8FAFC;

std::vector<Expected> gradient_oracle(std::uint32_t swatch) {
    std::vector<Expected> expected{
        {5, 5, hex(kBackground), "the background"},
        {160, 70, hex(swatch), "the token-bound swatch"},
    };
    for (const std::uint32_t x : {36U, 100U, 164U}) {
        expected.push_back({x, 25, mix(hex(0xFF0000), hex(0x0000FF), along(x, 20.0F, 160.0F)),
                            "the horizontal ramp at x=" + std::to_string(x), kGradientTolerance});
    }
    for (const std::uint32_t y : {55U, 80U, 105U}) {
        const float fraction = along(y, 50.0F, 60.0F);
        expected.push_back({40, y, mix(hex(0x00FF00), hex(0x0000FF), (fraction - 0.25F) / 0.5F),
                            "the vertical ramp at y=" + std::to_string(y), kGradientTolerance});
    }
    for (const std::uint32_t x : {110U, 125U, 132U}) {
        const float distance = std::hypot(static_cast<float>(x) + 0.5F - 110.0F, 0.5F);
        expected.push_back({x, 80, mix(hex(0xFFFFFF), hex(0x000000), distance / 30.0F),
                            "the radial glow at x=" + std::to_string(x), kGradientTolerance});
    }
    return expected;
}

void pixel_oracles() {
    if (const auto scene = fixture("gradients")) {
        if (const auto pixels = render_once(*scene)) {
            expect_pixels("gradients subtree", *pixels, gradient_oracle(0xE11D48));
        }
    }
    if (const auto scene = fixture("gradients-retheme")) {
        if (const auto pixels = render_once(*scene)) {
            expect_pixels("gradients subtree after a retheme", *pixels,
                          {{160, 70, hex(0x22C55E), "the retinted swatch"}});
        }
    }
    if (const auto scene = fixture("gradients-compiled")) {
        expect(scene->root.children.size() == 1U &&
                   scene->root.children.front().kind == SceneNode::Kind::vector,
               "the compiled gradients fixture is one vector node");
        if (const auto pixels = render_once(*scene)) {
            expect_pixels("gradients compiled", *pixels, gradient_oracle(0xE11D48));
        }
    }
    if (const auto scene = fixture("clip-mask")) {
        if (const auto pixels = render_once(*scene)) {
            expect_pixels("clip and mask", *pixels, {
                {50, 60, hex(0x00A000), "inside the circular clip"},
                {15, 25, hex(kBackground), "inside the rectangle, outside its circular clip"},
                {120, 60, hex(0x0050FF), "inside the first bar of the two-shape clip"},
                {160, 60, hex(0x0050FF), "inside the second bar of the two-shape clip"},
                {140, 60, hex(kBackground), "between the bars of the two-shape clip"},
                {105, 60, hex(kBackground), "beside the two-shape clip"},
                {80, 110, hex(0xFF0000), "where the luminance mask is white"},
                {120, 110, hex(kBackground), "where the luminance mask is black"},
            });
        }
    }
    if (const auto scene = fixture("use")) {
        if (const auto pixels = render_once(*scene)) {
            const Rgb ground = hex(kBackground);
            expect_pixels("use instances", *pixels, {
                {5, 5, ground, "the ground"},
                {15, 15, hex(0x2040FF), "the first instance's tile"},
                {25, 25, hex(0xFFD000), "the first instance's inset"},
                {155, 15, mix(hex(0x2040FF), ground, 0.5F), "the half-opaque instance's tile"},
                {165, 25, mix(hex(0xFFD000), ground, 0.5F), "the half-opaque instance's inset"},
                {65, 75, hex(0x2040FF), "the first nested instance"},
                {105, 75, hex(0x2040FF), "the second nested instance"},
                {95, 75, ground, "between the nested instances"},
            });
        }
    }
    if (const auto scene = fixture("compiled-only")) {
        if (const auto pixels = render_once(*scene)) {
            std::vector<Expected> expected{{5, 5, hex(kBackground), "the background"}};
            for (const std::uint32_t x : {10U, 100U, 190U}) {
                expected.push_back({x, 25, mix(hex(0xFF0000), hex(0x0000FF), along(x, 40.0F, 120.0F)),
                                    "the user-space ramp at x=" + std::to_string(x),
                                    kGradientTolerance});
            }
            for (const std::uint32_t y : {55U, 80U, 105U}) {
                expected.push_back({40, y, mix(hex(0x00FF00), hex(0xFF00FF), along(y, 50.0F, 60.0F)),
                                    "the turned ramp at y=" + std::to_string(y),
                                    kGradientTolerance});
            }
            for (const auto& [x, y] : {std::pair{105U, 80U}, std::pair{178U, 80U},
                                       std::pair{130U, 60U}}) {
                const float u = (static_cast<float>(x) + 0.5F - 80.0F) / 100.0F;
                const float v = (static_cast<float>(y) + 0.5F - 50.0F) / 60.0F;
                const float t = two_circle_t(u, v, 0.25F, 0.5F, 0.5F, 0.5F, 0.5F);
                expected.push_back({x, y, mix(hex(0xFFFF00), hex(0x000080), t),
                                    "the focal radial at (" + std::to_string(x) + ", " +
                                        std::to_string(y) + ")",
                                    kGradientTolerance});
            }
            expect_pixels("compiled-only features", *pixels, expected);
        }
    }
    if (const auto scene = fixture("over-budget")) {
        if (const auto pixels = render_once(*scene)) {
            expect_pixels("over-budget compiled grid", *pixels, {
                {2, 2, hex(kBackground), "the background"},
                {8, 7, hex(0xC81E1E), "cell (0, 0)"},
                {15, 7, hex(0x1E64C8), "cell (0, 1)"},
                {11, 7, hex(kBackground), "the gap after cell (0, 0)"},
                {183, 102, hex(0xC81E1E), "cell (19, 25)"},
                {176, 102, hex(0x1E64C8), "cell (19, 24)"},
            });
        }
    }
}

// A blend mode on a subtree path and on a compiled vector node, whose radial
// and linear gradients multiply over a grey backdrop.
void blended_vectors() {
    const Rgb grey = hex(0x808080);
    const auto times = [&](const Rgb& colour) {
        return Rgb{colour[0] * grey[0], colour[1] * grey[1], colour[2] * grey[2]};
    };
    for (const char* name : {"gradients", "gradients-compiled"}) {
        auto scene = fixture(name);
        if (!scene) return;
        SceneNode backdrop{.id = "backdrop", .kind = SceneNode::Kind::rectangle,
                           .bounds = {0.0F, 0.0F, scene->width, scene->height},
                           .fill = Color::hex(0x808080)};
        scene->root.children.insert(scene->root.children.begin(), std::move(backdrop));
        SceneNode& blended = scene->root.children[1];
        if (std::string(name) == "gradients") {
            SceneNode* swatch = nullptr;
            for (auto& child : blended.children) {
                if (child.id == "assets/svg:swatch") swatch = &child;
            }
            expect(swatch != nullptr, "the subtree swatch is found");
            if (swatch == nullptr) return;
            swatch->blend_mode = BlendMode::multiply;
            for (auto& child : blended.children) {
                if (child.id == "assets/svg:dot") child.blend_mode = BlendMode::multiply;
            }
        } else {
            expect(blended.kind == SceneNode::Kind::vector, "the compiled fixture is a vector node");
            blended.blend_mode = BlendMode::multiply;
        }
        const auto pixels = render_once(*scene);
        if (!pixels) return;
        const float distance = std::hypot(125.5F - 110.0F, 0.5F);
        expect_pixels(std::string("multiplied ") + name, *pixels, {
            {5, 5, grey, "the backdrop"},
            {160, 70, times(hex(0xE11D48)), "the swatch multiplied over grey", 3.0F},
            {125, 80, times(mix(hex(0xFFFFFF), hex(0x000000), distance / 30.0F)),
             "the radial glow multiplied over grey", kGradientTolerance},
        });
    }
}

// The subtree and the compiled resource of one asset draw the same pixels.
void forms_agree() {
    const auto subtree = fixture("gradients");
    const auto compiled = fixture("gradients-compiled");
    if (!subtree || !compiled) return;
    for (const float scale : {1.0F, 2.0F}) {
        const auto a = render_once(*subtree, scale);
        const auto b = render_once(*compiled, scale);
        if (!a || !b) return;
        int worst = 0;
        std::size_t over = 0;
        for (std::size_t index = 0; index < a->rgba.size(); ++index) {
            const int difference = std::abs(int(a->rgba[index]) - int(b->rgba[index]));
            worst = std::max(worst, difference);
            if (difference > 3) ++over;
        }
        expect(a->rgba.size() == b->rgba.size() && over == 0,
               "the subtree and compiled forms of one asset agree within 3 levels at " +
                   std::to_string(static_cast<int>(scale)) + "x",
               "worst " + std::to_string(worst) + ", " + std::to_string(over) + " channels over");
    }
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

bool same_frame(SkiaDawnSurface& a, SkiaDawnSurface& b, std::string& message) {
    const auto left = capture(a);
    const auto right = capture(b);
    if (left.rgba.size() != right.rgba.size()) {
        message = "sizes differ";
        return false;
    }
    for (std::size_t index = 0; index < left.rgba.size(); ++index) {
        if (left.rgba[index] != right.rgba[index]) {
            const auto pixel = index / 4U;
            message = "first difference at (" + std::to_string(pixel % left.width) + ", " +
                      std::to_string(pixel / left.width) + ")";
            return false;
        }
    }
    return true;
}

struct SweepCount final {
    int differing = 0;
    int partial = 0;
    int edges = 0;
    std::string first;
};

// Cuts the scene with a damage rectangle whose left edge, then top edge,
// steps across the frame one device pixel at a time, comparing each partial
// frame with a full render.
SweepCount sweep_damage_edges(const Scene& scene, float scale, bool layer_cache) {
    SweepCount count;
    auto full = make_surface(scene, scale);
    auto partial = make_surface(scene, scale, layer_cache);
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
            if (!same_frame(*full, *partial, message)) {
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

void damage_sweeps() {
    for (const char* name : {"gradients", "gradients-compiled", "clip-mask", "use",
                             "compiled-only", "over-budget"}) {
        const auto scene = fixture(name);
        if (!scene) continue;
        for (const float scale : {1.0F, 2.0F}) {
            for (const bool cache : {false, true}) {
                const auto count = sweep_damage_edges(*scene, scale, cache);
                expect(count.differing == 0 && count.partial > 0,
                       std::string("a damage edge swept across the ") + name +
                           " asset takes true partial redraw and stays byte-identical at " +
                           std::to_string(static_cast<int>(scale)) + "x" +
                           (cache ? " with the layer cache" : ""),
                       sweep_detail(count));
            }
        }
    }
    struct Control final {
        const char* name;
        hooks::PartialRedrawRules off;
    };
    for (const auto& control : {
             Control{"gradients-compiled",
                     {.enclose_interpolated_fills = false, .enclose_vector_assets = false}},
             Control{"compiled-only",
                     {.enclose_interpolated_fills = false, .enclose_vector_assets = false}},
             Control{"clip-mask",
                     {.enclose_paths = false, .enclose_clips = false,
                      .enclose_group_layers = false}},
         }) {
        const auto scene = fixture(control.name);
        if (!scene) continue;
        RulesOverride off(control.off);
        const auto cut = sweep_damage_edges(*scene, 2.0F, false);
        expect(cut.partial > 0 && cut.differing > 0,
               std::string("negative control: without its enclosure rules a swept edge cuts the ") +
                   control.name + " asset visibly",
               sweep_detail(cut));
    }
}

SceneNode* find_mutable(SceneNode& node, const std::string& id) {
    if (node.id == id) return &node;
    for (auto& child : node.children) {
        if (auto* found = find_mutable(child, id); found != nullptr) return found;
    }
    return nullptr;
}

std::string describe(const FrameStats& stats) {
    return "full=" + std::to_string(stats.full_repaint) + " reason=" +
           std::string(to_string(stats.full_repaint_reason)) + " damage_px=" +
           std::to_string(stats.damage_area_pixels) + "/" + std::to_string(stats.frame_area_pixels);
}

// A token retheme changes one node of the imported subtree, and the frame
// redraws only that node's box.
void per_node_damage() {
    const auto before = fixture("gradients");
    const auto after = fixture("gradients-retheme");
    if (!before || !after) return;
    for (const float scale : {1.0F, 2.0F}) {
        auto full = make_surface(*before, scale);
        auto partial = make_surface(*before, scale);
        if (!full || !partial) return;
        std::string error;
        expect(full->render(*before, &error) && partial->render_incremental(*before, &error),
               "the first frame renders", error);
        expect(full->render(*after, &error) && partial->render_incremental(*after, &error),
               "the rethemed frame renders", error);
        const auto stats = partial->frame_stats();
        const auto swatch = static_cast<std::int64_t>((40.0F * scale + 8.0F) * (40.0F * scale + 8.0F));
        expect(!stats.full_repaint && stats.damage_area_pixels > 0 &&
                   stats.damage_area_pixels <= swatch,
               "a retheme damages only the token-bound node at " +
                   std::to_string(static_cast<int>(scale)) + "x",
               describe(stats));
        std::string message;
        expect(same_frame(*full, *partial, message),
               "the rethemed partial frame matches a full render", message);
    }

    auto compiled = fixture("compiled-only");
    auto base = fixture("use");
    if (!compiled || !base) return;
    SceneNode mover = compiled->root.children.front();
    mover.id = "mover";
    mover.bounds = {20.0F, 30.0F, 50.0F, 30.0F};
    base->root.children.push_back(mover);
    auto full = make_surface(*base, 2.0F);
    auto partial = make_surface(*base, 2.0F, true);
    if (!full || !partial) return;
    std::string error;
    expect(full->render(*base, &error) && partial->render_incremental(*base, &error),
           "a scene with a compiled node renders", error);
    for (int step = 0; step < 4; ++step) {
        find_mutable(base->root, "mover")->bounds.x += 13.25F;
        expect(full->render(*base, &error) && partial->render_incremental(*base, &error),
               "a moved compiled node renders", error);
        const auto stats = partial->frame_stats();
        std::string message;
        expect(!stats.full_repaint && stats.damage_area_pixels < stats.frame_area_pixels / 2 &&
                   same_frame(*full, *partial, message),
               "moving a compiled node redraws part of the frame, byte-identical, step " +
                   std::to_string(step),
               describe(stats) + " " + message);
    }
    auto older = make_surface(*base, 2.0F);
    if (!older) return;
    SkiaDawnSurfaceTestAccess::set_damage_model_version(*older, 8U);
    expect(older->render_incremental(*base, &error), "a vector scene renders past an older model",
           error);
    find_mutable(base->root, "mover")->bounds.x -= 20.0F;
    expect(older->render_incremental(*base, &error), "it renders a second frame", error);
    expect(older->frame_stats().full_repaint &&
               older->frame_stats().full_repaint_reason == FullRepaintReason::damage_model_version,
           "negative control: a version 9 scene past a version 8 damage model repaints fully",
           describe(older->frame_stats()));
}

void version_refusal() {
    auto scene = fixture("compiled-only");
    if (!scene) return;
    scene->scene_version = 8U;
    auto surface = make_surface(*scene, 1.0F);
    if (!surface) return;
    std::string error;
    expect(!surface->render(*scene, &error) &&
               error.find("compiled vector asset") != std::string::npos,
           "the GPU surface refuses a version 8 scene with a compiled vector asset", error);
    auto legacy = fixture("clip-mask");
    if (!legacy) return;
    legacy->scene_version = 8U;
    expect(surface->render(*legacy, &error),
           "a version 8 scene of paths, clips and masks still renders", error);
}

// Prints the canvas shell's plan for each compiled fixture, one JSON object
// per line, for the browser oracle in web/tests/run_vector_canvas_oracle.py.
int print_canvas_plans() {
    quiet = true;
    for (const char* name : {"gradients-compiled", "compiled-only", "over-budget"}) {
        const auto scene = fixture(name);
        if (!scene) return 1;
        const auto lowering = vellum::web::lower_for_canvas_shell(*scene);
        if (!lowering.accepted) return 1;
        const auto opacities = effective_opacities(lowering.commands);
        std::string plans;
        for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
            const auto& command = lowering.commands[index];
            if (command.kind != PaintCommand::Kind::vector) continue;
            if (!plans.empty()) plans += ',';
            plans += vellum::web::lower_vector_for_canvas(command, opacities[index]).json;
        }
        const auto& background = scene->background;
        std::cout << "{\"name\":\"" << name << "\",\"background\":[" << background.red << ','
                  << background.green << ',' << background.blue << ',' << background.alpha
                  << "],\"width\":" << scene->width
                  << ",\"height\":" << scene->height << ",\"plans\":[" << plans << "]}\n";
    }
    return failures == 0 ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--canvas-plans") == 0) return print_canvas_plans();
    std::string error;
    if (!vellum::graphics::capture_harness::prepare_device(&error)) {
        std::cerr << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    pixel_oracles();
    forms_agree();
    blended_vectors();
    per_node_damage();
    version_refusal();
    damage_sweeps();
    std::cout << (failures == 0 ? "vector asset GPU tests passed\n" : "vector asset GPU tests failed\n");
    return failures == 0 ? 0 : 1;
}
