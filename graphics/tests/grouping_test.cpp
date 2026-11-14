#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
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

bool near(float left, float right) { return std::abs(left - right) < 0.0001F; }

bool near_rect(const Rect& rect, float x, float y, float width, float height) {
    return near(rect.x, x) && near(rect.y, y) && near(rect.width, width) &&
           near(rect.height, height);
}

SceneNode box(std::string id, Rect bounds, std::uint32_t color = 0xFFFFFF) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = Color::hex(color)};
}

std::shared_ptr<const PathGeometry> triangle() {
    return std::make_shared<const PathGeometry>(PathGeometry{
        .verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close},
        .values = {0.0F, 0.0F, 10.0F, 0.0F, 0.0F, 10.0F},
    });
}

std::vector<std::pair<Kind, std::string>> shape(const std::vector<PaintCommand>& commands) {
    std::vector<std::pair<Kind, std::string>> result;
    for (const auto& command : commands) result.emplace_back(command.kind, command.node_id);
    return result;
}

Scene scene_with(std::vector<SceneNode> children) {
    Scene scene{.width = 100.0F, .height = 100.0F};
    scene.root.id = "root";
    scene.root.children = std::move(children);
    return scene;
}

// A clip wraps the node's own paint and its subtree, in absolute coordinates,
// and carries the node's composed transform.
void clip_commands() {
    auto card = box("card", {10.0F, 20.0F, 40.0F, 30.0F});
    card.clip = NodeClip{.rect = {2.0F, 3.0F, 20.0F, -5.0F},
                         .radii = {1.0F, 2.0F, 3.0F, 4.0F}};
    card.transform = Affine2D{.a = 2.0F, .d = 2.0F};
    card.children.push_back(box("card/child", {1.0F, 1.0F, 5.0F, 5.0F}));
    auto outline = box("outline", {50.0F, 50.0F, 10.0F, 10.0F});
    outline.kind = SceneNode::Kind::group;
    outline.clip = NodeClip{.path = triangle()};
    outline.children.push_back(box("outline/child", {0.0F, 0.0F, 10.0F, 10.0F}));
    const auto commands = make_paint_commands(scene_with({card, outline}));
    const std::vector<std::pair<Kind, std::string>> expected{
        {Kind::begin_clip, "card"},      {Kind::rectangle, "card"},
        {Kind::rectangle, "card/child"}, {Kind::end_clip, "card"},
        {Kind::begin_clip, "outline"},   {Kind::rectangle, "outline/child"},
        {Kind::end_clip, "outline"},
    };
    expect(shape(commands) == expected, "clips bracket the node's paint and subtree");
    if (commands.size() != expected.size()) return;
    const auto& clip = commands[0];
    expect(near_rect(clip.clip_rect, 12.0F, 23.0F, 20.0F, 0.0F),
           "the clip rectangle is absolute and a negative size clamps to zero");
    expect(near(clip.corner_radii.top_left, 1.0F) && near(clip.corner_radii.bottom_left, 4.0F),
           "the clip carries its own radii");
    expect(near(clip.transform.a, 2.0F) && near(clip.transform.tx, -10.0F) &&
               near(clip.transform.ty, -20.0F),
           "the clip moves with the node's transform about its origin");
    expect(!clip.path, "a rectangle clip has no path");
    expect(commands[4].path == outline.clip->path &&
               near_rect(commands[4].bounds, 50.0F, 50.0F, 10.0F, 10.0F),
           "a path clip keeps its geometry and the node's absolute box");
    expect(!draws_content(Kind::begin_clip) && !draws_content(Kind::end_clip) &&
               !draws_content(Kind::begin_mask) && !draws_content(Kind::end_mask),
           "clip and mask brackets draw no content");
    expect(to_string(Kind::begin_clip) == "begin_clip" && to_string(Kind::end_mask) == "end_mask",
           "the new kinds have names");
}

// A clip that spares the node's own paint opens after that paint, inside the
// node's layer, and closes before the layer does.
void subtree_clip_commands() {
    auto card = box("card", {10.0F, 10.0F, 20.0F, 20.0F});
    card.box_shadows = {{.offset_x = 8.0F, .color = Color::hex(0x000000)}};
    card.opacity = 0.5F;
    card.clip = NodeClip{.rect = {0.0F, 0.0F, 20.0F, 20.0F}, .clips_own_paint = false};
    card.children = {box("card/a", {0.0F, 0.0F, 5.0F, 5.0F}), box("card/b", {5.0F, 5.0F, 5.0F, 5.0F})};
    const auto commands = make_paint_commands(scene_with({card}));
    const std::vector<std::pair<Kind, std::string>> expected{
        {Kind::begin_layer, "card"}, {Kind::rectangle, "card"}, {Kind::begin_clip, "card"},
        {Kind::rectangle, "card/a"}, {Kind::rectangle, "card/b"}, {Kind::end_clip, "card"},
        {Kind::end_layer, "card"},
    };
    expect(shape(commands) == expected, "a subtree clip leaves the node's own paint outside it");
    auto lone = box("lone", {0.0F, 0.0F, 5.0F, 5.0F});
    lone.clip = NodeClip{.rect = {0.0F, 0.0F, 1.0F, 1.0F}, .clips_own_paint = false};
    const std::vector<std::pair<Kind, std::string>> bare{
        {Kind::rectangle, "lone"}, {Kind::begin_clip, "lone"}, {Kind::end_clip, "lone"}};
    expect(shape(make_paint_commands(scene_with({lone}))) == bare,
           "a subtree clip without children still balances");
}

// Isolation forces a layer even at full opacity and for a single command;
// clip, layer and paint nest in that order.
void isolation_commands() {
    auto lone = box("lone", {0.0F, 0.0F, 10.0F, 10.0F});
    lone.isolate = true;
    auto both = box("both", {20.0F, 20.0F, 10.0F, 10.0F});
    both.isolate = true;
    both.opacity = 0.5F;
    both.clip = NodeClip{.rect = {0.0F, 0.0F, 5.0F, 5.0F}};
    auto faded = box("faded", {40.0F, 40.0F, 10.0F, 10.0F});
    faded.opacity = 0.5F;
    const auto commands = make_paint_commands(scene_with({lone, both, faded}));
    const std::vector<std::pair<Kind, std::string>> expected{
        {Kind::begin_layer, "lone"}, {Kind::rectangle, "lone"},   {Kind::end_layer, "lone"},
        {Kind::begin_clip, "both"},  {Kind::begin_layer, "both"}, {Kind::rectangle, "both"},
        {Kind::end_layer, "both"},   {Kind::end_clip, "both"},    {Kind::rectangle, "faded"},
    };
    expect(shape(commands) == expected, "isolation keeps a layer that opacity alone folds");
    if (commands.size() != expected.size()) return;
    expect(commands[0].layer_isolated && near(commands[0].opacity, 1.0F) &&
               near_rect(commands[0].bounds, 0.0F, 0.0F, 10.0F, 10.0F),
           "an isolated layer is marked, opaque and bounded by its members");
    expect(commands[4].layer_isolated && near(commands[4].opacity, 0.5F) &&
               near(commands[5].opacity, 1.0F),
           "an isolated faded layer keeps its opacity on the layer");
    expect(near(commands[8].opacity, 0.5F), "a lone faded node still folds its opacity");
}

// Mask children are not drawn as siblings: the other children form a masked
// group that ends with one mask pair per mask child.
void mask_commands() {
    SceneNode parent = box("parent", {10.0F, 10.0F, 50.0F, 50.0F});
    parent.children.push_back(box("parent/a", {0.0F, 0.0F, 10.0F, 10.0F}));
    auto alpha = box("parent/alpha", {5.0F, 5.0F, 10.0F, 10.0F});
    alpha.mask = MaskMode::alpha;
    alpha.children.push_back(box("parent/alpha/inner", {1.0F, 1.0F, 2.0F, 2.0F}));
    parent.children.push_back(alpha);
    parent.children.push_back(box("parent/b", {20.0F, 20.0F, 10.0F, 10.0F}));
    auto empty = box("parent/empty", {0.0F, 0.0F, 1.0F, 1.0F});
    empty.mask = MaskMode::luminance;
    empty.opacity = 0.0F;
    parent.children.push_back(empty);
    const auto commands = make_paint_commands(scene_with({parent}));
    const std::vector<std::pair<Kind, std::string>> expected{
        {Kind::rectangle, "parent"},
        {Kind::begin_layer, "parent/masked"},
        {Kind::rectangle, "parent/a"},
        {Kind::rectangle, "parent/b"},
        {Kind::begin_mask, "parent/alpha"},
        {Kind::rectangle, "parent/alpha"},
        {Kind::rectangle, "parent/alpha/inner"},
        {Kind::end_mask, "parent/alpha"},
        {Kind::begin_mask, "parent/empty"},
        {Kind::end_mask, "parent/empty"},
        {Kind::end_layer, "parent/masked"},
    };
    expect(shape(commands) == expected, "masks follow the siblings they mask");
    if (commands.size() != expected.size()) return;
    expect(commands[1].layer_isolated && near(commands[1].opacity, 1.0F) &&
               near_rect(commands[1].bounds, 10.0F, 10.0F, 30.0F, 30.0F),
           "the masked group is isolated and bounded by the siblings alone");
    expect(commands[4].mask_mode == MaskMode::alpha &&
               commands[8].mask_mode == MaskMode::luminance,
           "each mask pair carries its mode");

    // A faded parent whose only visible paint is masked keeps its layer,
    // because folding into a masked or masking command changes coverage.
    SceneNode faded{.id = "faded", .kind = SceneNode::Kind::group, .opacity = 0.5F};
    faded.children.push_back(box("faded/content", {0.0F, 0.0F, 10.0F, 10.0F}));
    auto empty_mask = box("faded/mask", {0.0F, 0.0F, 0.0F, 0.0F});
    empty_mask.kind = SceneNode::Kind::group;
    empty_mask.mask = MaskMode::alpha;
    faded.children.push_back(empty_mask);
    const auto layered = make_paint_commands(scene_with({faded}));
    expect(!layered.empty() && layered.front().kind == Kind::begin_layer &&
               layered.front().node_id == "faded" && near(layered.front().opacity, 0.5F) &&
               near(layered[2].opacity, 1.0F),
           "a faded parent of a mask keeps its opacity layer");

    auto root_mask = scene_with({box("a", {0.0F, 0.0F, 1.0F, 1.0F})});
    root_mask.root.mask = MaskMode::alpha;
    expect(make_paint_commands(root_mask).empty(), "a scene whose root is a mask draws nothing");
}

void clip_validity() {
    std::string reason;
    expect(clip_is_valid(NodeClip{}) && clip_is_valid(NodeClip{.path = triangle()}),
           "plain clips are valid");
    NodeClip infinite{.rect = {0.0F, 0.0F, std::numeric_limits<float>::infinity(), 1.0F}};
    expect(!clip_is_valid(infinite, &reason) && reason == "the clip rectangle is not finite",
           "a non-finite clip rectangle is invalid");
    NodeClip radius{.radii = {0.0F, std::numeric_limits<float>::quiet_NaN(), 0.0F, 0.0F}};
    expect(!clip_is_valid(radius, &reason) && reason == "a clip corner radius is not finite",
           "a non-finite clip radius is invalid");
    NodeClip broken{.path = std::make_shared<const PathGeometry>(
                        PathGeometry{.verbs = {PathVerb::line}, .values = {1.0F, 1.0F}})};
    reason.clear();
    expect(!clip_is_valid(broken, &reason) && !reason.empty(),
           "a clip path that fails path_is_valid is invalid");

    auto bad = box("bad", {0.0F, 0.0F, 10.0F, 10.0F});
    bad.clip = broken;
    bad.children.push_back(box("bad/child", {0.0F, 0.0F, 1.0F, 1.0F}));
    const auto scene = scene_with({bad, box("good", {0.0F, 0.0F, 1.0F, 1.0F})});
    const auto commands = make_paint_commands(scene);
    expect(commands.size() == 1U && commands[0].node_id == "good",
           "a node with an invalid clip contributes nothing for its subtree");
    std::string why;
    const auto* found = find_invalid_clip(scene.root, why);
    expect(found != nullptr && found->id == "bad" && !why.empty(), "find_invalid_clip names it");

    expect(near_rect(clip_extent(NodeClip{.rect = {1.0F, 2.0F, -3.0F, 4.0F}}, 10.0F, 20.0F),
                     11.0F, 22.0F, 0.0F, 4.0F),
           "a rectangle clip's extent is absolute with sizes clamped");
    expect(near_rect(clip_extent(NodeClip{.path = triangle()}, 10.0F, 20.0F), 10.0F, 20.0F,
                     10.0F, 10.0F),
           "a path clip's extent is its control bounds");
    expect(near_rect(clip_extent(NodeClip{.path = std::make_shared<const PathGeometry>()}, 3.0F,
                                 4.0F),
                     3.0F, 4.0F, 0.0F, 0.0F),
           "an empty path clip has an empty extent");
}

// Every grouping field is refused before version 5 and accepted at 5.
void versioning() {
    static_assert(kSceneVersion == 8U);
    static_assert(scene_version_is_supported(5U));
    static_assert(kDamageModelSceneVersion == 8U);
    static_assert(partial_redraw_supported(8U));
    static_assert(!partial_redraw_supported(9U));

    const auto variants = std::vector<std::pair<std::string, SceneNode>>{
        {"clip",
         [] {
             auto node = box("node", {0.0F, 0.0F, 10.0F, 10.0F});
             node.clip = NodeClip{.rect = {0.0F, 0.0F, 5.0F, 5.0F}};
             return node;
         }()},
        {"path clip",
         [] {
             auto node = box("node", {0.0F, 0.0F, 10.0F, 10.0F});
             node.clip = NodeClip{.path = triangle()};
             return node;
         }()},
        {"mask",
         [] {
             auto node = box("node", {0.0F, 0.0F, 10.0F, 10.0F});
             node.mask = MaskMode::alpha;
             return node;
         }()},
        {"luminance mask",
         [] {
             auto node = box("node", {0.0F, 0.0F, 10.0F, 10.0F});
             node.mask = MaskMode::luminance;
             return node;
         }()},
        {"isolation",
         [] {
             auto node = box("node", {0.0F, 0.0F, 10.0F, 10.0F});
             node.isolate = true;
             return node;
         }()},
    };
    for (const auto& [name, node] : variants) {
        auto parent = box("parent", {0.0F, 0.0F, 20.0F, 20.0F});
        parent.children = {box("sibling", {0.0F, 0.0F, 1.0F, 1.0F}), node};
        auto scene = scene_with({parent});
        expect(!uses_version_5_fields(parent) && uses_version_5_fields(node),
               name + " is a version 5 field");
        for (std::uint32_t version = 1U; version < 5U; ++version) {
            scene.scene_version = version;
            const auto* found = find_version_5_fields(scene.root);
            expect(!scene_fields_match_version(scene) && make_paint_commands(scene).empty() &&
                       found != nullptr && found->id == "node",
                   "a version " + std::to_string(version) + " scene with a " + name +
                       " is refused");
        }
        scene.scene_version = 4U;
        const auto loss = scene_loss_diagnostics(scene);
        const std::string property = node.clip ? "clip" : node.mask ? "mask" : "isolate";
        expect(loss.size() == 1U && loss[0].code == kSceneVersionMismatchCode &&
                   loss[0].severity == LoweringDiagnostic::Severity::error &&
                   loss[0].node_id == "node" && loss[0].property == property &&
                   loss[0].message ==
                       "scene version 4 cannot carry the clip, mask, or isolation at node",
               "a version 4 scene with a " + name + " reports a version-mismatch loss");
        const auto acceptance = accept_scene(scene);
        expect(!acceptance.accepted && acceptance.reason == loss[0].message &&
                   acceptance.diagnostics.size() == 1U,
               "accept_scene refuses a version 4 scene with a " + name);
        scene.scene_version = 5U;
        expect(scene_fields_match_version(scene) && !make_paint_commands(scene).empty() &&
                   scene_loss_diagnostics(scene).empty() && accept_scene(scene).accepted,
               "a version 5 scene with a " + name + " draws");
    }
    auto plain = scene_with({box("plain", {0.0F, 0.0F, 1.0F, 1.0F})});
    plain.scene_version = 4U;
    expect(scene_fields_match_version(plain) && make_paint_commands(plain).size() == 1U,
           "a version 4 scene without grouping fields still draws");

    LinearGradient gradient;
    gradient.stops = {{.position = 0.0F, .color = Color::hex(0xFF0000)},
                      {.position = 1.0F, .color = Color::hex(0x0000FF)}};
    auto gradient_fill = box("gradient-fill", {0.0F, 0.0F, 10.0F, 10.0F});
    gradient_fill.fill_gradients = {gradient};
    auto fill_scene = scene_with({gradient_fill});
    for (std::uint32_t version = 1U; version < 8U; ++version) {
        fill_scene.scene_version = version;
        const auto loss = scene_loss_diagnostics(fill_scene);
        expect(!scene_fields_match_version(fill_scene) && !accept_scene(fill_scene).accepted &&
                   loss.size() == 1U && loss[0].code == kSceneVersionMismatchCode,
               "a version " + std::to_string(version) +
                   " scene with a present gradient is refused");
    }
    fill_scene.scene_version = 8U;
    expect(scene_fields_match_version(fill_scene) && accept_scene(fill_scene).accepted,
           "a version 8 scene with a present gradient is accepted");

    auto stroke_node = box("gradient-stroke", {0.0F, 0.0F, 10.0F, 10.0F});
    stroke_node.kind = SceneNode::Kind::path;
    auto stroke_path = std::make_shared<PathGeometry>(*triangle());
    stroke_path->stroke.width = 2.0F;
    stroke_path->stroke.gradient = gradient;
    stroke_node.path = stroke_path;
    auto stroke_scene = scene_with({stroke_node});
    stroke_scene.scene_version = 7U;
    expect(!scene_fields_match_version(stroke_scene) &&
               scene_loss_diagnostics(stroke_scene)[0].code == kSceneVersionMismatchCode &&
               !accept_scene(stroke_scene).accepted,
           "a version 7 scene with a stroke gradient is refused");
    stroke_scene.scene_version = 8U;
    expect(scene_fields_match_version(stroke_scene) && accept_scene(stroke_scene).accepted,
           "a version 8 scene with a stroke gradient is accepted");
}

PixelRect damage_bounds(const Scene& before, const Scene& after) {
    return compute_scene_damage(before, after, 1.0F).bounds();
}

// A clip bounds its subtree's damage; changing a clip, a mask mode or
// isolation damages the subtrees that change.
void damage() {
    auto window = box("window", {10.0F, 10.0F, 20.0F, 20.0F});
    window.clip = NodeClip{.rect = {0.0F, 0.0F, 20.0F, 20.0F}};
    window.children.push_back(box("window/slider", {0.0F, 0.0F, 60.0F, 5.0F}));
    const auto before = scene_with({window});
    auto moved = before;
    moved.root.children[0].children[0].bounds.y = 10.0F;
    const auto clipped = damage_bounds(before, moved);
    expect(clipped.left >= 9 && clipped.right <= 31 && clipped.top >= 9 && clipped.bottom <= 31,
           "a clip bounds the damage of a moved descendant");
    expect(!compute_scene_damage(before, moved, 1.0F).empty(), "the move still damages");

    auto turned = before;
    turned.root.children[0].transform = Affine2D{.a = 1.0F, .d = 1.0F, .tx = 0.0F};
    turned.root.children[0].transform->b = 0.5F;
    auto turned_moved = turned;
    turned_moved.root.children[0].children[0].bounds.y = 10.0F;
    // A shear about the window's origin (10, 10) maps its clip to the frame
    // box x 10..30, y 10..40; the slider still reaches no further right.
    const auto sheared = damage_bounds(turned, turned_moved);
    expect(sheared.right <= 31 && sheared.bottom <= 41 && sheared.bottom > 31,
           "a transformed node's clip bounds damage through its transform");

    auto radii = before;
    radii.root.children[0].clip->radii = uniform_corner_radii(4.0F);
    expect(grouping_differs(before.root.children[0], radii.root.children[0]) &&
               !compute_scene_damage(before, radii, 1.0F).empty(),
           "changing only a clip's radii damages the clipped subtree");
    auto shaped = before;
    shaped.root.children[0].clip->path = triangle();
    auto reshaped = shaped;
    reshaped.root.children[0].clip->path = triangle();
    expect(!grouping_differs(shaped.root.children[0], reshaped.root.children[0]) &&
               compute_scene_damage(shaped, reshaped, 1.0F).empty(),
           "an equal clip path in a new allocation damages nothing");
    expect(!compute_scene_damage(before, shaped, 1.0F).empty(),
           "replacing a clip rectangle with a path damages");

    // A subtree clip bounds the descendants but not the node's own shadow.
    auto card = box("card", {10.0F, 10.0F, 20.0F, 20.0F});
    card.box_shadows = {{.offset_x = 40.0F, .color = Color::hex(0x000000)}};
    card.clip = NodeClip{.rect = {0.0F, 0.0F, 20.0F, 20.0F}, .clips_own_paint = false};
    card.children.push_back(box("card/slider", {0.0F, 0.0F, 60.0F, 5.0F}));
    const auto card_before = scene_with({card});
    auto recoloured = card_before;
    recoloured.root.children[0].fill = Color::hex(0x00FF00);
    expect(damage_bounds(card_before, recoloured).right >= 70,
           "a subtree clip does not bound the node's own shadow damage");
    auto slid = card_before;
    slid.root.children[0].children[0].bounds.y = 10.0F;
    expect(damage_bounds(card_before, slid).right <= 31,
           "a subtree clip bounds its descendants' damage");

    // Under a sheared ancestor the clip maps to the frame box x 10..30, y 15..45.
    SceneNode turner{.id = "turner", .kind = SceneNode::Kind::group,
                     .transform = Affine2D{.b = 0.5F}};
    turner.children.push_back(before.root.children[0]);
    const auto under = scene_with({turner});
    auto under_moved = under;
    under_moved.root.children[0].children[0].children[0].bounds.y = 10.0F;
    const auto under_damage = damage_bounds(under, under_moved);
    expect(under_damage.right <= 31 && under_damage.bottom <= 46 && under_damage.bottom > 31,
           "a clip under a transformed ancestor bounds damage through the composed transform");

    auto isolated = before;
    isolated.root.children[0].isolate = true;
    expect(grouping_differs(before.root.children[0], isolated.root.children[0]) &&
               !compute_scene_damage(before, isolated, 1.0F).empty(),
           "toggling isolation damages the subtree");

    // The sibling sits far from the mask, so only damaging the parent's whole
    // subtree reaches it.
    auto parent = box("parent", {0.0F, 0.0F, 0.0F, 0.0F});
    parent.kind = SceneNode::Kind::group;
    parent.children.push_back(box("parent/far", {70.0F, 70.0F, 10.0F, 10.0F}));
    parent.children.push_back(box("parent/mask", {0.0F, 0.0F, 10.0F, 10.0F}));
    const auto unmasked = scene_with({parent});
    auto masked = unmasked;
    masked.root.children[0].children[1].mask = MaskMode::alpha;
    expect(damage_bounds(unmasked, masked).right >= 80,
           "turning a child into a mask damages the siblings it now hides");
    expect(damage_bounds(masked, unmasked).right >= 80,
           "turning a mask back into paint damages the siblings it revealed");
    auto luminance = masked;
    luminance.root.children[0].children[1].mask = MaskMode::luminance;
    expect(damage_bounds(masked, luminance).right >= 80,
           "changing a mask's mode damages the parent's subtree");
    auto added = unmasked;
    added.root.children[0].children.push_back(box("parent/new-mask", {0.0F, 0.0F, 5.0F, 5.0F}));
    added.root.children[0].children.back().mask = MaskMode::alpha;
    expect(damage_bounds(unmasked, added).right >= 80,
           "adding a mask damages the siblings it hides");
    expect(damage_bounds(added, unmasked).right >= 80,
           "removing a mask damages the siblings it revealed");
    auto faded_mask = masked;
    faded_mask.root.children[0].children[1].opacity = 0.0F;
    expect(damage_bounds(masked, faded_mask).right >= 80,
           "fading a mask out damages every sibling it now hides");
    auto moved_mask = masked;
    moved_mask.root.children[0].children[1].transform = Affine2D{.tx = 2.0F};
    expect(damage_bounds(masked, moved_mask).right >= 80,
           "transforming a mask damages the parent's subtree");
    auto plain_added = unmasked;
    plain_added.root.children[0].children.push_back(
        box("parent/new", {0.0F, 0.0F, 5.0F, 5.0F}));
    expect(damage_bounds(unmasked, plain_added).right <= 6,
           "adding ordinary paint damages only its own area");
}

}  // namespace

int main() {
    clip_commands();
    subtree_clip_commands();
    isolation_commands();
    mask_commands();
    clip_validity();
    versioning();
    damage();
    if (failures != 0) {
        std::cerr << failures << " grouping check(s) failed\n";
        return 1;
    }
    std::cout << "grouping checks passed\n";
    return 0;
}
