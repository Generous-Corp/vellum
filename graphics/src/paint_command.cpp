#include <vellum/graphics/paint_command.hpp>

#include <vellum/graphics/image_asset.hpp>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace vellum::graphics {
namespace {

Rect union_of(const Rect& left, const Rect& right) {
    const float x = std::min(left.x, right.x);
    const float y = std::min(left.y, right.y);
    return {x, y, std::max(left.x + left.width, right.x + right.width) - x,
            std::max(left.y + left.height, right.y + right.height) - y};
}

void append_node(const ImageAssetIndex& images, const SceneNode& node, float parent_x,
                 float parent_y,
                 const Affine2D& parent_transform, std::vector<PaintCommand>& output) {
    if (!is_known_kind(node.kind) || !compositing_is_finite(node)) return;
    const float opacity = std::clamp(node.opacity, 0.0F, 1.0F);
    if (opacity <= 0.0F) return;
    const Rect absolute{
        parent_x + node.bounds.x,
        parent_y + node.bounds.y,
        node.bounds.width,
        node.bounds.height,
    };
    Affine2D transform = parent_transform;
    if (node.transform && !is_identity(*node.transform)) {
        transform = concat(parent_transform, concat(
            translation(absolute.x, absolute.y),
            concat(*node.transform, translation(-absolute.x, -absolute.y))));
    }
    const auto radii = resolved_corner_radii(node);
    const auto slot = output.size();
    if (opacity < 1.0F) {
        output.push_back({.kind = PaintCommand::Kind::begin_layer, .node_id = node.id});
    }
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
    } else if (node.kind == SceneNode::Kind::path && (!node.path || !node.path->verbs.empty())) {
        output.push_back({
            .kind = PaintCommand::Kind::path,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .fill_gradients = node.fill_gradients,
            .path = node.path,
            .transform = transform,
        });
    } else if (node.kind == SceneNode::Kind::image) {
        output.push_back({
            .kind = PaintCommand::Kind::image,
            .node_id = node.id,
            .bounds = absolute,
            .fill = node.fill,
            .corner_radius = node.corner_radius,
            .transform = transform,
            .corner_radii = radii,
            .image = node.image,
            .image_asset = node.image ? images.find(node.image->content_hash) : nullptr,
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
        append_node(images, child, absolute.x, absolute.y, transform, output);
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
    if (drawn <= 1U) {
        if (drawn == 1U) output[last_drawn].opacity *= opacity;
        output.erase(output.begin() + static_cast<std::ptrdiff_t>(slot));
        return;
    }
    auto& layer = output[slot];
    layer.opacity = opacity;
    bool bounded = true;
    bool any = false;
    for (auto index = first; index < output.size(); ++index) {
        const auto& member = output[index];
        Rect extent;
        if (member.kind == PaintCommand::Kind::begin_layer) {
            bounded = bounded && !member.layer_unbounded;
            extent = member.bounds;
        } else if (draws_content(member.kind)) {
            const auto local = paint_extent(member);
            bounded = bounded && local.bounded;
            extent = transformed_bounds(member.transform, local.rect);
        } else {
            continue;
        }
        layer.bounds = any ? union_of(layer.bounds, extent) : extent;
        any = true;
    }
    layer.layer_unbounded = !bounded;
    output.push_back({.kind = PaintCommand::Kind::end_layer, .node_id = node.id});
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

std::optional<Rect> path_gradient_box(const PaintCommand& command) {
    const auto has_area = [](const Rect& box) { return box.width > 0.0F && box.height > 0.0F; };
    if (has_area(command.bounds)) return command.bounds;
    if (!command.path) return std::nullopt;
    const auto control = path_control_bounds(*command.path);
    if (!control || !has_area(*control)) return std::nullopt;
    return Rect{command.bounds.x + control->x, command.bounds.y + control->y, control->width,
                control->height};
}

std::vector<PaintCommand> make_paint_commands(const Scene& scene) {
    std::vector<PaintCommand> output;
    if (!scene_version_is_supported(scene.scene_version)) return output;
    if (!scene_fields_match_version(scene)) return output;
    append_node(ImageAssetIndex(scene), scene.root, 0.0F, 0.0F, Affine2D{}, output);
    return output;
}

namespace {

void report_missing_images(const ImageAssetIndex& images, const SceneNode& node,
                           std::vector<LoweringDiagnostic>& output) {
    const auto asset = node.kind == SceneNode::Kind::image && node.image
        ? images.find(node.image->content_hash)
        : nullptr;
    // A unit box always has area, so an empty placement means the source
    // rectangle selects nothing of the image.
    if (asset &&
        !image_placement(*node.image, asset->width, asset->height, {0.0F, 0.0F, 1.0F, 1.0F})) {
        output.push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::lowered,
            .code = std::string(kImageAssetMissingCode),
            .node_id = node.id,
            .property = "image",
            .message = "image " + node.id +
                       " has a source rectangle outside its asset; only its fill was drawn",
        });
    }
    if (node.kind == SceneNode::Kind::image && node.image && !asset) {
        output.push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::lowered,
            .code = std::string(kImageAssetMissingCode),
            .node_id = node.id,
            .property = "image",
            .message = "image " + node.id + " names asset " + node.image->content_hash +
                       ", which the scene does not hold; a placeholder was drawn",
        });
    }
    for (const auto& child : node.children) report_missing_images(images, child, output);
}

}  // namespace

std::vector<LoweringDiagnostic> scene_loss_diagnostics(const Scene& scene) {
    std::vector<LoweringDiagnostic> output;
    const auto refuse = [&](std::string node_id, std::string message) {
        output.push_back({
            .severity = LoweringDiagnostic::Severity::error,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(kSceneVersionMismatchCode),
            .node_id = std::move(node_id),
            .property = "kind",
            .message = std::move(message),
        });
        return output;
    };
    const std::string version = std::to_string(scene.scene_version);
    if (!scene_version_is_supported(scene.scene_version)) {
        return refuse(scene.root.id, "scene version " + version + " is not supported");
    }
    if (const auto* node = find_version_mismatch(scene); node != nullptr) {
        if (!is_known_kind(node->kind)) {
            return refuse(node->id, "node " + node->id + " has a kind this build does not know");
        }
        if (node->kind == SceneNode::Kind::image) {
            return refuse(node->id, "scene version " + version +
                                        " cannot carry the image node " + node->id);
        }
        if (node->kind == SceneNode::Kind::path) {
            return refuse(node->id, "scene version " + version +
                                        " cannot carry the path node " + node->id);
        }
        output.push_back({
            .severity = LoweringDiagnostic::Severity::error,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(kSceneVersionMismatchCode),
            .node_id = node->id,
            .property = "transform",
            .message = "scene version " + version +
                       " cannot carry the transform, opacity, or corner radii at " + node->id,
        });
        return output;
    }
    report_missing_images(ImageAssetIndex(scene), scene.root, output);
    return output;
}

PaintExtent paint_extent(const PaintCommand& command) {
    const auto& box = command.bounds;
    if (command.kind == PaintCommand::Kind::text) return {box, false};
    if (command.kind == PaintCommand::Kind::mesh) {
        if (!command.mesh || command.mesh->vertices.empty()) return {{box.x, box.y, 0.0F, 0.0F}};
        const auto& vertices = command.mesh->vertices;
        float left = vertices.front().x;
        float right = left;
        float top = vertices.front().y;
        float bottom = top;
        for (const auto& vertex : vertices) {
            left = std::min(left, vertex.x);
            right = std::max(right, vertex.x);
            top = std::min(top, vertex.y);
            bottom = std::max(bottom, vertex.y);
        }
        if (command.mesh->clip_enabled) {
            const auto& clip = command.mesh->clip;
            left = std::max(left, clip.x);
            top = std::max(top, clip.y);
            right = std::min(right, clip.x + std::max(0.0F, clip.width));
            bottom = std::min(bottom, clip.y + std::max(0.0F, clip.height));
        }
        return {{box.x + left, box.y + top, std::max(0.0F, right - left),
                 std::max(0.0F, bottom - top)}};
    }
    if (command.kind == PaintCommand::Kind::path) {
        if (!command.path || !path_is_valid(*command.path)) return {box, false};
        const auto control = path_control_bounds(*command.path);
        if (!control) return {{box.x, box.y, 0.0F, 0.0F}};
        const float outset = path_stroke_outset(command.path->stroke);
        return {{box.x + control->x - outset, box.y + control->y - outset,
                 control->width + 2.0F * outset, control->height + 2.0F * outset}};
    }
    if (command.kind == PaintCommand::Kind::image) return {box};
    if (!draws_content(command.kind)) return {box};
    Rect extent = box;
    for (const auto& shadow : command.box_shadows) {
        const float outset = std::max(0.0F, shadow.spread_radius) +
                             1.5F * std::max(0.0F, shadow.blur_radius);
        extent = union_of(extent, {box.x + shadow.offset_x - outset,
                                   box.y + shadow.offset_y - outset,
                                   box.width + outset * 2.0F,
                                   box.height + outset * 2.0F});
    }
    return {extent};
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
