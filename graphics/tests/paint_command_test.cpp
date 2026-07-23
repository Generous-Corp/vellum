#include <vellum/graphics/paint_command.hpp>

#include <cmath>

namespace {

bool near(float left, float right) {
    return std::abs(left - right) < 0.0001F;
}

}  // namespace

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
            .text = "Shared core",
            .font_size = 16.0F,
        }},
    });

    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 2 || commands[0].node_id != "card" ||
        commands[1].node_id != "label") {
        return 1;
    }
    if (!near(commands[0].bounds.x, 15.0F) ||
        !near(commands[0].bounds.y, 27.0F) ||
        !near(commands[1].bounds.x, 18.0F) ||
        !near(commands[1].bounds.y, 31.0F) ||
        commands[1].text != "Shared core") {
        return 1;
    }
    return 0;
}
