#include "../src/damage_testing.hpp"
#include "../src/skia_dawn_surface_test_access.hpp"
#include "../src/skia_dawn_surface_testing.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cstdlib>
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
namespace hooks = vellum::graphics::testing;

int failures = 0;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kWidth = 240.0F;
constexpr float kHeight = 180.0F;

std::string describe(const FrameStats& stats) {
    std::ostringstream out;
    out << "full=" << stats.full_repaint << " reason=" << to_string(stats.full_repaint_reason)
        << " damage_px=" << stats.damage_area_pixels << '/' << stats.frame_area_pixels;
    return out.str();
}

std::unique_ptr<SkiaDawnSurface> make_surface(float scale, bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(kWidth),
         .height = static_cast<std::uint32_t>(kHeight),
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

bool identical(SkiaDawnSurface& a, SkiaDawnSurface& b, std::string& message) {
    const auto left = capture(a);
    const auto right = capture(b);
    if (!left.ok || !right.ok) {
        message = left.error + right.error;
        return false;
    }
    const auto verdict = harness::assert_identical(left.result, right.result);
    message = verdict.message;
    return static_cast<bool>(verdict);
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

Affine2D rotation_degrees(float degrees) {
    const float radians = degrees * 3.14159265358979323846F / 180.0F;
    return {.a = std::cos(radians), .b = std::sin(radians), .c = -std::sin(radians),
            .d = std::cos(radians)};
}

SceneNode box(std::string id, Rect bounds, Color fill, float radius = 0.0F) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = fill, .corner_radius = radius};
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

// Stripes behind everything, so a blur or backdrop has detail to spread.
Scene striped_scene() {
    Scene scene;
    scene.scene_version = kDamageModelSceneVersion;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = Color::hex(0x0B1020);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    for (int stripe = 0; stripe < 12; ++stripe) {
        const float x = 6.0F + static_cast<float>(stripe) * 19.0F;
        scene.root.children.push_back(box("stripe" + std::to_string(stripe),
                                          {x, 10.0F, 8.5F, 160.0F},
                                          stripe % 2 == 0 ? Color::hex(0xFDE68A)
                                                          : Color::hex(0x34D399)));
    }
    return scene;
}

SceneNode effect_group(std::vector<NodeEffect> effects) {
    SceneNode group{.id = "fx", .kind = SceneNode::Kind::group,
                    .bounds = {60.5F, 45.25F, 120.0F, 90.0F}};
    group.children.push_back(box("fx/a", {10.0F, 8.0F, 52.0F, 40.0F}, Color::hex(0xEF4444), 7.0F));
    group.children.push_back(box("fx/b", {44.0F, 30.0F, 58.0F, 46.0F},
                                 Color::rgba(0.23F, 0.51F, 0.96F, 0.85F), 11.0F));
    group.effects = std::move(effects);
    return group;
}

NodeEffect blur(float sigma) { return {.kind = EffectKind::blur, .sigma = sigma}; }

NodeEffect channel_swap() {
    NodeEffect effect{.kind = EffectKind::color_matrix};
    effect.matrix = {0.0F, 0.0F, 1.0F, 0.0F, 0.0F,
                     0.0F, 1.0F, 0.0F, 0.0F, 0.0F,
                     1.0F, 0.0F, 0.0F, 0.0F, 0.0F,
                     0.0F, 0.0F, 0.0F, 0.9F, 0.08F};
    return effect;
}

NodeEffect drop_shadow() {
    return {.kind = EffectKind::drop_shadow,
            .sigma = 3.0F,
            .offset_x = 7.0F,
            .offset_y = 5.0F,
            .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.75F)};
}

NodeEffect backdrop(float sigma) { return {.kind = EffectKind::backdrop_blur, .sigma = sigma}; }

NodeEffect glass() {
    return {.kind = EffectKind::material,
            .material = std::string(kFrostedGlassMaterial),
            .values = {{.name = "blur", .value = {5.0F}},
                       {.name = "tint", .type = MaterialValueType::color,
                        .value = {0.9F, 0.95F, 1.0F, 0.22F}}}};
}

// The stripes, the effect group, and a node drawn after it.
Scene filter_scene(std::vector<NodeEffect> effects) {
    Scene scene = striped_scene();
    scene.root.children.push_back(effect_group(std::move(effects)));
    scene.root.children.push_back(box("front", {24.0F, 120.0F, 30.0F, 24.0F}, Color::hex(0xA855F7)));
    return scene;
}

// The stripes, a rounded, turned pane with a backdrop effect and one child,
// and a node drawn after it that overlaps the area the backdrop samples but
// not the pane itself.
Scene backdrop_scene(NodeEffect effect) {
    Scene scene = striped_scene();
    SceneNode pane{.id = "pane", .kind = SceneNode::Kind::group,
                   .bounds = {70.25F, 50.5F, 100.0F, 70.0F},
                   .corner_radii = CornerRadii{14.0F, 4.0F, 18.0F, 9.0F}};
    pane.transform = rotation_degrees(8.0F);
    pane.children.push_back(box("pane/label", {16.0F, 22.0F, 40.0F, 18.0F},
                                Color::rgba(1.0F, 1.0F, 1.0F, 0.7F), 5.0F));
    pane.children.push_back(box("pane/dot", {70.0F, 40.0F, 12.0F, 12.0F},
                                Color::rgba(0.1F, 0.1F, 0.1F, 0.8F), 6.0F));
    pane.effects = {std::move(effect)};
    scene.root.children.push_back(std::move(pane));
    scene.root.children.push_back(box("front", {52.0F, 126.0F, 14.0F, 14.0F}, Color::hex(0xF43F5E)));
    return scene;
}

Scene chain_scene() {
    Scene scene = filter_scene({blur(2.5F), channel_swap(), drop_shadow()});
    auto& group = node(scene, "fx");
    group.opacity = 0.8F;
    group.transform = rotation_degrees(-6.0F);
    return scene;
}

struct NamedScene final {
    std::string name;
    Scene scene;
    hooks::PartialRedrawRules off;
    /// Whether the scene needs the enclosure rules to stay exact; a colour
    /// matrix alone works per pixel and reproduces through any clip.
    bool needs_enclosure = true;
};

std::vector<NamedScene> effect_scenes() {
    const hooks::PartialRedrawRules filters_off{
        .enclose_group_layers = false, .enclose_effects = false, .enclose_backdrops = false};
    return {
        {"a blur", filter_scene({blur(4.0F)}), filters_off},
        {"a colour matrix", filter_scene({channel_swap()}), filters_off, false},
        {"a drop shadow", filter_scene({drop_shadow()}), filters_off},
        {"a filter chain", chain_scene(), filters_off},
        {"a backdrop blur", backdrop_scene(backdrop(4.0F)), {.enclose_backdrops = false}},
        {"frosted glass", backdrop_scene(glass()), {.enclose_backdrops = false}},
    };
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
SweepCount sweep_damage_edges(const Scene& scene, float scale, bool layer_cache) {
    SweepCount count;
    auto full = make_surface(scale);
    auto partial = make_surface(scale, layer_cache);
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

Scene without_effects(Scene scene);

void sweeps() {
    Scene control = filter_scene({});
    std::erase_if(control.root.children, [](const SceneNode& child) { return child.id == "fx"; });
    const auto bare = sweep_damage_edges(control, 2.0F, false);
    expect(bare.differing == 0 && bare.partial > 0,
           "control: the stripes and the front node sweep byte-identical", sweep_detail(bare));
    for (const auto& sweep : effect_scenes()) {
        for (const float scale : {1.0F, 2.0F}) {
            for (const bool cached : {false, true}) {
                const std::string label = std::string(cached ? "cache on @" : "cache off @") +
                                          std::to_string(static_cast<int>(scale)) + " ";
                const auto enclosed = sweep_damage_edges(sweep.scene, scale, cached);
                expect(enclosed.differing == 0 && enclosed.partial > 0,
                       label + "a damage edge swept across " + sweep.name +
                           " leaves every frame byte-identical",
                       sweep_detail(enclosed));
            }
        }
        RulesOverride off(sweep.off);
        const auto cut = sweep_damage_edges(sweep.scene, 2.0F, false);
        if (sweep.needs_enclosure) {
            expect(cut.differing > 0 && cut.partial > 0,
                   "negative control: without its enclosure rule a swept edge cuts " +
                       sweep.name + " visibly",
                   sweep_detail(cut));
        } else {
            expect(cut.differing == 0 && cut.partial > 0,
                   sweep.name + " reproduces through a partial clip even without enclosure",
                   sweep_detail(cut));
        }
    }
}

bool render_both(SkiaDawnSurface& full, SkiaDawnSurface& partial, const Scene& scene) {
    std::string error;
    const bool full_ok = full.render(scene, &error);
    const bool partial_ok = full_ok && partial.render_incremental(scene, &error);
    expect(full_ok && partial_ok, "both paths render", error);
    return full_ok && partial_ok;
}

struct Edit final {
    std::string name;
    std::function<void(Scene&)> apply;
};

void edit_sequence(const std::string& name, Scene scene, const std::vector<Edit>& edits,
                   float scale, bool cached) {
    const std::string label = std::string(cached ? "cache on @" : "cache off @") +
                              std::to_string(static_cast<int>(scale)) + " " + name + ": ";
    auto full = make_surface(scale);
    auto partial = make_surface(scale, cached);
    if (!full || !partial || !render_both(*full, *partial, scene)) return;
    int partial_frames = 0;
    for (const auto& edit : edits) {
        edit.apply(scene);
        if (!render_both(*full, *partial, scene)) return;
        const auto stats = partial->frame_stats();
        if (!stats.full_repaint) ++partial_frames;
        std::string message;
        expect(identical(*full, *partial, message),
               label + edit.name + " is byte-identical to a full redraw",
               describe(stats) + " " + message);
    }
    expect(partial_frames > 0, label + "the edits take the partial path",
           std::to_string(partial_frames) + " of " + std::to_string(edits.size()));
}

void edit_sequences() {
    const std::vector<Edit> filter_edits = {
        {"move a member", [](Scene& s) { node(s, "fx/a").bounds.x += 3.5F; }},
        {"recolour a member", [](Scene& s) { node(s, "fx/b").fill = Color::hex(0x10B981); }},
        {"move a node behind", [](Scene& s) { node(s, "stripe5").bounds.y += 4.0F; }},
        {"move the node in front", [](Scene& s) { node(s, "front").bounds.x += 9.0F; }},
        {"move the group", [](Scene& s) { node(s, "fx").bounds.y += 2.25F; }},
        {"fade the group", [](Scene& s) { node(s, "fx").opacity = 0.6F; }},
        {"change nothing", [](Scene&) {}},
    };
    auto blur_edits = filter_edits;
    blur_edits.push_back({"change the sigma", [](Scene& s) { node(s, "fx").effects[0].sigma = 6.0F; }});
    auto matrix_edits = filter_edits;
    matrix_edits.push_back({"change a matrix entry", [](Scene& s) {
        node(s, "fx").effects[0].matrix[19] = 0.0F;
    }});
    auto shadow_edits = filter_edits;
    shadow_edits.push_back({"move the shadow", [](Scene& s) {
        node(s, "fx").effects[0].offset_x = -4.0F;
    }});
    const std::vector<Edit> backdrop_edits = {
        {"move a stripe inside the sample", [](Scene& s) { node(s, "stripe3").bounds.x += 2.0F; }},
        {"recolour a stripe under the pane",
         [](Scene& s) { node(s, "stripe6").fill = Color::hex(0x60A5FA); }},
        {"move the node in front", [](Scene& s) { node(s, "front").bounds.y -= 3.0F; }},
        {"move a child", [](Scene& s) { node(s, "pane/dot").bounds.x -= 6.0F; }},
        {"move the pane", [](Scene& s) { node(s, "pane").bounds.x += 4.5F; }},
        {"turn the pane", [](Scene& s) { node(s, "pane").transform = rotation_degrees(-5.0F); }},
        {"change nothing", [](Scene&) {}},
    };
    auto blur_backdrop_edits = backdrop_edits;
    blur_backdrop_edits.push_back({"change the sigma", [](Scene& s) {
        node(s, "pane").effects[0].sigma = 2.0F;
    }});
    auto glass_edits = backdrop_edits;
    glass_edits.push_back({"change the tint", [](Scene& s) {
        node(s, "pane").effects[0].values[1].value = {0.2F, 0.1F, 0.0F, 0.4F};
    }});
    glass_edits.push_back({"remove the material", [](Scene& s) {
        node(s, "pane").effects.clear();
    }});
    for (const bool cached : {false, true}) {
        for (const float scale : {1.0F, 2.0F}) {
            edit_sequence("blur", filter_scene({blur(4.0F)}), blur_edits, scale, cached);
            edit_sequence("colour matrix", filter_scene({channel_swap()}), matrix_edits, scale,
                          cached);
            edit_sequence("drop shadow", filter_scene({drop_shadow()}), shadow_edits, scale,
                          cached);
            edit_sequence("chain", chain_scene(), filter_edits, scale, cached);
            edit_sequence("backdrop blur", backdrop_scene(backdrop(4.0F)), blur_backdrop_edits,
                          scale, cached);
            edit_sequence("frosted glass", backdrop_scene(glass()), glass_edits, scale, cached);
        }
    }
}

Scene without_effects(Scene scene) {
    const std::function<void(SceneNode&)> strip = [&](SceneNode& node) {
        node.effects.clear();
        for (auto& child : node.children) strip(child);
    };
    strip(scene.root);
    return scene;
}

struct DamageRulesOverride final {
    explicit DamageRulesOverride(const hooks::EffectDamageRules& rules)
        : saved(hooks::effect_damage_rules()) {
        hooks::set_effect_damage_rules(rules);
    }
    ~DamageRulesOverride() { hooks::set_effect_damage_rules(saved); }
    DamageRulesOverride(const DamageRulesOverride&) = delete;
    DamageRulesOverride& operator=(const DamageRulesOverride&) = delete;
    hooks::EffectDamageRules saved;
};

struct Coverage final {
    std::int64_t changed = 0;
    std::int64_t missed = 0;
};

// How many pixels differ between full renders of `before` and `after`, and
// how many of those the modelled damage misses.
Coverage damage_coverage(const Scene& before, const Scene& after, float scale) {
    const auto first = [&] {
        auto surface = make_surface(scale);
        std::string error;
        if (!surface || !surface->render(before, &error)) return Capture{};
        return capture(*surface);
    }();
    const auto second = [&] {
        auto surface = make_surface(scale);
        std::string error;
        if (!surface || !surface->render(after, &error)) return Capture{};
        return capture(*surface);
    }();
    Coverage coverage;
    if (!first.ok || !second.ok) {
        coverage.missed = -1;
        return coverage;
    }
    const auto damage = compute_scene_damage(before, after, scale, {.full_repaint_fraction = 2.0});
    const auto width = first.result.pixel_width;
    for (std::uint32_t y = 0; y < first.result.pixel_height; ++y) {
        for (std::uint32_t x = 0; x < width; ++x) {
            const auto offset = (static_cast<std::size_t>(y) * width + x) * 4U;
            if (std::equal(first.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset),
                           first.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset + 4U),
                           second.result.rgba.begin() + static_cast<std::ptrdiff_t>(offset))) {
                continue;
            }
            ++coverage.changed;
            const PixelRect pixel{static_cast<std::int32_t>(x), static_cast<std::int32_t>(y),
                                  static_cast<std::int32_t>(x) + 1,
                                  static_cast<std::int32_t>(y) + 1};
            if (!damage.intersects(pixel)) ++coverage.missed;
        }
    }
    return coverage;
}

std::string coverage_detail(const Coverage& coverage) {
    return std::to_string(coverage.missed) + " of " + std::to_string(coverage.changed) +
           " changed pixels outside the damage";
}

// Each effect rule of the damage model, proved on the GPU: the modelled
// damage covers every pixel an edit changes, and with the rule off it misses
// some.
void damage_rule_controls() {
    const float scale = 2.0F;
    struct Case final {
        std::string what;
        Scene before;
        Scene after;
        hooks::EffectDamageRules off;
    };
    std::vector<Case> cases;
    {
        Scene before = filter_scene({blur(4.0F)});
        Scene after = before;
        node(after, "fx/a").fill = Color::hex(0xFACC15);
        cases.push_back({"a recoloured member inside a blur", before, after,
                         {.grow_by_radius = false}});
    }
    {
        Scene before = filter_scene({drop_shadow()});
        Scene after = before;
        node(after, "fx/b").bounds.y += 6.0F;
        cases.push_back({"a moved member inside a drop shadow", before, after,
                         {.grow_by_radius = false}});
    }
    {
        Scene before = filter_scene({channel_swap()});
        Scene after = before;
        node(after, "fx/a").bounds.x += 3.5F;
        cases.push_back({"a member widening a colour matrix that lifts alpha", after, before,
                         {.fill_area = false}});
    }
    {
        Scene before = backdrop_scene(backdrop(4.0F));
        Scene after = before;
        node(after, "stripe3").fill = Color::hex(0xF472B6);
        cases.push_back({"a recoloured stripe inside a backdrop's sample", before, after,
                         {.propagate_backdrops = false}});
    }
    {
        Scene before = backdrop_scene(glass());
        Scene after = before;
        node(after, "stripe3").bounds.x -= 2.0F;
        cases.push_back({"a moved stripe inside frosted glass's sample", before, after,
                         {.propagate_backdrops = false}});
    }
    for (const auto& item : cases) {
        const auto covered = damage_coverage(item.before, item.after, scale);
        expect(covered.changed > 0 && covered.missed == 0,
               "the damage of " + item.what + " covers every changed pixel",
               coverage_detail(covered));
        DamageRulesOverride off(item.off);
        const auto missed = damage_coverage(item.before, item.after, scale);
        expect(missed.missed > 0,
               "negative control: without its damage rule " + item.what +
                   " leaves changed pixels outside the damage",
               coverage_detail(missed));
    }
    // Without the fill rule the renderer's enclosure cannot help once the
    // group loses every member: nothing is left to enclose, and the lifted
    // gaps between the old members lie outside their damage.
    DamageRulesOverride off({.fill_area = false});
    auto full = make_surface(scale);
    auto partial = make_surface(scale);
    Scene scene = filter_scene({channel_swap()});
    if (full && partial && render_both(*full, *partial, scene)) {
        node(scene, "fx").children.clear();
        if (render_both(*full, *partial, scene)) {
            std::string message;
            expect(!identical(*full, *partial, message) && !partial->frame_stats().full_repaint,
                   "negative control: without the fill rule a partial frame leaves the lifted "
                   "area stale",
                   message);
        }
    }
}

std::int64_t alpha_outside(const EffectPassCapture& pass, const PixelRect& keep) {
    std::int64_t count = 0;
    for (std::uint32_t y = 0; y < pass.height; ++y) {
        for (std::uint32_t x = 0; x < pass.width; ++x) {
            const bool inside = static_cast<std::int32_t>(x) >= keep.left &&
                                static_cast<std::int32_t>(x) < keep.right &&
                                static_cast<std::int32_t>(y) >= keep.top &&
                                static_cast<std::int32_t>(y) < keep.bottom;
            if (!inside && pass.rgba[(static_cast<std::size_t>(y) * pass.width + x) * 4U + 3U] != 0) {
                ++count;
            }
        }
    }
    return count;
}

std::int64_t differing_pixels(const EffectPassCapture& a, const EffectPassCapture& b,
                              const PixelRect* inside = nullptr, bool invert = false) {
    std::int64_t count = 0;
    for (std::uint32_t y = 0; y < a.height; ++y) {
        for (std::uint32_t x = 0; x < a.width; ++x) {
            if (inside != nullptr) {
                const bool in = static_cast<std::int32_t>(x) >= inside->left &&
                                static_cast<std::int32_t>(x) < inside->right &&
                                static_cast<std::int32_t>(y) >= inside->top &&
                                static_cast<std::int32_t>(y) < inside->bottom;
                if (in == invert) continue;
            }
            const auto offset = (static_cast<std::size_t>(y) * a.width + x) * 4U;
            if (!std::equal(a.rgba.begin() + static_cast<std::ptrdiff_t>(offset),
                            a.rgba.begin() + static_cast<std::ptrdiff_t>(offset + 4U),
                            b.rgba.begin() + static_cast<std::ptrdiff_t>(offset))) {
                ++count;
            }
        }
    }
    return count;
}

const EffectPassCapture* pass_named(const std::vector<EffectPassCapture>& passes,
                                    const std::string& name) {
    for (const auto& pass : passes) {
        if (pass.name == name) return &pass;
    }
    return nullptr;
}

std::string pass_names(const std::vector<EffectPassCapture>& passes) {
    std::string names;
    for (const auto& pass : passes) names += (names.empty() ? "" : ", ") + pass.name;
    return names;
}

// The device pixels the members of "fx" cover.
PixelRect member_extent(const Scene& scene, float scale) {
    const auto commands = make_paint_commands(scene);
    PixelRect extent{};
    for (const auto& command : commands) {
        if (!draws_content(command.kind) || command.node_id.rfind("fx/", 0) != 0) continue;
        const auto pixels = to_pixel_rect(transformed_bounds(command.transform, command.bounds), scale);
        if (pixels) extent = bounding_union(extent, *pixels);
    }
    return extent;
}

PixelRect grown(const PixelRect& rect, std::int32_t by) {
    return {rect.left - by, rect.top - by, rect.right + by, rect.bottom + by};
}

Scene without_front(Scene scene) {
    std::erase_if(scene.root.children, [](const SceneNode& child) { return child.id == "front"; });
    return scene;
}

void pass_captures() {
    const float scale = 2.0F;
    auto surface = make_surface(scale);
    if (!surface) return;
    std::string error;
    std::vector<EffectPassCapture> passes;

    const Scene blurred = without_front(filter_scene({blur(4.0F)}));
    const bool blur_ok = SkiaDawnSurfaceTestAccess::capture_effect_passes(*surface, blurred, "fx",
                                                                          passes, &error);
    expect(blur_ok && pass_names(passes) == "behind, content, filter 1 blur, composited",
           "a blur captures the scene behind, the members, the blur and the result",
           pass_names(passes) + " " + error);
    if (blur_ok && passes.size() == 4U) {
        const auto extent = member_extent(blurred, scale);
        const auto reach = static_cast<std::int32_t>(std::ceil(kEffectBlurReachSigmas * 4.0F * scale));
        expect(alpha_outside(passes[1], grown(extent, 2)) == 0,
               "the members' pass paints only within their extent");
        expect(alpha_outside(passes[2], grown(extent, 2)) > 0 &&
                   alpha_outside(passes[2], grown(extent, reach + 2)) == 0,
               "the blur spreads past the members and stays within its declared radius",
               std::to_string(alpha_outside(passes[2], grown(extent, 2))) + " beyond the extent, " +
                   std::to_string(alpha_outside(passes[2], grown(extent, reach + 2))) +
                   " beyond the radius");
        if (!surface->render(blurred, &error)) {
            expect(false, "the blur scene renders", error);
        } else {
            const auto frame = capture(*surface);
            const auto& composited = passes[3];
            expect(frame.ok && frame.result.rgba == composited.rgba,
                   "the composited pass is the frame the surface draws");
        }
    }

    const Scene chained = filter_scene({blur(2.5F), channel_swap(), drop_shadow()});
    const bool chain_ok = SkiaDawnSurfaceTestAccess::capture_effect_passes(*surface, chained, "fx",
                                                                           passes, &error);
    expect(chain_ok && pass_names(passes) ==
                           "behind, content, filter 1 blur, filter 2 color_matrix, "
                           "filter 3 drop_shadow, composited",
           "a chain captures every intermediate pass in order", pass_names(passes) + " " + error);
    if (chain_ok && passes.size() == 6U) {
        bool each_changes = true;
        for (std::size_t stage = 2; stage <= 4; ++stage) {
            each_changes = each_changes && differing_pixels(passes[stage - 1], passes[stage]) > 0;
        }
        expect(each_changes, "each pass of the chain changes the pixels before it");
        const auto extent = member_extent(chained, scale);
        // fx/a is red; after the channel swap its centre is blue.
        const auto& swapped = passes[3];
        const std::uint32_t x = static_cast<std::uint32_t>((60.5F + 10.0F + 20.0F) * scale);
        const std::uint32_t y = static_cast<std::uint32_t>((45.25F + 8.0F + 15.0F) * scale);
        const auto offset = (static_cast<std::size_t>(y) * swapped.width + x) * 4U;
        expect(swapped.rgba[offset + 2U] > 150U && swapped.rgba[offset] < 100U,
               "the colour matrix maps the red member to blue",
               std::to_string(swapped.rgba[offset]) + "," +
                   std::to_string(swapped.rgba[offset + 2U]));
        expect(alpha_outside(passes[2], grown(extent, 30)) == 0 &&
                   alpha_outside(passes[3], grown(extent, 30)) > 0,
               "a colour matrix that lifts alpha fills its cropped area and no more");
        const auto shadow_reach = static_cast<std::int32_t>(
            std::ceil((kEffectBlurReachSigmas * (2.5F + 3.0F) + 7.0F) * scale));
        expect(alpha_outside(passes[4], grown(extent, shadow_reach + 2)) == 0,
               "the drop shadow stays within the chain's declared radius");
    }

    const Scene pane = without_front(backdrop_scene(glass()));
    const bool glass_ok = SkiaDawnSurfaceTestAccess::capture_effect_passes(*surface, pane, "pane",
                                                                           passes, &error);
    expect(glass_ok && pass_names(passes) == "behind, content, backdrop, composited",
           "frosted glass captures the scene behind, the members, the backdrop and the result",
           pass_names(passes) + " " + error);
    if (glass_ok && passes.size() == 4U) {
        const auto commands = make_paint_commands(pane);
        PixelRect box{};
        for (const auto& command : commands) {
            if (command.node_id == "pane" && command.kind == PaintCommand::Kind::begin_layer) {
                box = *to_pixel_rect(transformed_bounds(command.transform, command.effect_box),
                                     scale);
            }
        }
        expect(differing_pixels(passes[0], passes[2], &box) > 0 &&
                   differing_pixels(passes[0], passes[2], &box, true) == 0,
               "the backdrop pass changes pixels within the pane's box and none outside",
               std::to_string(differing_pixels(passes[0], passes[2], &box)) + " inside, " +
                   std::to_string(differing_pixels(passes[0], passes[2], &box, true)) +
                   " outside");
        if (surface->render(pane, &error)) {
            const auto frame = capture(*surface);
            expect(frame.ok && frame.result.rgba == passes[3].rgba,
                   "the composited glass pass is the frame the surface draws");
        }
    }
    const Scene plain_pane = backdrop_scene(backdrop(4.0F));
    std::vector<EffectPassCapture> plain_passes;
    if (SkiaDawnSurfaceTestAccess::capture_effect_passes(*surface, plain_pane, "pane",
                                                         plain_passes, &error) &&
        glass_ok && plain_passes.size() == 4U && passes.size() == 4U) {
        expect(differing_pixels(plain_passes[2], passes[2]) > 0,
               "the glass backdrop differs from a plain backdrop blur");
    }
}

std::vector<std::uint8_t> render_pixels(const Scene& scene, float scale, bool cached = false) {
    auto surface = make_surface(scale, cached);
    std::string error;
    if (!surface || !surface->render(scene, &error)) {
        expect(false, "a scene renders", error);
        return {};
    }
    const auto frame = capture(*surface);
    return frame.ok ? frame.result.rgba : std::vector<std::uint8_t>{};
}

void budget_fallback() {
    const float scale = 2.0F;
    const Scene over = filter_scene({blur(1.5F), blur(1.5F), blur(1.5F), blur(1.5F)});
    const Scene within = filter_scene({blur(1.5F), blur(1.5F), blur(1.5F)});
    const Scene bare = without_effects(over);
    const auto plain = render_pixels(bare, scale);
    expect(!plain.empty() && render_pixels(over, scale) == plain,
           "an over-budget chain renders exactly as the node without effects");
    expect(render_pixels(within, scale) != plain,
           "negative control: a chain within budget changes the pixels");
    auto surface = make_surface(scale);
    std::string error;
    if (surface && surface->render(over, &error)) {
        const auto& diagnostics = surface->diagnostics();
        expect(diagnostics.size() == 1U && diagnostics[0].code == kEffectBudgetExceededCode,
               "the surface reports the over-budget chain as a loss");
    }
}

void cached_layers() {
    const float scale = 2.0F;
    for (auto scene : {filter_scene({blur(4.0F)}), backdrop_scene(glass()), chain_scene()}) {
        auto cached = make_surface(scale, true);
        auto direct = make_surface(scale, false);
        std::string error;
        if (!cached || !direct) return;
        const std::string id = scene.root.children.size() > 13U &&
                                       find_node(scene, "pane") != nullptr ? "pane" : "fx";
        expect(cached->render(scene, &error) && direct->render(scene, &error), "renders", error);
        const auto first = cached->layer_cache_stats();
        expect(cached->render(scene, &error), "renders again", error);
        const auto second = cached->layer_cache_stats();
        std::string message;
        expect(first.builds == 1U && second.hits == 1U && second.builds == 0U &&
                   identical(*cached, *direct, message),
               id + ": the effect group's members are kept as a cached layer and drawn "
                    "through the effects exactly as without the cache",
               "builds " + std::to_string(first.builds) + " hits " +
                   std::to_string(second.hits) + " " + message);
        auto& effects = node(scene, id).effects;
        if (effects[0].kind == EffectKind::material) {
            effects[0].values[0].value[0] = 9.0F;
        } else {
            effects[0].sigma = 6.0F;
        }
        expect(cached->render(scene, &error) && direct->render(scene, &error), "renders", error);
        const auto changed = cached->layer_cache_stats();
        expect(changed.hits == 1U && changed.builds == 0U && identical(*cached, *direct, message),
               id + ": an effect parameter change reuses the cached members exactly",
               "hits " + std::to_string(changed.hits) + " " + message);
    }
}

// An effect group inside another group drawn offscreen, placed away from
// the outer group's origin, whose drop shadow reaches past its members.
Scene nested_scene(bool outer_effect, float outer_opacity) {
    Scene scene = striped_scene();
    SceneNode outer{.id = "outer", .kind = SceneNode::Kind::group,
                    .bounds = {20.5F, 20.25F, 200.0F, 140.0F}};
    SceneNode inner{.id = "inner", .kind = SceneNode::Kind::group,
                    .bounds = {120.0F, 50.0F, 50.0F, 40.0F}};
    inner.children.push_back(box("inner/a", {0.0F, 0.0F, 30.0F, 24.0F}, Color::hex(0xEF4444), 5.0F));
    inner.children.push_back(box("inner/b", {12.0F, 10.0F, 30.0F, 24.0F},
                                 Color::rgba(0.23F, 0.51F, 0.96F, 0.8F), 4.0F));
    inner.effects = {{.kind = EffectKind::drop_shadow,
                      .sigma = 3.0F,
                      .offset_x = 9.0F,
                      .offset_y = 7.0F,
                      .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.8F)}};
    outer.children.push_back(std::move(inner));
    if (outer_effect) {
        outer.effects = {blur(0.0F)};
    } else {
        outer.isolate = true;
    }
    outer.opacity = outer_opacity;
    scene.root.children.push_back(std::move(outer));
    return scene;
}

void nested_effects() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string size = "@" + std::to_string(static_cast<int>(scale)) + " ";
        const auto isolated = render_pixels(nested_scene(false, 1.0F), scale);
        const auto offscreen = render_pixels(nested_scene(true, 1.0F), scale);
        expect(!isolated.empty() && offscreen == isolated,
               size + "an effect group nested in another effect group lands where an isolated "
                      "parent puts it, shadow whole");
        auto cached = make_surface(scale, true);
        auto direct = make_surface(scale, false);
        std::string error;
        const Scene faded = nested_scene(false, 0.7F);
        if (!cached || !direct || !cached->render(faded, &error) ||
            !direct->render(faded, &error) || !cached->render(faded, &error)) {
            expect(false, size + "the faded nested scene renders", error);
            continue;
        }
        std::string message;
        const auto stats = cached->layer_cache_stats();
        expect(stats.hits == 1U && identical(*cached, *direct, message),
               size + "an effect group inside a cached faded group draws as without the cache",
               "hits " + std::to_string(stats.hits) + " " + message);
    }
    for (const bool cached : {false, true}) {
        const auto sweep = sweep_damage_edges(nested_scene(true, 0.85F), 2.0F, cached);
        expect(sweep.differing == 0 && sweep.partial > 0,
               std::string(cached ? "cache on " : "cache off ") +
                   "a damage edge swept across nested effect groups leaves every frame "
                   "byte-identical",
               sweep_detail(sweep));
    }
}

void extension_material_renders_without_it() {
    MaterialDefinition app{
        .id = "com.example.sheen",
        .provenance = {.owner = "com.example", .source = "sheen.dylib"},
        .component = "sheen",
        .passes = 1U,
    };
    std::string error;
    expect(MaterialRegistry::shared().register_extension(app, &error),
           "an application material registers", error);
    Scene scene = backdrop_scene({.kind = EffectKind::material, .material = app.id});
    Scene bare = without_effects(scene);
    const auto with_extension = render_pixels(scene, 2.0F);
    expect(!with_extension.empty() && with_extension == render_pixels(bare, 2.0F),
           "the native renderer draws a node without its application material");
    (void)MaterialRegistry::shared().unregister_extension(app.id);
}

}  // namespace

int main() {
    try {
        std::string error;
        if (!harness::prepare_device(&error)) {
            std::cerr << "FAIL device bootstrap: " << error << '\n';
            return 1;
        }
        pass_captures();
        budget_fallback();
        cached_layers();
        extension_material_renders_without_it();
        nested_effects();
        damage_rule_controls();
        edit_sequences();
        sweeps();
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "effects on the GPU " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
