#include <vellum/graphics/paint_command.hpp>

#include <algorithm>

namespace vellum::graphics {
namespace {

void append_node(const SceneNode& node, float parent_x, float parent_y,
                 std::vector<PaintCommand>& output) {
    const Rect absolute{
        parent_x + node.bounds.x,
        parent_y + node.bounds.y,
        node.bounds.width,
        node.bounds.height,
    };
    if (node.kind == SceneNode::Kind::rectangle) {
        output.push_back({
            .kind = PaintCommand::Kind::rectangle,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .corner_radius = node.corner_radius,
        });
    } else if (node.kind == SceneNode::Kind::text && !node.text.empty()) {
        output.push_back({
            .kind = PaintCommand::Kind::text,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .text = node.text,
            .font_size = std::max(1.0F, node.font_size),
        });
    }
    for (const auto& child : node.children) {
        append_node(child, absolute.x, absolute.y, output);
    }
}

}  // namespace

std::vector<PaintCommand> make_paint_commands(const Scene& scene) {
    std::vector<PaintCommand> output;
    append_node(scene.root, 0.0F, 0.0F, output);
    return output;
}

}  // namespace vellum::graphics
