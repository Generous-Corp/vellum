#include <vellum/graphics/vector_asset.hpp>

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>

#include <algorithm>
#include <cmath>
#include <vector>

namespace vellum::graphics {
namespace {

bool set_reason(std::string* reason, std::string message) {
    if (reason != nullptr) *reason = std::move(message);
    return false;
}

bool finite_color(const Color& color) noexcept {
    return std::isfinite(color.red) && std::isfinite(color.green) && std::isfinite(color.blue) &&
           std::isfinite(color.alpha);
}

bool invertible(const Affine2D& transform) noexcept {
    const float determinant = transform.a * transform.d - transform.b * transform.c;
    return std::isfinite(determinant) && determinant != 0.0F;
}

bool paint_is_valid(const VectorPaint& paint, const std::string& where, std::string* reason) {
    if (!finite_color(paint.color)) return set_reason(reason, where + " colour is not finite");
    if (!paint.gradient) return true;
    const auto& gradient = *paint.gradient;
    if (paint.color.alpha != 0.0F) {
        return set_reason(reason, where + " sets both a colour and a gradient");
    }
    if (gradient.stops.size() < 2U || gradient.stops.size() > 64U) {
        return set_reason(reason, where + " gradient needs between two and 64 stops");
    }
    float previous = 0.0F;
    for (const auto& stop : gradient.stops) {
        if (!std::isfinite(stop.position) || stop.position < previous || stop.position > 1.0F ||
            !finite_color(stop.color)) {
            return set_reason(reason, where +
                " gradient stops need finite colours at non-decreasing positions within [0, 1]");
        }
        previous = stop.position;
    }
    if (gradient.repeat_length != 0.0F) {
        return set_reason(reason, where + " gradient cannot set a repeat length");
    }
    if (auto why = gradient_inexpressible_reason(gradient); !why.empty()) {
        return set_reason(reason, where + " gradient: " + why);
    }
    const float geometry[]{gradient.angle_degrees, gradient.center_x, gradient.center_y,
                           gradient.focal_x, gradient.focal_y};
    if (!std::all_of(std::begin(geometry), std::end(geometry),
                     [](float value) { return std::isfinite(value); })) {
        return set_reason(reason, where + " gradient geometry is not finite");
    }
    if (!is_finite(paint.gradient_transform) || !invertible(paint.gradient_transform)) {
        return set_reason(reason, where + " gradient transform is not finite and invertible");
    }
    return true;
}

Rect bounds_union(const std::optional<Rect>& left, const Rect& right) {
    if (!left) return right;
    const float x = std::min(left->x, right.x);
    const float y = std::min(left->y, right.y);
    return {x, y, std::max(left->x + left->width, right.x + right.width) - x,
            std::max(left->y + left->height, right.y + right.height) - y};
}

}  // namespace

bool vector_op_fills(const VectorOp& op) noexcept {
    return op.kind == VectorOp::Kind::path && op.path && !op.path->verbs.empty() &&
           (op.fill.gradient.has_value() || op.fill.color.alpha > 0.0F);
}

bool vector_op_strokes(const VectorOp& op) noexcept {
    return op.kind == VectorOp::Kind::path && op.path && !op.path->verbs.empty() &&
           op.path->stroke.width > 0.0F &&
           (op.stroke.gradient.has_value() || op.stroke.color.alpha > 0.0F);
}

bool vector_asset_is_valid(const VectorAsset& asset, std::string* reason) {
    if (!is_content_hash(asset.content_hash)) {
        return set_reason(reason, "the content hash is not sha256: and 64 lowercase hex digits");
    }
    const auto& box = asset.view_box;
    if (!std::isfinite(box.x) || !std::isfinite(box.y) || !std::isfinite(box.width) ||
        !std::isfinite(box.height) || !(box.width > 0.0F) || !(box.height > 0.0F)) {
        return set_reason(reason, "the view box needs a finite origin and a positive size");
    }
    if (asset.ops.size() > kMaxVectorOps) {
        return set_reason(reason, "the asset has more than " + std::to_string(kMaxVectorOps) +
                                      " operations");
    }
    std::size_t depth = 0;
    for (std::size_t index = 0; index < asset.ops.size(); ++index) {
        const auto& op = asset.ops[index];
        const std::string where = "operation " + std::to_string(index);
        if (!is_finite(op.transform)) return set_reason(reason, where + " transform is not finite");
        switch (op.kind) {
        case VectorOp::Kind::push_group:
            if (++depth > kMaxVectorGroupDepth) {
                return set_reason(reason, where + " nests groups deeper than " +
                                              std::to_string(kMaxVectorGroupDepth));
            }
            if (!std::isfinite(op.opacity) || op.opacity < 0.0F || op.opacity > 1.0F) {
                return set_reason(reason, where + " opacity is not within [0, 1]");
            }
            if (op.clip) {
                std::string why;
                if (!path_is_valid(*op.clip, &why)) {
                    return set_reason(reason, where + " clip: " + why);
                }
            }
            if (op.path || op.fill.gradient || op.stroke.gradient ||
                op.fill.color.alpha != 0.0F || op.stroke.color.alpha != 0.0F) {
                return set_reason(reason, where + " is a group and cannot carry paint");
            }
            break;
        case VectorOp::Kind::pop_group:
            if (depth == 0U) return set_reason(reason, where + " closes a group that is not open");
            --depth;
            break;
        case VectorOp::Kind::path: {
            if (!op.path) return set_reason(reason, where + " has no geometry");
            std::string why;
            if (!path_is_valid(*op.path, &why)) return set_reason(reason, where + " path: " + why);
            if (op.path->stroke.color.alpha != 0.0F || op.path->stroke.gradient) {
                return set_reason(reason,
                    where + " geometry carries a stroke paint; use the operation's stroke");
            }
            if (!paint_is_valid(op.fill, where + " fill", reason) ||
                !paint_is_valid(op.stroke, where + " stroke", reason)) {
                return false;
            }
            if (op.clip) return set_reason(reason, where + " is a path and cannot clip");
            if (op.opacity != 1.0F) {
                return set_reason(reason, where + " is a path; opacity belongs to a group");
            }
            break;
        }
        default:
            return set_reason(reason, where + " has an unknown kind");
        }
    }
    if (depth != 0U) return set_reason(reason, "the asset leaves a group open");
    return true;
}

Affine2D vector_view_box_transform(const Rect& bounds, const Rect& view_box) noexcept {
    const float sx = bounds.width / view_box.width;
    const float sy = bounds.height / view_box.height;
    return {.a = sx, .d = sy, .tx = bounds.x - view_box.x * sx, .ty = bounds.y - view_box.y * sy};
}

std::optional<Rect> vector_asset_paint_box(const VectorAsset& asset) {
    std::vector<Affine2D> stack{Affine2D{}};
    std::optional<Rect> painted;
    for (const auto& op : asset.ops) {
        switch (op.kind) {
        case VectorOp::Kind::push_group:
            stack.push_back(concat(stack.back(), op.transform));
            break;
        case VectorOp::Kind::pop_group:
            if (stack.size() > 1U) stack.pop_back();
            break;
        case VectorOp::Kind::path: {
            const bool fills = vector_op_fills(op);
            const bool strokes = vector_op_strokes(op);
            if (!fills && !strokes) break;
            PathGeometry geometry = *op.path;
            geometry.stroke.color =
                strokes ? Color::rgba(0.0F, 0.0F, 0.0F, 1.0F) : Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
            const auto box = path_paint_box(geometry);
            if (!box) break;
            painted = bounds_union(painted,
                                   transformed_bounds(concat(stack.back(), op.transform), *box));
            break;
        }
        }
    }
    return painted;
}

std::optional<Rect> vector_paint_bounds(const VectorAsset& asset, const Rect& bounds) {
    const auto box = vector_asset_paint_box(asset);
    if (!box) return std::nullopt;
    return transformed_bounds(vector_view_box_transform(bounds, asset.view_box), *box);
}

VectorPaintCount vector_paint_count(const VectorAsset& asset) noexcept {
    VectorPaintCount count;
    for (const auto& op : asset.ops) {
        if (op.kind == VectorOp::Kind::push_group) count.grouped = true;
        if (vector_op_fills(op)) {
            ++count.paints;
            count.gradients = count.gradients || op.fill.gradient.has_value();
        }
        if (vector_op_strokes(op)) {
            ++count.paints;
            count.gradients = count.gradients || op.stroke.gradient.has_value();
        }
    }
    return count;
}

}  // namespace vellum::graphics
