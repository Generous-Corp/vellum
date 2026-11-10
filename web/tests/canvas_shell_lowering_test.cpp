#include "canvas_shell_lowering.hpp"

#include <algorithm>
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

    scene.root.children.push_back({
        .id = "faded-path-group",
        .kind = SceneNode::Kind::group,
        .children = {box("faded-path-group/box"),
                     {.id = "faded-path-group/outline", .kind = SceneNode::Kind::path,
                      .path = outline}},
        .opacity = 0.5F,
    });
    const auto layered = vellum::graphics::make_paint_commands(scene);
    using Kind = vellum::graphics::PaintCommand::Kind;
    const auto wrapped_path = std::find_if(layered.begin(), layered.end(), [](const auto& command) {
        return command.node_id == "faded-path-group/outline";
    });
    if (layered.empty() || wrapped_path == layered.end() || wrapped_path->kind != Kind::path ||
        (wrapped_path - 2)->kind != Kind::begin_layer ||
        !vellum::web::canvas_shell_loses(*wrapped_path) ||
        vellum::web::canvas_shell_loss_count(layered) != 7) {
        std::cerr << "a path inside a faded layer was not counted with its layer: "
                  << vellum::web::canvas_shell_loss_count(layered) << '\n';
        return 1;
    }

    // A clip counts once; a mask counts once and its commands are skipped,
    // so the shell never draws mask coverage as visible paint. The masked
    // group's layer is flattened and counts once, as any layer does.
    vellum::graphics::Scene grouped;
    grouped.root.id = "root";
    auto clipped = box("clipped");
    clipped.clip = vellum::graphics::NodeClip{.rect = {0.0F, 0.0F, 2.0F, 2.0F}};
    grouped.root.children.push_back(clipped);
    SceneNode masked{.id = "masked", .kind = SceneNode::Kind::group};
    masked.children.push_back(box("masked/content"));
    auto mask = box("masked/mask");
    mask.mask = vellum::graphics::MaskMode::luminance;
    mask.children.push_back(box("masked/mask/inner"));
    masked.children.push_back(mask);
    grouped.root.children.push_back(masked);
    const auto grouped_commands = vellum::graphics::make_paint_commands(grouped);
    const auto skipped = vellum::web::canvas_shell_masked(grouped_commands);
    std::vector<std::string> drawn;
    for (std::size_t index = 0; index < grouped_commands.size(); ++index) {
        if (!skipped[index] && vellum::graphics::draws_content(grouped_commands[index].kind)) {
            drawn.push_back(grouped_commands[index].node_id);
        }
    }
    if (drawn != std::vector<std::string>{"clipped", "masked/content"} ||
        vellum::web::canvas_shell_loss_count(grouped_commands) != 3) {
        std::cerr << "clips and masks were not lowered as declared: "
                  << vellum::web::canvas_shell_loss_count(grouped_commands) << '\n';
        return 1;
    }
    return 0;
}
