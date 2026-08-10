#include <vellum/graphics/paint_command.hpp>

#include <cmath>
#include <string>

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
    return 0;
}
