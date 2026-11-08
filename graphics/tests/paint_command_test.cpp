#include <vellum/graphics/paint_command.hpp>

#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
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

}  // namespace

bool scene_versioning() {
    using vellum::graphics::SceneNode;
    static_assert(vellum::graphics::kSceneVersion == 1U);
    static_assert(vellum::graphics::scene_version_is_supported(vellum::graphics::kSceneVersion));
    static_assert(!vellum::graphics::scene_version_is_supported(0U));
    static_assert(!vellum::graphics::scene_version_is_supported(2U));

    vellum::graphics::Scene scene;
    if (scene.scene_version != vellum::graphics::kSceneVersion) return false;
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
    if (commands.size() != 1U || commands[0].node_id != "known" ||
        commands[0].scene_version != vellum::graphics::kSceneVersion) {
        return false;
    }

    scene.scene_version = vellum::graphics::kSceneVersion + 1U;
    return vellum::graphics::make_paint_commands(scene).empty();
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
    return mesh_commands() && scene_versioning() ? 0 : 1;
}
