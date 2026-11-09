#include "canvas_shell_lowering.hpp"

#include <iostream>
#include <memory>
#include <string>
#include <utility>

int main() {
    using vellum::graphics::Affine2D;
    using vellum::graphics::CornerRadii;
    using vellum::graphics::SceneNode;
    const auto box = [](std::string id) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .corner_radius = 2.0F,
        };
    };
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back(box("plain"));
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .text = "Hi",
    });
    if (vellum::web::canvas_shell_loss_count(
            vellum::graphics::make_paint_commands(scene)) != 0) {
        std::cerr << "a plain scene reported a loss\n";
        return 1;
    }

    auto turned = box("turned");
    turned.transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    auto corners = box("corners");
    corners.corner_radii = CornerRadii{1.0F, 0.0F, 0.0F, 0.0F};
    auto faded_single = box("faded-single");
    faded_single.opacity = 0.5F;
    scene.root.children.push_back(turned);
    scene.root.children.push_back(corners);
    scene.root.children.push_back(faded_single);
    scene.root.children.push_back({
        .id = "group",
        .kind = SceneNode::Kind::group,
        .children = {box("group/a"), box("group/b")},
        .opacity = 0.5F,
    });
    auto mesh = std::make_shared<vellum::graphics::TriangleMesh>();
    mesh->vertices = {{}, {.x = 1.0F}, {.y = 1.0F}};
    mesh->indices = {0, 1, 2};
    scene.root.children.push_back({
        .id = "mesh", .kind = SceneNode::Kind::mesh, .mesh = mesh,
    });
    scene.root.children.push_back({
        .id = "shaded",
        .kind = SceneNode::Kind::rectangle,
        .box_shadows = {{.blur_radius = 4.0F}},
    });
    auto outline = std::make_shared<vellum::graphics::PathGeometry>();
    outline->verbs = {vellum::graphics::PathVerb::move, vellum::graphics::PathVerb::line};
    outline->values = {0.0F, 0.0F, 4.0F, 4.0F};
    scene.root.children.push_back({
        .id = "outline", .kind = SceneNode::Kind::path, .path = outline,
    });
    const auto losses = vellum::web::canvas_shell_loss_count(
        vellum::graphics::make_paint_commands(scene));
    if (losses != 5) {
        std::cerr << "expected 5 losses, got " << losses << '\n';
        return 1;
    }
    return 0;
}
