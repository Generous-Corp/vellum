#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/layer_cache.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <set>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;
using Kind = PaintCommand::Kind;

int failures = 0;

void expect(bool ok, const std::string& what) {
    if (ok) return;
    ++failures;
    std::cerr << "FAIL " << what << '\n';
}

SceneNode box(std::string id, Rect bounds, std::uint32_t color = 0xFFFFFF) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = Color::hex(color)};
}

SceneNode blended(std::string id, Rect bounds, BlendMode mode) {
    auto node = box(std::move(id), bounds);
    node.blend_mode = mode;
    return node;
}

Scene scene_with(std::vector<SceneNode> children) {
    Scene scene{.width = 100.0F, .height = 100.0F};
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, 100.0F, 100.0F};
    scene.root.children = std::move(children);
    return scene;
}

const PaintCommand* layer_of(const std::vector<PaintCommand>& commands, std::string_view id) {
    for (const auto& command : commands) {
        if (command.kind == Kind::begin_layer && command.node_id == id) return &command;
    }
    return nullptr;
}

void vocabulary() {
    static_assert(kAllBlendModes.size() == 29U);
    static_assert(blend_mode_tier(BlendMode::normal) == 1);
    static_assert(blend_mode_tier(BlendMode::destination_out) == 1);
    static_assert(blend_mode_tier(BlendMode::hue) == 2);
    static_assert(blend_mode_tier(BlendMode::plus) == 2);
    static_assert(blend_mode_tier(static_cast<BlendMode>(29)) == 0);
    static_assert(!is_known_blend_mode(static_cast<BlendMode>(255)));

    const std::set<std::string_view> tier_one{
        "normal", "multiply", "screen", "overlay", "darken", "lighten", "difference",
        "exclusion", "source_over", "destination_in", "destination_out"};
    std::set<std::string_view> names;
    int ones = 0;
    int twos = 0;
    for (const auto mode : kAllBlendModes) {
        const auto name = to_string(mode);
        expect(names.insert(name).second, "blend mode name " + std::string(name) + " is unique");
        expect(parse_blend_mode(name) == mode, "blend mode " + std::string(name) + " round-trips");
        expect(tier_one.contains(name) == (blend_mode_tier(mode) == 1),
               "blend mode " + std::string(name) + " is in its tier");
        (blend_mode_tier(mode) == 1 ? ones : twos) += 1;
    }
    expect(ones == 11 && twos == 18, "eleven Tier 1 modes and eighteen Tier 2 modes");
    expect(parse_blend_mode("color-dodge") == BlendMode::color_dodge &&
               parse_blend_mode("hard-light") == BlendMode::hard_light &&
               parse_blend_mode("destination-in") == BlendMode::destination_in,
           "CSS hyphenated spellings parse");
    expect(!parse_blend_mode("").has_value() && !parse_blend_mode("Multiply").has_value() &&
               !parse_blend_mode("multiplyy").has_value() && !parse_blend_mode("plus-lighter"),
           "unknown spellings do not parse");
    expect(to_string(static_cast<BlendMode>(200)) == "unknown", "an unknown value has no name");
}

void lowering() {
    auto parent = box("parent", {0.0F, 0.0F, 80.0F, 80.0F});
    parent.children = {box("below", {0.0F, 0.0F, 40.0F, 40.0F}),
                       blended("lone", {10.0F, 10.0F, 30.0F, 30.0F}, BlendMode::multiply)};
    const auto commands = make_paint_commands(scene_with({parent}));
    const auto* layer = layer_of(commands, "lone");
    expect(layer != nullptr && layer->blend_mode == BlendMode::multiply &&
               layer->opacity == 1.0F,
           "a blended lone command is wrapped in a layer that carries the mode");
    expect(commands.size() == 5U && commands[3].kind == Kind::rectangle &&
               commands[3].blend_mode == BlendMode::normal &&
               commands[4].kind == Kind::end_layer,
           "a blend never folds into the command it blends");
    expect(layer_of(commands, "parent") == nullptr && layer_of(commands, "below") == nullptr,
           "an unblended parent and sibling stay unwrapped");

    auto faded = blended("faded", {0.0F, 0.0F, 30.0F, 30.0F}, BlendMode::screen);
    faded.opacity = 0.5F;
    faded.children = {box("faded/a", {0.0F, 0.0F, 10.0F, 10.0F}),
                      box("faded/b", {5.0F, 5.0F, 10.0F, 10.0F})};
    const auto fade_commands = make_paint_commands(scene_with({faded}));
    const auto* fade = layer_of(fade_commands, "faded");
    expect(fade != nullptr && fade->blend_mode == BlendMode::screen &&
               fade->opacity == 0.5F && !fade->layer_isolated,
           "a faded blended group keeps its opacity and mode on one layer");

    auto nested = blended("outer", {0.0F, 0.0F, 50.0F, 50.0F}, BlendMode::overlay);
    nested.children = {blended("outer/inner", {0.0F, 0.0F, 20.0F, 20.0F}, BlendMode::difference)};
    const auto nested_commands = make_paint_commands(scene_with({nested}));
    const auto* outer = layer_of(nested_commands, "outer");
    const auto* inner = layer_of(nested_commands, "outer/inner");
    expect(outer != nullptr && inner != nullptr && outer->blend_mode == BlendMode::overlay &&
               inner->blend_mode == BlendMode::difference,
           "nested blended groups each keep their own mode");

    auto source_over = box("source-over", {0.0F, 0.0F, 10.0F, 10.0F});
    source_over.blend_mode = BlendMode::source_over;
    const auto explicit_commands = make_paint_commands(scene_with({source_over}));
    expect(layer_of(explicit_commands, "source-over") != nullptr &&
               make_paint_commands(scene_with({box("plain", {0.0F, 0.0F, 10.0F, 10.0F})})).size() ==
                   1U,
           "source_over always draws a group, normal never does");

    auto hidden = blended("hidden", {0.0F, 0.0F, 10.0F, 10.0F}, BlendMode::multiply);
    hidden.opacity = 0.0F;
    expect(make_paint_commands(scene_with({hidden})).empty(),
           "a fully faded blended node draws nothing");

    auto unknown = blended("unknown", {0.0F, 0.0F, 10.0F, 10.0F}, static_cast<BlendMode>(77));
    unknown.children = {box("unknown/child", {0.0F, 0.0F, 5.0F, 5.0F})};
    const auto kept = make_paint_commands(scene_with({unknown, box("known", {0.0F, 0.0F, 4.0F, 4.0F})}));
    expect(kept.size() == 1U && kept[0].node_id == "known",
           "a node whose blend mode is unknown contributes nothing, with its subtree");
    expect(!accept_scene(scene_with({unknown})).accepted &&
               accept_scene(scene_with({unknown})).reason ==
                   "node unknown has a blend mode this build does not know",
           "an unknown blend mode refuses the scene");

    std::vector<PaintCommandRange> ranges;
    const auto ranged = make_paint_commands(scene_with({blended("a", {0, 0, 5, 5}, BlendMode::hue)}),
                                            &ranges);
    expect(ranges.size() == 2U && ranges[1].begin == 0U && ranges[1].end == ranged.size() &&
               ranged.front().kind == Kind::begin_layer,
           "a blended node's range holds its layer");
}

void versioning() {
    static_assert(kSceneVersion == 6U);
    static_assert(kDamageModelSceneVersion == 6U);
    auto node = blended("node", {0.0F, 0.0F, 10.0F, 10.0F}, BlendMode::exclusion);
    auto scene = scene_with({box("sibling", {0.0F, 0.0F, 1.0F, 1.0F}), node});
    expect(uses_version_6_fields(node) && !uses_version_6_fields(scene.root.children[0]) &&
               find_version_6_fields(scene.root) != nullptr &&
               find_version_6_fields(scene.root)->id == "node",
           "a blend mode is a version 6 field");
    for (std::uint32_t version = 1U; version < 6U; ++version) {
        scene.scene_version = version;
        expect(!scene_fields_match_version(scene) && make_paint_commands(scene).empty() &&
                   find_version_mismatch(scene) != nullptr &&
                   find_version_mismatch(scene)->id == "node",
               "a version " + std::to_string(version) + " scene with a blend mode is refused");
    }
    scene.scene_version = 5U;
    const auto loss = scene_loss_diagnostics(scene);
    expect(loss.size() == 1U && loss[0].code == kSceneVersionMismatchCode &&
               loss[0].severity == LoweringDiagnostic::Severity::error &&
               loss[0].node_id == "node" && loss[0].property == "blendMode" &&
               loss[0].message == "scene version 5 cannot carry the blend mode at node",
           "a version 5 scene with a blend mode reports a version-mismatch loss");
    const auto acceptance = accept_scene(scene);
    expect(!acceptance.accepted && acceptance.reason == loss[0].message &&
               acceptance.diagnostics.size() == 1U,
           "accept_scene refuses a version 5 scene with a blend mode");
    scene.scene_version = 6U;
    expect(scene_fields_match_version(scene) && !make_paint_commands(scene).empty() &&
               scene_loss_diagnostics(scene).empty() && accept_scene(scene).accepted,
           "a version 6 scene with a blend mode draws");
    auto isolated = box("iso", {0.0F, 0.0F, 3.0F, 3.0F});
    isolated.isolate = true;
    auto older = scene_with({isolated});
    older.scene_version = 5U;
    expect(scene_fields_match_version(older) && make_paint_commands(older).size() == 3U,
           "a version 5 scene without a blend mode still draws");
    older.scene_version = 4U;
    older.root.children.push_back(node);
    expect(scene_loss_diagnostics(older)[0].message ==
               "scene version 4 cannot carry the clip, mask, or isolation at iso",
           "an older field is named before a blend mode");
}

void damage() {
    auto before = scene_with({box("under", {10.0F, 10.0F, 40.0F, 40.0F}),
                              box("over", {30.0F, 30.0F, 40.0F, 40.0F})});
    auto after = before;
    after.root.children[1].blend_mode = BlendMode::multiply;
    expect(!compute_scene_damage(before, after, 1.0F).empty(),
           "adding a blend mode damages the node");
    const auto changed = compute_scene_damage(before, after, 1.0F).bounds();
    expect(changed.left <= 30 && changed.top <= 30 && changed.right >= 70 && changed.bottom >= 70,
           "the damage of a blend change holds the whole node");
    auto switched = after;
    switched.root.children[1].blend_mode = BlendMode::screen;
    expect(!compute_scene_damage(after, switched, 1.0F).empty(),
           "switching between two blend modes damages the node");
    expect(compute_scene_damage(after, after, 1.0F).empty(), "an unchanged blend damages nothing");

    auto clearing = before;
    clearing.root.children[1].blend_mode = BlendMode::destination_in;
    const auto whole = compute_scene_damage(before, clearing, 1.0F);
    expect(whole.is_full_frame() || whole.area() >= 100 * 100,
           "a mode that empties the backdrop damages the whole frame");
    expect(compute_scene_damage(before, after, 1.0F).area() < 100 * 100,
           "negative control: a mode that only blends damages less than the frame");
    for (const auto mode : kAllBlendModes) {
        auto probe = before;
        probe.root.children[1].blend_mode = mode;
        const auto area = compute_scene_damage(before, probe, 1.0F).area();
        expect(mode == BlendMode::normal ? area == 0
                                         : (blend_mode_clears_backdrop(mode) == (area >= 100 * 100)),
               "only a backdrop-clearing mode damages the frame: " + std::string(to_string(mode)));
    }

    auto group_before = scene_with({});
    auto group = box("group", {10.0F, 10.0F, 60.0F, 60.0F});
    group.children = {box("group/a", {0.0F, 0.0F, 20.0F, 20.0F}),
                      box("group/b", {30.0F, 30.0F, 20.0F, 20.0F})};
    group_before.root.children = {group};
    auto group_after = group_before;
    group_after.root.children[0].blend_mode = BlendMode::darken;
    const auto subtree = compute_scene_damage(group_before, group_after, 1.0F).bounds();
    expect(subtree.left <= 10 && subtree.right >= 60 && subtree.bottom >= 60,
           "a blend change on a group damages its whole subtree");
}

std::vector<LayerCache::Layer> plan_frames(LayerCache& cache, const Scene& scene, int frames) {
    std::vector<LayerCache::Layer> last;
    for (int frame = 0; frame < frames; ++frame) {
        std::vector<PaintCommandRange> ranges;
        const auto commands = make_paint_commands(scene, &ranges);
        last = cache.plan(commands, ranges, 100 * 100);
        (void)cache.take_dropped();
        for (const auto& layer : last) {
            if (layer.build) (void)cache.admit(layer.node_id, 100);
        }
    }
    return last;
}

bool planned(const std::vector<LayerCache::Layer>& layers, std::string_view id) {
    return std::any_of(layers.begin(), layers.end(),
                       [&](const LayerCache::Layer& layer) { return layer.node_id == id; });
}

// A cached image is drawn on a transparent surface, so a blend recorded into
// it would meet no backdrop. The cache keeps blended groups, and the plain
// groups that hold one, out of its images; a faded group that holds one is its
// own backdrop and caches whole.
void layer_cache() {
    const auto build = [](BlendMode mode) {
        auto panel = box("panel", {5.0F, 5.0F, 60.0F, 60.0F});
        panel.children = {box("panel/a", {0.0F, 0.0F, 20.0F, 20.0F}),
                          blended("panel/b", {10.0F, 10.0F, 20.0F, 20.0F}, mode),
                          box("panel/c", {30.0F, 30.0F, 10.0F, 10.0F})};
        auto fade = box("fade", {10.0F, 10.0F, 60.0F, 60.0F});
        fade.opacity = 0.5F;
        fade.children = {box("fade/a", {0.0F, 0.0F, 20.0F, 20.0F}),
                         blended("fade/b", {10.0F, 10.0F, 20.0F, 20.0F}, mode),
                         box("fade/c", {30.0F, 30.0F, 10.0F, 10.0F})};
        auto own = blended("own", {20.0F, 20.0F, 60.0F, 60.0F}, mode);
        own.opacity = 0.5F;
        own.children = {box("own/a", {0.0F, 0.0F, 20.0F, 20.0F}),
                        box("own/b", {10.0F, 10.0F, 20.0F, 20.0F})};
        return scene_with({panel, fade, own});
    };
    const LayerCachePolicy policy{.allow_inexact = true, .static_frames = 2};
    LayerCache blending(policy);
    const auto with_blend = plan_frames(blending, build(BlendMode::multiply), 6);
    expect(!planned(with_blend, "panel"),
           "a static group holding a blended group is not cached");
    expect(!planned(with_blend, "own"), "a blended faded group is not cached");
    expect(planned(with_blend, "fade"),
           "a faded group holding a blended group caches whole, as its own backdrop");
    expect(!planned(with_blend, "panel/b") && !planned(with_blend, "fade/b"),
           "a lone blended command is never a candidate");

    LayerCache plain(policy);
    const auto without = plan_frames(plain, build(BlendMode::normal), 6);
    expect(planned(without, "panel"),
           "negative control: the same static group caches once nothing in it blends");
    expect(planned(without, "own"),
           "negative control: the same faded group caches once it does not blend");
}

void hashing() {
    auto first = blended("a", {0, 0, 10, 10}, BlendMode::multiply);
    first.opacity = 0.5F;
    first.children = {box("a/x", {0, 0, 4, 4}), box("a/y", {5, 5, 4, 4})};
    auto second = first;
    second.blend_mode = BlendMode::screen;
    const auto one = make_paint_commands(scene_with({first}));
    const auto two = make_paint_commands(scene_with({second}));
    expect(one.size() == two.size() && !paint_commands_equal(one.front(), two.front()),
           "two layers that differ only by blend mode are not the same paint");
    expect(paint_commands_equal(one.front(), one.front()), "a layer equals itself");
}

}  // namespace

int main() {
    vocabulary();
    lowering();
    versioning();
    damage();
    layer_cache();
    hashing();
    if (failures != 0) {
        std::cerr << failures << " blend mode check(s) failed\n";
        return 1;
    }
    std::cout << "blend mode checks passed\n";
    return 0;
}
