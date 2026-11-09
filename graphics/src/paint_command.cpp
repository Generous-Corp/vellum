#include <vellum/graphics/paint_command.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace vellum::graphics {
namespace {

void append_node(const SceneNode& node, float parent_x, float parent_y,
                 const Affine2D& parent_transform, bool compositing,
                 std::vector<PaintCommand>& output) {
    if (!is_known_kind(node.kind)) return;
    // A version 1 scene predates transform, opacity, and corner_radii, so they
    // keep their defaults there whatever they hold.
    if (compositing && !compositing_is_finite(node)) return;
    const float opacity = compositing ? std::clamp(node.opacity, 0.0F, 1.0F) : 1.0F;
    if (opacity <= 0.0F) return;
    const Rect absolute{
        parent_x + node.bounds.x,
        parent_y + node.bounds.y,
        node.bounds.width,
        node.bounds.height,
    };
    Affine2D transform = parent_transform;
    if (compositing && node.transform && !is_identity(*node.transform)) {
        transform = concat(parent_transform, concat(
            translation(absolute.x, absolute.y),
            concat(*node.transform, translation(-absolute.x, -absolute.y))));
    }
    const auto radii = compositing ? resolved_corner_radii(node)
                                   : resolved_corner_radii(SceneNode{
                                         .corner_radius = node.corner_radius});
    const auto first = output.size();
    if (node.kind == SceneNode::Kind::rectangle) {
        output.push_back({
            .kind = PaintCommand::Kind::rectangle,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .fill_gradients = node.fill_gradients,
            .box_shadows = node.box_shadows,
            .corner_radius = node.corner_radius,
            .transform = transform,
            .corner_radii = radii,
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
                .transform = transform,
                .corner_radii = radii,
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
            .transform = transform,
        });
    } else if (node.kind == SceneNode::Kind::mesh && node.mesh) {
        output.push_back({
            .kind = PaintCommand::Kind::mesh,
            .node_id = node.id,
            .bounds = absolute,
            .mesh = node.mesh,
            .transform = transform,
        });
    }
    for (const auto& child : node.children) {
        append_node(child, absolute.x, absolute.y, transform, compositing, output);
    }
    if (opacity >= 1.0F) return;

    std::size_t drawn = 0;
    std::size_t last_drawn = first;
    for (auto index = first; index < output.size(); ++index) {
        if (draws_content(output[index].kind)) {
            ++drawn;
            last_drawn = index;
        }
    }
    if (drawn == 0U) return;
    if (drawn == 1U) {
        // A single command cannot overlap itself, so fading its paint is the
        // same as fading the group. A layer needs two commands, so none is open.
        output[last_drawn].opacity *= opacity;
        return;
    }
    output.insert(output.begin() + static_cast<std::ptrdiff_t>(first), PaintCommand{
        .kind = PaintCommand::Kind::begin_layer,
        .node_id = node.id,
        .bounds = absolute,
        .transform = transform,
        .opacity = opacity,
    });
    output.push_back({
        .kind = PaintCommand::Kind::end_layer,
        .node_id = node.id,
        .bounds = absolute,
        .transform = transform,
    });
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

bool mesh_is_valid(const TriangleMesh& mesh, std::string* reason) {
    const auto fail = [reason](const char* message) {
        if (reason != nullptr) *reason = message;
        return false;
    };
    if (mesh.indices.size() % 3U != 0U) {
        return fail("the index count is not a multiple of three");
    }
    for (const auto index : mesh.indices) {
        if (index >= mesh.vertices.size()) {
            return fail("an index names a vertex past the end of the vertex list");
        }
    }
    for (const auto& vertex : mesh.vertices) {
        if (!std::isfinite(vertex.x) || !std::isfinite(vertex.y) ||
            !std::isfinite(vertex.u) || !std::isfinite(vertex.v)) {
            return fail("a vertex position or texture coordinate is not finite");
        }
    }
    if (mesh.texture) {
        const auto& texture = *mesh.texture;
        if (texture.width == 0U || texture.height == 0U) {
            return fail("the texture has no pixels");
        }
        const auto expected = static_cast<std::uint64_t>(texture.width) *
                              static_cast<std::uint64_t>(texture.height) * 4U;
        if (texture.premultiplied_rgba.size() != expected) {
            return fail("the texture's pixel buffer does not match its dimensions");
        }
    }
    if (mesh.clip_enabled &&
        (!std::isfinite(mesh.clip.x) || !std::isfinite(mesh.clip.y) ||
         !std::isfinite(mesh.clip.width) || !std::isfinite(mesh.clip.height))) {
        return fail("the clip rectangle is not finite");
    }
    return true;
}

std::vector<PaintCommand> make_paint_commands(const Scene& scene) {
    std::vector<PaintCommand> output;
    if (!scene_version_is_supported(scene.scene_version)) return output;
    append_node(scene.root, 0.0F, 0.0F, Affine2D{},
                scene_version_has_compositing(scene.scene_version), output);
    return output;
}

Rect transformed_bounds(const Affine2D& transform, const Rect& rect) noexcept {
    if (is_identity(transform)) return rect;
    const Point corners[]{
        apply(transform, {rect.x, rect.y}),
        apply(transform, {rect.x + rect.width, rect.y}),
        apply(transform, {rect.x + rect.width, rect.y + rect.height}),
        apply(transform, {rect.x, rect.y + rect.height}),
    };
    float left = corners[0].x;
    float right = corners[0].x;
    float top = corners[0].y;
    float bottom = corners[0].y;
    for (const auto& corner : corners) {
        left = std::min(left, corner.x);
        right = std::max(right, corner.x);
        top = std::min(top, corner.y);
        bottom = std::max(bottom, corner.y);
    }
    return {left, top, right - left, bottom - top};
}

std::vector<float> effective_opacities(const std::vector<PaintCommand>& commands) {
    std::vector<float> result;
    result.reserve(commands.size());
    std::vector<float> layers;
    float enclosing = 1.0F;
    for (const auto& command : commands) {
        if (command.kind == PaintCommand::Kind::end_layer) {
            // An unmatched end leaves the enclosing opacity unchanged.
            if (!layers.empty()) {
                enclosing = layers.back();
                layers.pop_back();
            }
            result.push_back(enclosing);
            continue;
        }
        result.push_back(enclosing * command.opacity);
        if (command.kind == PaintCommand::Kind::begin_layer) {
            layers.push_back(enclosing);
            enclosing *= command.opacity;
        }
    }
    return result;
}

}  // namespace vellum::graphics
