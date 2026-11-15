#include "canvas_shell_lowering.hpp"

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/vector_asset.hpp>
#include <vellum/runtime/kernel.hpp>

#include <emscripten.h>

#include <bit>
#include <cmath>
#include <initializer_list>
#include <optional>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace {

vellum::graphics::Scene scene;
vellum::web::CanvasShellLowering lowering;
const std::vector<vellum::graphics::PaintCommand>& commands = lowering.commands;
std::unique_ptr<vellum::runtime::Kernel> kernel;
// The vector node being built by `vellum_web_begin_vector`, if any.
std::optional<vellum::graphics::SceneNode> building_vector;
std::shared_ptr<vellum::graphics::VectorAsset> building_asset;
std::size_t building_depth = 0;

bool finite(std::initializer_list<float> values) {
    for (const float value : values) {
        if (!std::isfinite(value)) return false;
    }
    return true;
}

// Each builder call checks its own values and the asset's budgets, so a
// caller cannot grow an asset past what validation would accept.
bool room_for_op() {
    return building_asset && building_asset->ops.size() < vellum::graphics::kMaxVectorOps;
}

vellum::graphics::VectorPaint* last_paint(int stroke) {
    if (!building_asset || building_asset->ops.empty() ||
        building_asset->ops.back().kind != vellum::graphics::VectorOp::Kind::path) {
        return nullptr;
    }
    return stroke != 0 ? &building_asset->ops.back().stroke : &building_asset->ops.back().fill;
}

EM_JS(void, canvas_begin,
      (float width, float height, float red, float green, float blue, float alpha), {
    globalThis.VellumCanvasBackend.begin(width, height, red, green, blue, alpha);
});

EM_JS(void, canvas_rectangle,
      (float x, float y, float width, float height, float radius,
       float red, float green, float blue, float alpha), {
    globalThis.VellumCanvasBackend.rectangle(
        x, y, width, height, radius, red, green, blue, alpha);
});

EM_JS(void, canvas_text,
      (const char* text, float x, float baseline, float size,
       float red, float green, float blue, float alpha), {
    globalThis.VellumCanvasBackend.text(
        UTF8ToString(text), x, baseline, size, red, green, blue, alpha);
});

EM_JS(void, canvas_vector, (const char* plan), {
    globalThis.VellumCanvasBackend.vector(JSON.parse(UTF8ToString(plan)));
});

// A backend written before group compositing has no blendBegin or blendEnd;
// the shell then draws blended groups as normal and reports each one rather
// than calling a missing function.
EM_JS(int, canvas_has_group_compositing, (), {
    const backend = globalThis.VellumCanvasBackend;
    return backend && typeof backend.blendBegin === "function" &&
        typeof backend.blendEnd === "function" ? 1 : 0;
});

EM_JS(void, canvas_blend_begin, (const char* operation, float alpha), {
    globalThis.VellumCanvasBackend.blendBegin(UTF8ToString(operation), alpha);
});

EM_JS(void, canvas_blend_end, (), {
    globalThis.VellumCanvasBackend.blendEnd();
});

EM_JS(void, canvas_finish, (), {
    globalThis.VellumCanvasBackend.finish();
});

void hash_byte(std::uint32_t& hash, std::uint8_t byte) {
    hash ^= byte;
    hash *= 16777619U;
}

void hash_u32(std::uint32_t& hash, std::uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) {
        hash_byte(hash, static_cast<std::uint8_t>((value >> shift) & 0xFFU));
    }
}

void hash_string(std::uint32_t& hash, const std::string& value) {
    hash_u32(hash, static_cast<std::uint32_t>(value.size()));
    for (const unsigned char byte : value) hash_byte(hash, byte);
}

void hash_float(std::uint32_t& hash, float value) {
    hash_u32(hash, std::bit_cast<std::uint32_t>(value));
}

void hash_color(std::uint32_t& hash, const vellum::graphics::Color& color) {
    hash_float(hash, color.red);
    hash_float(hash, color.green);
    hash_float(hash, color.blue);
    hash_float(hash, color.alpha);
}

void hash_affine(std::uint32_t& hash, const vellum::graphics::Affine2D& m) {
    for (const float value : {m.a, m.b, m.c, m.d, m.tx, m.ty}) hash_float(hash, value);
}

void hash_geometry(std::uint32_t& hash, const vellum::graphics::PathGeometry* path) {
    hash_u32(hash, path != nullptr ? 1U : 0U);
    if (path == nullptr) return;
    for (const auto verb : path->verbs) hash_u32(hash, static_cast<std::uint32_t>(verb));
    for (const float value : path->values) hash_float(hash, value);
    hash_u32(hash, static_cast<std::uint32_t>(path->fill_rule));
    hash_float(hash, path->stroke.width);
    hash_u32(hash, static_cast<std::uint32_t>(path->stroke.join));
    hash_u32(hash, static_cast<std::uint32_t>(path->stroke.cap));
    hash_float(hash, path->stroke.miter_limit);
    for (const float value : path->stroke.dash_array) hash_float(hash, value);
    hash_float(hash, path->stroke.dash_offset);
}

void hash_paint(std::uint32_t& hash, const vellum::graphics::VectorPaint& paint) {
    hash_color(hash, paint.color);
    hash_u32(hash, paint.gradient ? 1U : 0U);
    if (!paint.gradient) return;
    const auto& g = *paint.gradient;
    hash_u32(hash, static_cast<std::uint32_t>(g.kind));
    hash_u32(hash, static_cast<std::uint32_t>(g.spread));
    hash_u32(hash, static_cast<std::uint32_t>(g.interpolation));
    hash_string(hash, g.color_space);
    for (const float value : {g.angle_degrees, g.center_x, g.center_y, g.radius_x, g.radius_y,
                              g.focal_x, g.focal_y, g.start_radius, g.end_radius}) {
        hash_float(hash, value);
    }
    for (const auto& stop : g.stops) {
        hash_float(hash, stop.position);
        hash_color(hash, stop.color);
    }
    hash_affine(hash, paint.gradient_transform);
}

// The asset's whole display list, so a replaced asset changes the digest
// even when its node keeps its id and box.
void hash_vector(std::uint32_t& hash, const vellum::graphics::VectorAsset& asset) {
    hash_string(hash, asset.content_hash);
    for (const float value : {asset.view_box.x, asset.view_box.y, asset.view_box.width,
                              asset.view_box.height}) {
        hash_float(hash, value);
    }
    for (const auto& op : asset.ops) {
        hash_u32(hash, static_cast<std::uint32_t>(op.kind));
        hash_affine(hash, op.transform);
        hash_geometry(hash, op.path.get());
        hash_paint(hash, op.fill);
        hash_paint(hash, op.stroke);
        hash_float(hash, op.opacity);
        hash_geometry(hash, op.clip.get());
    }
}

}  // namespace

extern "C" {

EMSCRIPTEN_KEEPALIVE int vellum_web_start() {
    if (kernel) return 0;
    auto candidate = std::make_unique<vellum::runtime::Kernel>(
        vellum::runtime::KernelConfiguration{.application_id = "dev.vellum.web-proof"});
    if (const auto status = candidate->start(); !status) return 0;
    kernel = std::move(candidate);
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_stop() {
    if (!kernel) return 0;
    const auto status = kernel->stop();
    kernel.reset();
    return status ? 1 : 0;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_begin_frame(
    float width, float height, float red, float green, float blue, float alpha) {
    if (!kernel || width <= 0.0F || height <= 0.0F) return 0;
    scene = {};
    scene.width = width;
    scene.height = height;
    scene.background = vellum::graphics::Color::rgba(red, green, blue, alpha);
    scene.root.id = "web-root";
    scene.root.kind = vellum::graphics::SceneNode::Kind::group;
    scene.root.bounds = {0.0F, 0.0F, width, height};
    lowering = {};
    building_vector.reset();
    building_asset.reset();
    building_depth = 0;
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_add_rectangle(
    const char* id, float x, float y, float width, float height, float radius,
    float red, float green, float blue, float alpha) {
    if (!kernel || id == nullptr || id[0] == '\0' || width < 0.0F || height < 0.0F) {
        return 0;
    }
    scene.root.children.push_back({
        .id = id,
        .kind = vellum::graphics::SceneNode::Kind::rectangle,
        .bounds = {x, y, width, height},
        .fill = vellum::graphics::Color::rgba(red, green, blue, alpha),
        .corner_radius = radius,
    });
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_add_text(
    const char* id, const char* text, float x, float y, float width, float height,
    float font_size, float red, float green, float blue, float alpha) {
    if (!kernel || id == nullptr || id[0] == '\0' || text == nullptr ||
        width < 0.0F || height < 0.0F) {
        return 0;
    }
    scene.root.children.push_back({
        .id = id,
        .kind = vellum::graphics::SceneNode::Kind::text,
        .bounds = {x, y, width, height},
        .fill = vellum::graphics::Color::rgba(red, green, blue, alpha),
        .text = text,
        .font_size = font_size,
    });
    return 1;
}

/// Starts a `vector` node with a compiled asset of the given view box and
/// content hash; operations follow, and `vellum_web_end_vector` adds it.
EMSCRIPTEN_KEEPALIVE int vellum_web_begin_vector(
    const char* id, float x, float y, float width, float height, float view_x, float view_y,
    float view_width, float view_height, const char* content_hash) {
    if (!kernel || id == nullptr || id[0] == '\0' || content_hash == nullptr ||
        building_vector || !finite({x, y, width, height, view_x, view_y, view_width, view_height}) ||
        width < 0.0F || height < 0.0F || !(view_width > 0.0F) || !(view_height > 0.0F) ||
        !vellum::graphics::is_content_hash(content_hash)) {
        return 0;
    }
    building_depth = 0;
    building_asset = std::make_shared<vellum::graphics::VectorAsset>();
    building_asset->content_hash = content_hash;
    building_asset->view_box = {view_x, view_y, view_width, view_height};
    building_vector = vellum::graphics::SceneNode{
        .id = id,
        .kind = vellum::graphics::SceneNode::Kind::vector,
        .bounds = {x, y, width, height},
    };
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_vector_group(
    float a, float b, float c, float d, float tx, float ty, float opacity, const char* clip,
    int clip_evenodd) {
    if (!room_for_op() || clip == nullptr || !finite({a, b, c, d, tx, ty, opacity}) ||
        opacity < 0.0F || opacity > 1.0F ||
        building_depth >= vellum::graphics::kMaxVectorGroupDepth) {
        return 0;
    }
    vellum::graphics::VectorOp op{
        .kind = vellum::graphics::VectorOp::Kind::push_group,
        .transform = {a, b, c, d, tx, ty},
        .opacity = opacity,
    };
    if (clip[0] != '\0') {
        vellum::graphics::PathGeometry geometry;
        if (!vellum::graphics::parse_svg_path_data(clip, geometry)) return 0;
        geometry.fill_rule = clip_evenodd != 0 ? vellum::graphics::FillRule::evenodd
                                               : vellum::graphics::FillRule::nonzero;
        op.clip = std::make_shared<const vellum::graphics::PathGeometry>(std::move(geometry));
    }
    building_asset->ops.push_back(std::move(op));
    ++building_depth;
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_vector_end_group() {
    if (!room_for_op() || building_depth == 0U) return 0;
    building_asset->ops.push_back({.kind = vellum::graphics::VectorOp::Kind::pop_group});
    --building_depth;
    return 1;
}

/// Adds a path operation. Join is 0 miter, 1 round, 2 bevel; cap is 0 butt,
/// 1 round, 2 square.
EMSCRIPTEN_KEEPALIVE int vellum_web_vector_path(
    const char* data, int evenodd, float a, float b, float c, float d, float tx, float ty,
    float fill_red, float fill_green, float fill_blue, float fill_alpha, float stroke_red,
    float stroke_green, float stroke_blue, float stroke_alpha, float stroke_width, int join,
    int cap, float miter_limit, float dash_offset) {
    if (!room_for_op() || data == nullptr || join < 0 || join > 2 || cap < 0 || cap > 2 ||
        !finite({a, b, c, d, tx, ty, fill_red, fill_green, fill_blue, fill_alpha, stroke_red,
                 stroke_green, stroke_blue, stroke_alpha, stroke_width, miter_limit,
                 dash_offset}) ||
        stroke_width < 0.0F || miter_limit < 1.0F) {
        return 0;
    }
    vellum::graphics::PathGeometry geometry;
    if (!vellum::graphics::parse_svg_path_data(data, geometry)) return 0;
    geometry.fill_rule = evenodd != 0 ? vellum::graphics::FillRule::evenodd
                                      : vellum::graphics::FillRule::nonzero;
    geometry.stroke.width = stroke_width;
    geometry.stroke.join = static_cast<vellum::graphics::StrokeJoin>(join);
    geometry.stroke.cap = static_cast<vellum::graphics::StrokeCap>(cap);
    geometry.stroke.miter_limit = miter_limit;
    geometry.stroke.dash_offset = dash_offset;
    building_asset->ops.push_back({
        .kind = vellum::graphics::VectorOp::Kind::path,
        .transform = {a, b, c, d, tx, ty},
        .path = std::make_shared<vellum::graphics::PathGeometry>(std::move(geometry)),
        .fill = {.color = vellum::graphics::Color::rgba(fill_red, fill_green, fill_blue,
                                                        fill_alpha)},
        .stroke = {.color = vellum::graphics::Color::rgba(stroke_red, stroke_green, stroke_blue,
                                                          stroke_alpha)},
    });
    return 1;
}

/// Appends one dash length to the last path operation's stroke.
EMSCRIPTEN_KEEPALIVE int vellum_web_vector_dash(float length) {
    if (!building_asset || building_asset->ops.empty() ||
        building_asset->ops.back().kind != vellum::graphics::VectorOp::Kind::path ||
        !std::isfinite(length) || length < 0.0F ||
        building_asset->ops.back().path->stroke.dash_array.size() >=
            vellum::graphics::kMaxPathDashes) {
        return 0;
    }
    auto geometry = std::make_shared<vellum::graphics::PathGeometry>(
        *building_asset->ops.back().path);
    geometry->stroke.dash_array.push_back(length);
    building_asset->ops.back().path = std::move(geometry);
    return 1;
}

/// Gives the last path operation's fill (`stroke` 0) or stroke (1) a
/// gradient. Kind is 0 linear, 1 radial, 2 two-circle radial, 3 conic;
/// spread is 0 pad, 1 repeat, 2 reflect; interpolation is 0 srgb, 1
/// linear-srgb, 2 oklab; colour space is 0 srgb, 1 linear-srgb, 2
/// display-p3. Stops follow with `vellum_web_vector_stop`.
EMSCRIPTEN_KEEPALIVE int vellum_web_vector_gradient(
    int stroke, int kind, int spread, int interpolation, int color_space, float angle,
    float center_x, float center_y, float radius_x, float radius_y, float focal_x,
    float focal_y, float start_radius, float end_radius, float a, float b, float c, float d,
    float tx, float ty) {
    auto* paint = last_paint(stroke);
    if (paint == nullptr || kind < 0 || kind > 3 || spread < 0 || spread > 2 ||
        interpolation < 0 || interpolation > 2 || color_space < 0 || color_space > 2 ||
        !finite({angle, center_x, center_y, radius_x, radius_y, focal_x, focal_y, start_radius,
                 end_radius, a, b, c, d, tx, ty})) {
        return 0;
    }
    auto& target = *paint;
    constexpr const char* kColorSpaces[] = {"srgb", "linear-srgb", "display-p3"};
    vellum::graphics::LinearGradient gradient;
    gradient.interpolation = static_cast<vellum::graphics::GradientInterpolation>(interpolation);
    gradient.color_space = kColorSpaces[color_space];
    gradient.kind = static_cast<vellum::graphics::GradientKind>(kind);
    gradient.spread = static_cast<vellum::graphics::GradientSpread>(spread);
    gradient.angle_degrees = angle;
    gradient.center_x = center_x;
    gradient.center_y = center_y;
    gradient.radius_x = radius_x;
    gradient.radius_y = radius_y;
    gradient.focal_x = focal_x;
    gradient.focal_y = focal_y;
    gradient.start_radius = start_radius;
    gradient.end_radius = end_radius;
    target.color = vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    target.gradient = std::move(gradient);
    target.gradient_transform = {a, b, c, d, tx, ty};
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_vector_stop(
    int stroke, float position, float red, float green, float blue, float alpha) {
    auto* paint = last_paint(stroke);
    if (paint == nullptr || !paint->gradient || paint->gradient->stops.size() >= 64U ||
        !finite({position, red, green, blue, alpha})) {
        return 0;
    }
    auto& target = *paint;
    target.gradient->stops.push_back(
        {.position = position, .color = vellum::graphics::Color::rgba(red, green, blue, alpha)});
    return 1;
}

/// Adds the vector node `vellum_web_begin_vector` started.
EMSCRIPTEN_KEEPALIVE int vellum_web_end_vector() {
    if (!building_vector || !building_asset || building_depth != 0U ||
        !vellum::graphics::vector_asset_is_valid(*building_asset)) {
        return 0;
    }
    building_vector->vector = std::move(building_asset);
    scene.root.children.push_back(std::move(*building_vector));
    building_vector.reset();
    building_asset.reset();
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_web_render() {
    if (!kernel || scene.width <= 0.0F || scene.height <= 0.0F) return 0;
    using Kind = vellum::graphics::PaintCommand::Kind;
    const vellum::web::CanvasShellCapabilities capabilities{
        .group_compositing = canvas_has_group_compositing() != 0,
    };
    // A refused scene draws nothing and keeps the previous canvas, as the
    // native renderer keeps its previous frame.
    lowering = vellum::web::lower_for_canvas_shell(scene, capabilities);
    if (!lowering.accepted) return 0;
    const auto plan = vellum::web::canvas_shell_paint_plan(commands, capabilities);
    const auto& opacities = plan.opacities;
    const auto masked = vellum::web::canvas_shell_masked(commands);
    // Whether each open layer was begun as a composited group, so its
    // end_layer composites it.
    std::vector<bool> open_groups;
    const auto background = scene.background;
    canvas_begin(scene.width, scene.height, background.red, background.green,
                 background.blue, background.alpha);
    for (std::size_t index = 0; index < commands.size(); ++index) {
        const auto& command = commands[index];
        if (masked[index]) continue;
        auto color = command.fill;
        color.alpha *= opacities[index];
        if (command.kind == Kind::begin_layer) {
            const bool group = !plan.group_operations[index].empty();
            open_groups.push_back(group);
            if (group) {
                const std::string operation(plan.group_operations[index]);
                canvas_blend_begin(operation.c_str(), opacities[index]);
            }
            continue;
        }
        if (command.kind == Kind::end_layer) {
            if (!open_groups.empty()) {
                if (open_groups.back()) canvas_blend_end();
                open_groups.pop_back();
            }
            continue;
        }
        switch (command.kind) {
        case Kind::rectangle:
            canvas_rectangle(
                command.bounds.x, command.bounds.y, command.bounds.width,
                command.bounds.height, command.corner_radius,
                color.red, color.green, color.blue, color.alpha);
            break;
        case Kind::text:
            canvas_text(
                command.text.c_str(), command.bounds.x,
                command.bounds.y + command.font_size, command.font_size,
                color.red, color.green, color.blue, color.alpha);
            break;
        case Kind::image: {
            // The shell has no image decoding; it marks the box and counts
            // the loss.
            auto placeholder = vellum::graphics::kMissingImagePlaceholder;
            placeholder.alpha *= opacities[index];
            canvas_rectangle(
                command.bounds.x, command.bounds.y, command.bounds.width,
                command.bounds.height, command.corner_radius,
                placeholder.red, placeholder.green, placeholder.blue, placeholder.alpha);
            break;
        }
        case Kind::vector: {
            const auto plan = vellum::web::lower_vector_for_canvas(command, opacities[index]);
            canvas_vector(plan.json.c_str());
            break;
        }
        case Kind::begin_layer:
        case Kind::end_layer:
        case Kind::mesh:
        case Kind::path:
        case Kind::begin_clip:
        case Kind::end_clip:
        case Kind::begin_mask:
        case Kind::end_mask:
            break;
        }
    }
    canvas_finish();
    return 1;
}

/// The number of commands the last render drew with a loss; see
/// `vellum::web::canvas_shell_loss_count`.
EMSCRIPTEN_KEEPALIVE int vellum_web_lowering_loss_count() {
    return lowering.losses;
}

/// Whether the last render refused its scene; its reason is
/// `vellum_web_refusal_reason`.
EMSCRIPTEN_KEEPALIVE int vellum_web_refused() {
    return !lowering.accepted && !lowering.reason.empty() ? 1 : 0;
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_refusal_reason() {
    return lowering.reason.c_str();
}

/// The diagnostics of the last render, as `vellum::graphics::accept_scene`
/// reports them: a refused scene's refusal, or an accepted scene's warnings.
EMSCRIPTEN_KEEPALIVE int vellum_web_diagnostic_count() {
    return static_cast<int>(lowering.diagnostics.size());
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_diagnostic_code(int index) {
    if (index < 0 || static_cast<std::size_t>(index) >= lowering.diagnostics.size()) return "";
    return lowering.diagnostics[static_cast<std::size_t>(index)].code.c_str();
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_diagnostic_node_id(int index) {
    if (index < 0 || static_cast<std::size_t>(index) >= lowering.diagnostics.size()) return "";
    return lowering.diagnostics[static_cast<std::size_t>(index)].node_id.c_str();
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_diagnostic_message(int index) {
    if (index < 0 || static_cast<std::size_t>(index) >= lowering.diagnostics.size()) return "";
    return lowering.diagnostics[static_cast<std::size_t>(index)].message.c_str();
}

EMSCRIPTEN_KEEPALIVE int vellum_web_command_count() {
    return static_cast<int>(commands.size());
}

EMSCRIPTEN_KEEPALIVE std::uint32_t vellum_web_command_digest() {
    std::uint32_t hash = 2166136261U;
    for (const auto& command : commands) {
        hash_u32(hash, static_cast<std::uint32_t>(command.kind));
        hash_string(hash, command.node_id);
        hash_float(hash, command.bounds.x);
        hash_float(hash, command.bounds.y);
        hash_float(hash, command.bounds.width);
        hash_float(hash, command.bounds.height);
        hash_float(hash, command.corner_radius);
        hash_string(hash, command.text);
        hash_float(hash, command.font_size);
        hash_float(hash, command.fill.red);
        hash_float(hash, command.fill.green);
        hash_float(hash, command.fill.blue);
        hash_float(hash, command.fill.alpha);
        if (command.vector) hash_vector(hash, *command.vector);
    }
    return hash;
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_backend_name() {
    return "wasm-shared-cpp-core+canvas2d-shell";
}

}  // extern "C"
