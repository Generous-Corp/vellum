#include <vellum/graphics/paint_command.hpp>

#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

bool near(float left, float right) {
    return std::abs(left - right) < 0.0001F;
}


bool mesh_commands() {
    using vellum::graphics::MeshTexture;
    using vellum::graphics::MeshVertex;
    using vellum::graphics::TriangleMesh;
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {
        {.x = 0.0F, .y = 0.0F},
        {.x = 4.0F, .y = 0.0F, .u = 1.0F},
        {.x = 0.0F, .y = 4.0F, .v = 1.0F},
    };
    mesh->indices = {0, 1, 2};

    vellum::graphics::Scene scene;
    scene.root.bounds = {10.0F, 20.0F, 100.0F, 100.0F};
    scene.root.children.push_back({
        .id = "mesh",
        .kind = vellum::graphics::SceneNode::Kind::mesh,
        .bounds = {1.0F, 2.0F, 0.0F, 0.0F},
        .mesh = mesh,
    });
    // A mesh node without geometry paints nothing.
    scene.root.children.push_back({
        .id = "no-geometry",
        .kind = vellum::graphics::SceneNode::Kind::mesh,
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 1U ||
        commands[0].kind != vellum::graphics::PaintCommand::Kind::mesh ||
        commands[0].node_id != "mesh" || commands[0].mesh != mesh ||
        !near(commands[0].bounds.x, 11.0F) || !near(commands[0].bounds.y, 22.0F)) {
        return false;
    }

    std::string reason;
    if (!vellum::graphics::mesh_is_valid(*mesh, &reason) || !reason.empty()) return false;

    auto expect_invalid = [&](TriangleMesh candidate, const std::string& expected) {
        std::string why;
        return !vellum::graphics::mesh_is_valid(candidate, &why) &&
               why.find(expected) != std::string::npos;
    };
    TriangleMesh partial = *mesh;
    partial.indices.push_back(0);
    TriangleMesh out_of_range = *mesh;
    out_of_range.indices[2] = 3;
    TriangleMesh not_finite = *mesh;
    not_finite.vertices[1].u = std::numeric_limits<float>::quiet_NaN();
    TriangleMesh short_texture = *mesh;
    short_texture.texture = std::make_shared<MeshTexture>(
        MeshTexture{.width = 2, .height = 2, .premultiplied_rgba = std::vector<std::uint8_t>(15)});
    TriangleMesh empty_texture = *mesh;
    empty_texture.texture = std::make_shared<MeshTexture>();
    TriangleMesh bad_clip = *mesh;
    bad_clip.clip_enabled = true;
    bad_clip.clip.width = std::numeric_limits<float>::infinity();
    TriangleMesh good_texture = *mesh;
    good_texture.texture = std::make_shared<MeshTexture>(
        MeshTexture{.width = 2, .height = 1, .premultiplied_rgba = std::vector<std::uint8_t>(8)});
    return expect_invalid(partial, "multiple of three") &&
           expect_invalid(out_of_range, "past the end") &&
           expect_invalid(not_finite, "not finite") &&
           expect_invalid(short_texture, "does not match") &&
           expect_invalid(empty_texture, "no pixels") &&
           expect_invalid(bad_clip, "clip rectangle") &&
           vellum::graphics::mesh_is_valid(good_texture) &&
           vellum::graphics::mesh_is_valid(TriangleMesh{});
}

bool near_point(vellum::graphics::Point point, float x, float y) {
    return near(point.x, x) && near(point.y, y);
}

bool near_rect(const vellum::graphics::Rect& rect, float x, float y, float width, float height) {
    return near(rect.x, x) && near(rect.y, y) && near(rect.width, width) &&
           near(rect.height, height);
}

vellum::graphics::Affine2D quarter_turn() {
    return {.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
}

// Transforms pivot on each node's origin and compose down the tree, while
// `bounds` stays untransformed.
bool transform_geometry() {
    using vellum::graphics::Affine2D;
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "turned",
        .kind = SceneNode::Kind::group,
        .bounds = {10.0F, 20.0F, 40.0F, 40.0F},
        .children = {{
            .id = "bar",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {5.0F, 5.0F, 10.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFF0000),
        }},
        .transform = quarter_turn(),
    });
    scene.root.children.push_back({
        .id = "scaled",
        .kind = SceneNode::Kind::group,
        .bounds = {10.0F, 20.0F, 40.0F, 40.0F},
        .children = {{
            .id = "nudged",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {5.0F, 5.0F, 2.0F, 2.0F},
            .fill = vellum::graphics::Color::hex(0x00FF00),
            .transform = vellum::graphics::translation(3.0F, 0.0F),
        }},
        .transform = Affine2D{.a = 2.0F, .d = 2.0F},
    });
    scene.root.children.push_back({
        .id = "plain",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 2.0F, 2.0F},
        .transform = Affine2D{},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 3U || commands[0].node_id != "bar" ||
        commands[1].node_id != "nudged" || commands[2].node_id != "plain") {
        return false;
    }
    const auto& bar = commands[0];
    const auto& nudged = commands[1];
    if (!near_rect(bar.bounds, 15.0F, 25.0F, 10.0F, 4.0F) ||
        !near_point(vellum::graphics::apply(bar.transform, {15.0F, 25.0F}), 5.0F, 25.0F) ||
        !near_point(vellum::graphics::apply(bar.transform, {25.0F, 29.0F}), 1.0F, 35.0F) ||
        !near_rect(vellum::graphics::transformed_bounds(bar.transform, bar.bounds),
                   1.0F, 25.0F, 4.0F, 10.0F)) {
        return false;
    }
    if (!near_point(vellum::graphics::apply(nudged.transform, {15.0F, 25.0F}), 26.0F, 30.0F) ||
        !near_rect(vellum::graphics::transformed_bounds(nudged.transform, nudged.bounds),
                   26.0F, 30.0F, 4.0F, 4.0F) ||
        !vellum::graphics::is_identity(commands[2].transform) ||
        !near_rect(vellum::graphics::transformed_bounds(commands[2].transform,
                                                         commands[2].bounds),
                   1.0F, 1.0F, 2.0F, 2.0F)) {
        return false;
    }
    const auto composed = vellum::graphics::concat(
        vellum::graphics::translation(1.0F, 0.0F), Affine2D{.a = 2.0F, .d = 2.0F});
    if (!near_point(vellum::graphics::apply(composed, {1.0F, 1.0F}), 3.0F, 2.0F)) return false;

    auto broken = scene;
    broken.root.children[1].transform->c = std::numeric_limits<float>::infinity();
    const auto kept = vellum::graphics::make_paint_commands(broken);
    const auto* found = vellum::graphics::find_non_finite_compositing(broken.root);
    return kept.size() == 2U && kept[1].node_id == "plain" && found != nullptr &&
           found->id == "scaled" &&
           vellum::graphics::find_non_finite_compositing(scene.root) == nullptr;
}

// Group opacity folds into a lone command and otherwise opens a layer.
bool opacity_nesting() {
    using vellum::graphics::PaintCommand;
    using vellum::graphics::SceneNode;
    const auto box = [](std::string id, float opacity = 1.0F) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .opacity = opacity,
        };
    };
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "pair",
        .kind = SceneNode::Kind::group,
        .bounds = {2.0F, 3.0F, 10.0F, 10.0F},
        .children = {box("pair/a"), box("pair/b", 0.5F)},
        .transform = quarter_turn(),
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "single",
        .kind = SceneNode::Kind::group,
        .children = {box("single/a", 0.5F)},
        .opacity = 0.25F,
    });
    scene.root.children.push_back(box("clear", 0.0F));
    scene.root.children.back().children.push_back(box("clear/child"));
    scene.root.children.push_back(box("clamped", 4.0F));
    scene.root.children.push_back({
        .id = "empty",
        .kind = SceneNode::Kind::group,
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "caption",
        .kind = SceneNode::Kind::text,
        .box_shadows = {{.color = vellum::graphics::Color::hex(0x000000)}},
        .text = "Hi",
        .opacity = 0.5F,
    });

    const auto commands = vellum::graphics::make_paint_commands(scene);
    const std::vector<std::pair<PaintCommand::Kind, std::string>> expected{
        {PaintCommand::Kind::begin_layer, "pair"},
        {PaintCommand::Kind::rectangle, "pair/a"},
        {PaintCommand::Kind::rectangle, "pair/b"},
        {PaintCommand::Kind::end_layer, "pair"},
        {PaintCommand::Kind::rectangle, "single/a"},
        {PaintCommand::Kind::rectangle, "clamped"},
        {PaintCommand::Kind::begin_layer, "caption"},
        {PaintCommand::Kind::rectangle, "caption/background"},
        {PaintCommand::Kind::text, "caption"},
        {PaintCommand::Kind::end_layer, "caption"},
    };
    if (commands.size() != expected.size()) return false;
    for (std::size_t index = 0; index < expected.size(); ++index) {
        if (commands[index].kind != expected[index].first ||
            commands[index].node_id != expected[index].second) {
            return false;
        }
    }
    const std::vector<float> paint{0.5F, 1.0F, 0.5F, 1.0F, 0.125F, 1.0F, 0.5F, 1.0F, 1.0F, 1.0F};
    const std::vector<float> effective{
        0.5F, 0.5F, 0.25F, 1.0F, 0.125F, 1.0F, 0.5F, 0.5F, 0.5F, 1.0F};
    const auto flattened = vellum::graphics::effective_opacities(commands);
    for (std::size_t index = 0; index < commands.size(); ++index) {
        if (!near(commands[index].opacity, paint[index]) ||
            !near(flattened[index], effective[index])) {
            return false;
        }
    }
    if (!near_rect(commands[0].bounds, -2.0F, 3.0F, 4.0F, 4.0F) ||
        commands[0].layer_unbounded || !vellum::graphics::is_identity(commands[0].transform) ||
        !commands[6].layer_unbounded ||
        vellum::graphics::draws_content(PaintCommand::Kind::begin_layer) ||
        vellum::graphics::draws_content(PaintCommand::Kind::end_layer) ||
        !vellum::graphics::draws_content(PaintCommand::Kind::mesh)) {
        return false;
    }
    const std::vector<PaintCommand> nested{
        {.kind = PaintCommand::Kind::begin_layer, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::begin_layer, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::rectangle, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::rectangle},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::rectangle},
    };
    const auto deep = vellum::graphics::effective_opacities(nested);
    const std::vector<float> deep_expected{0.5F, 0.25F, 0.125F, 0.5F, 0.5F, 1.0F, 1.0F, 1.0F};
    for (std::size_t index = 0; index < deep.size(); ++index) {
        if (!near(deep[index], deep_expected[index])) return false;
    }
    auto broken = scene;
    broken.root.children[0].opacity = std::numeric_limits<float>::quiet_NaN();
    const auto kept = vellum::graphics::make_paint_commands(broken);
    return deep.size() == nested.size() && kept.size() == expected.size() - 4U &&
           kept[0].node_id == "single/a";
}

bool corner_radii() {
    using vellum::graphics::CornerRadii;
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.root.children.push_back({
        .id = "shorthand",
        .kind = SceneNode::Kind::rectangle,
        .corner_radius = 5.0F,
    });
    scene.root.children.push_back({
        .id = "corners",
        .kind = SceneNode::Kind::rectangle,
        .corner_radius = 5.0F,
        .corner_radii = CornerRadii{1.0F, 2.0F, -3.0F, 4.0F},
    });
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .fill_gradients = {{.stops = {{}, {.position = 1.0F}}}},
        .text = "x",
        .corner_radii = CornerRadii{0.0F, 6.0F, 0.0F, 0.0F},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 4U) return false;
    const auto& shorthand = commands[0].corner_radii;
    const auto& corners = commands[1].corner_radii;
    const auto& background = commands[2].corner_radii;
    if (!vellum::graphics::is_uniform(shorthand) || shorthand.top_left != 5.0F ||
        commands[0].corner_radius != 5.0F || vellum::graphics::is_uniform(corners) ||
        corners.top_left != 1.0F || corners.top_right != 2.0F ||
        corners.bottom_right != 0.0F || corners.bottom_left != 4.0F ||
        commands[2].node_id != "label/background" || background.top_right != 6.0F ||
        !vellum::graphics::is_rounded(background) ||
        vellum::graphics::is_rounded(CornerRadii{})) {
        return false;
    }
    scene.root.children[1].corner_radii->top_left = std::numeric_limits<float>::quiet_NaN();
    const auto* found = vellum::graphics::find_non_finite_compositing(scene.root);
    return found != nullptr && found->id == "corners";
}

// A version 1 scene that sets a version 2 field yields no commands at all.
bool legacy_scene_fields() {
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.scene_version = 1U;
    scene.root.children.push_back({
        .id = "plain",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 4.0F, 4.0F},
        .corner_radius = 2.0F,
        .transform = vellum::graphics::Affine2D{},
    });
    if (!vellum::graphics::scene_fields_match_version(scene) ||
        vellum::graphics::make_paint_commands(scene).size() != 1U) {
        return false;
    }
    const auto refused = [&](auto change) {
        auto candidate = scene;
        change(candidate.root.children[0]);
        const auto* found = vellum::graphics::find_version_2_fields(candidate.root);
        auto upgraded = candidate;
        upgraded.scene_version = 2U;
        return !vellum::graphics::scene_fields_match_version(candidate) &&
               vellum::graphics::make_paint_commands(candidate).empty() &&
               found != nullptr && found->id == "plain" &&
               vellum::graphics::scene_fields_match_version(upgraded);
    };
    return refused([](SceneNode& node) { node.transform = quarter_turn(); }) &&
           refused([](SceneNode& node) { node.opacity = 0.5F; }) &&
           refused([](SceneNode& node) { node.opacity = 2.0F; }) &&
           refused([](SceneNode& node) {
               node.opacity = std::numeric_limits<float>::quiet_NaN();
           }) &&
           refused([](SceneNode& node) {
               node.corner_radii = vellum::graphics::CornerRadii{2.0F, 2.0F, 2.0F, 2.0F};
           });
}

// Shadows grow the paint extent by offset, spread, and 1.5 blur radii, and a
// mesh's extent is its clipped vertex box.
bool paint_extents() {
    using vellum::graphics::PaintCommand;
    const PaintCommand shaded{
        .kind = PaintCommand::Kind::rectangle,
        .bounds = {10.0F, 10.0F, 20.0F, 10.0F},
        .box_shadows = {{.offset_x = 4.0F, .blur_radius = 2.0F, .spread_radius = 1.0F},
                        {.offset_y = -6.0F}},
    };
    const auto extent = vellum::graphics::paint_extent(shaded);
    auto mesh = std::make_shared<vellum::graphics::TriangleMesh>();
    mesh->vertices = {{.x = 1.0F, .y = 2.0F}, {.x = 9.0F, .y = 2.0F}, {.x = 1.0F, .y = 8.0F}};
    mesh->indices = {0, 1, 2};
    mesh->clip_enabled = true;
    mesh->clip = {0.0F, 0.0F, 5.0F, 50.0F};
    const auto mesh_extent = vellum::graphics::paint_extent(
        {.kind = PaintCommand::Kind::mesh, .bounds = {100.0F, 100.0F, 0.0F, 0.0F},
         .mesh = mesh});
    const auto text_extent = vellum::graphics::paint_extent(
        {.kind = PaintCommand::Kind::text, .bounds = {1.0F, 2.0F, 3.0F, 4.0F}});
    return extent.bounded && near_rect(extent.rect, 10.0F, 4.0F, 28.0F, 20.0F) &&
           mesh_extent.bounded && near_rect(mesh_extent.rect, 101.0F, 102.0F, 4.0F, 6.0F) &&
           !text_extent.bounded && near_rect(text_extent.rect, 1.0F, 2.0F, 3.0F, 4.0F);
}

}  // namespace

bool scene_versioning() {
    using vellum::graphics::SceneNode;
    static_assert(vellum::graphics::kSceneVersion == 2U);
    static_assert(vellum::graphics::kMinSupportedSceneVersion == 1U);
    static_assert(vellum::graphics::scene_version_is_supported(vellum::graphics::kSceneVersion));
    static_assert(vellum::graphics::scene_version_is_supported(1U));
    static_assert(!vellum::graphics::scene_version_is_supported(0U));
    static_assert(!vellum::graphics::scene_version_is_supported(3U));

    vellum::graphics::Scene scene;
    if (scene.scene_version != vellum::graphics::kSceneVersion || scene.scene_version != 2U) {
        return false;
    }
    scene.root.children.push_back({
        .id = "future",
        .kind = static_cast<SceneNode::Kind>(42),
        .bounds = {5.0F, 5.0F, 10.0F, 10.0F},
        .children = {{
            .id = "future/child",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
        }},
    });
    scene.root.children.push_back({
        .id = "known",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 4.0F, 4.0F},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 1U || commands[0].node_id != "known") return false;
    static_assert(!vellum::graphics::is_known_kind(static_cast<SceneNode::Kind>(42)));
    static_assert(vellum::graphics::is_known_kind(SceneNode::Kind::mesh));
    const auto* unknown = vellum::graphics::find_unknown_kind(scene.root);
    if (unknown == nullptr || unknown->id != "future" ||
        vellum::graphics::find_unknown_kind(scene.root.children[1]) != nullptr) {
        return false;
    }

    scene.scene_version = vellum::graphics::kSceneVersion + 1U;
    if (!vellum::graphics::make_paint_commands(scene).empty()) return false;
    return legacy_scene_fields() && paint_extents();
}

int main() {
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.bounds = {10.0F, 20.0F, 300.0F, 200.0F};
    scene.root.children.push_back({
        .id = "card",
        .kind = vellum::graphics::SceneNode::Kind::rectangle,
        .bounds = {5.0F, 7.0F, 120.0F, 80.0F},
        .fill = vellum::graphics::Color::hex(0x123456),
        .corner_radius = 9.0F,
        .children = {{
            .id = "label",
            .kind = vellum::graphics::SceneNode::Kind::text,
            .bounds = {3.0F, 4.0F, 90.0F, 20.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .fill_gradients = {{
                .angle_degrees = 90.0F,
                .stops = {
                    {.position = 0.0F, .color = vellum::graphics::Color::hex(0x000000)},
                    {.position = 1.0F, .color = vellum::graphics::Color::hex(0xFFFFFF)},
                },
            }},
            .box_shadows = {{
                .spread_radius = 2.0F,
                .color = vellum::graphics::Color::hex(0x000000),
            }},
            .text = "Shared core",
            .font_size = 16.0F,
        }},
    });

    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 3 || commands[0].node_id != "card" ||
        commands[1].node_id != "label/background" ||
        commands[1].kind != vellum::graphics::PaintCommand::Kind::rectangle ||
        commands[1].fill_gradients.size() != 1U ||
        commands[1].box_shadows.size() != 1U ||
        commands[2].node_id != "label") {
        return 1;
    }
    if (!near(commands[0].bounds.x, 15.0F) ||
        !near(commands[0].bounds.y, 27.0F) ||
        !near(commands[2].bounds.x, 18.0F) ||
        !near(commands[2].bounds.y, 31.0F) ||
        commands[2].text != "Shared core") {
        return 1;
    }

    const auto gradient = vellum::graphics::resolve_linear_gradient(
        {10.0F, 20.0F, 200.0F, 100.0F},
        {.angle_degrees = 90.0F,
         .repeating = true,
         .stops = {
             {.position = 1.2F, .color = vellum::graphics::Color::hex(0xFFFFFF)},
             {.position = -0.2F, .color = vellum::graphics::Color::hex(0x000000)},
         }});
    if (!near(gradient.start_x, 10.0F) || !near(gradient.start_y, 70.0F) ||
        !near(gradient.end_x, 210.0F) || !near(gradient.end_y, 70.0F) ||
        !gradient.repeating || gradient.stops.size() != 2U ||
        !near(gradient.stops[0].position, 0.0F) ||
        !near(gradient.stops[1].position, 1.0F)) {
        return 1;
    }

    const auto diagonal = vellum::graphics::resolve_linear_gradient(
        {0.0F, 0.0F, 200.0F, 100.0F}, {.angle_degrees = 135.0F});
    if (!near(diagonal.start_x, 25.0F) ||
        !near(diagonal.start_y, -25.0F) ||
        !near(diagonal.end_x, 175.0F) ||
        !near(diagonal.end_y, 125.0F)) {
        return 1;
    }
    return mesh_commands() && scene_versioning() && transform_geometry() &&
                   opacity_nesting() && corner_radii()
               ? 0
               : 1;
}
