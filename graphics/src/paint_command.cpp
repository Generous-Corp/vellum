#include <vellum/graphics/paint_command.hpp>

#include <algorithm>
#include <cmath>

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
            .fill_gradients = node.fill_gradients,
            .box_shadows = node.box_shadows,
            .corner_radius = node.corner_radius,
        });
    } else if (node.kind == SceneNode::Kind::text &&
               (!node.text.empty() || !node.text_runs.empty())) {
        if (!node.fill_gradients.empty() || !node.box_shadows.empty()) {
            output.push_back({
                .kind = PaintCommand::Kind::rectangle,
                .node_id = node.id + "/background",
                .bounds = absolute,
                .fill_gradients = node.fill_gradients,
                .box_shadows = node.box_shadows,
                .corner_radius = node.corner_radius,
            });
        }
        output.push_back({
            .kind = PaintCommand::Kind::text,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .text = node.text,
            .font_size = std::max(1.0F, node.font_size),
            .font_family = node.font_family,
            .font_weight = std::clamp(node.font_weight, 100, 900),
            .letter_spacing = node.letter_spacing,
            .underline = node.underline,
            .strikethrough = node.strikethrough,
            .text_runs = node.text_runs,
        });
    }
    for (const auto& child : node.children) {
        append_node(child, absolute.x, absolute.y, output);
    }
}

}  // namespace

ResolvedLinearGradient resolve_linear_gradient(
    const Rect& bounds, const LinearGradient& gradient) {
    constexpr float kPi = 3.14159265358979323846F;
    const float radians = gradient.angle_degrees * kPi / 180.0F;
    const float direction_x = std::sin(radians);
    const float direction_y = -std::cos(radians);
    const float half_length = 0.5F * (
        std::abs(bounds.width * direction_x) +
        std::abs(bounds.height * direction_y));
    const float center_x = bounds.x + bounds.width * 0.5F;
    const float center_y = bounds.y + bounds.height * 0.5F;

    ResolvedLinearGradient resolved{
        .start_x = center_x - direction_x * half_length,
        .start_y = center_y - direction_y * half_length,
        .end_x = center_x + direction_x * half_length,
        .end_y = center_y + direction_y * half_length,
        .repeating = gradient.repeating,
        .repeat_length = std::max(0.0F, gradient.repeat_length),
        .stops = gradient.stops,
    };
    for (auto& stop : resolved.stops) {
        stop.position = std::clamp(stop.position, 0.0F, 1.0F);
    }
    std::stable_sort(resolved.stops.begin(), resolved.stops.end(),
        [](const GradientStop& left, const GradientStop& right) {
            return left.position < right.position;
        });
    return resolved;
}

std::vector<PaintCommand> make_paint_commands(const Scene& scene) {
    std::vector<PaintCommand> output;
    append_node(scene.root, 0.0F, 0.0F, output);
    return output;
}

}  // namespace vellum::graphics
