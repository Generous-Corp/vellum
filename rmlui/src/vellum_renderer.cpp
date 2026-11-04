#include <vellum/rmlui/vellum_renderer.hpp>

#include <algorithm>
#include <cmath>
#include <string>
#include <utility>

namespace vellum::rmlui {

namespace {

namespace graphics = vellum::graphics;

// The soft renderer's bound: farther vertices are not drawn by either renderer.
constexpr double kMaxCoordinate = 16384.0;

std::uint32_t channel(std::uint32_t packed, int index) {
    return (packed >> (24 - 8 * index)) & 0xffu;
}

graphics::Color clear_color(std::uint32_t straight_rgba) {
    return graphics::Color::rgba(static_cast<float>(channel(straight_rgba, 0)) / 255.0f,
                                 static_cast<float>(channel(straight_rgba, 1)) / 255.0f,
                                 static_cast<float>(channel(straight_rgba, 2)) / 255.0f,
                                 static_cast<float>(channel(straight_rgba, 3)) / 255.0f);
}

// The same projection as the soft renderer, without its 1/256 pixel snap.
bool project(const DrawCommand& command, const DrawVertex& vertex, float& out_x, float& out_y) {
    double x = static_cast<double>(vertex.x) + static_cast<double>(command.translation_x);
    double y = static_cast<double>(vertex.y) + static_cast<double>(command.translation_y);
    if (command.transform_enabled) {
        const auto& m = command.transform;
        const double cx = static_cast<double>(m[0]) * x + static_cast<double>(m[4]) * y +
                          static_cast<double>(m[12]);
        const double cy = static_cast<double>(m[1]) * x + static_cast<double>(m[5]) * y +
                          static_cast<double>(m[13]);
        const double cw = static_cast<double>(m[3]) * x + static_cast<double>(m[7]) * y +
                          static_cast<double>(m[15]);
        if (!(cw > 1e-9))
            return false;
        x = cx / cw;
        y = cy / cw;
    }
    if (!std::isfinite(x) || !std::isfinite(y) || std::fabs(x) > kMaxCoordinate ||
        std::fabs(y) > kMaxCoordinate)
        return false;
    out_x = static_cast<float>(x);
    out_y = static_cast<float>(y);
    return true;
}

} // namespace

VellumRenderer::VellumRenderer(graphics::SkiaDawnSurface& surface, std::uint32_t clear_rgba)
    : surface_(surface), clear_rgba_(clear_rgba) {}

void VellumRenderer::begin_frame(std::uint32_t width, std::uint32_t height) {
    width_ = width;
    height_ = height;
    triangles_ = 0;
    skipped_ = 0;
    frame_ok_ = false;
    error_.clear();
    textures_.clear();
    scene_ = {};
    scene_.width = static_cast<float>(width);
    scene_.height = static_cast<float>(height);
    // The clear colour is straight RGBA; Skia premultiplies it as the soft
    // renderer does.
    scene_.background = clear_color(clear_rgba_);
    scene_.root.id = "rmlui";
    scene_.root.bounds = {0.0f, 0.0f, scene_.width, scene_.height};
}

std::shared_ptr<const graphics::MeshTexture>
VellumRenderer::texture_for(const DrawCommand& command) {
    if (command.texture_path.empty()) {
        if (const auto found = textures_.find(command.texture_bytes.get()); found != textures_.end())
            return found->second.texture;
        const auto expected =
            static_cast<std::size_t>(command.texture_width) * command.texture_height * 4u;
        if (command.texture_width == 0 || command.texture_height == 0 ||
            command.texture_bytes->size() != expected)
            return nullptr;
        auto texture = std::make_shared<graphics::MeshTexture>();
        texture->width = command.texture_width;
        texture->height = command.texture_height;
        const auto* bytes = reinterpret_cast<const std::uint8_t*>(command.texture_bytes->data());
        texture->premultiplied_rgba.assign(bytes, bytes + expected);
        textures_.emplace(command.texture_bytes.get(),
                          CachedTexture{command.texture_bytes, texture});
        return texture;
    }
    auto texture = std::make_shared<graphics::MeshTexture>();
    if (!decoder_ ||
        !decoder_(*command.texture_bytes, texture->width, texture->height,
                  texture->premultiplied_rgba) ||
        texture->width == 0 || texture->height == 0 ||
        texture->premultiplied_rgba.size() !=
            static_cast<std::size_t>(texture->width) * texture->height * 4u)
        return nullptr;
    return texture;
}

void VellumRenderer::submit(const DrawCommand& command) {
    if (!command.vertices || !command.indices || width_ == 0 || height_ == 0)
        return;
    std::shared_ptr<const graphics::MeshTexture> texture;
    if (command.kind == DrawCommand::Kind::texture && command.texture_bytes) {
        texture = texture_for(command);
        if (!texture) {
            ++skipped_;
            return;
        }
    }

    auto mesh = std::make_shared<graphics::TriangleMesh>();
    mesh->texture = std::move(texture);
    if (command.scissor_enabled) {
        mesh->clip_enabled = true;
        mesh->clip = {static_cast<float>(command.scissor.x), static_cast<float>(command.scissor.y),
                      static_cast<float>(std::max(0, command.scissor.width)),
                      static_cast<float>(std::max(0, command.scissor.height))};
    }
    // Each triangle is projected on its own, so one vertex that cannot be
    // projected drops only the triangles that use it.
    const auto& vertices = *command.vertices;
    const auto& indices = *command.indices;
    for (std::size_t i = 0; i + 2 < indices.size(); i += 3) {
        graphics::MeshVertex corners[3];
        bool drawable = true;
        for (int k = 0; k < 3 && drawable; ++k) {
            const auto index = indices[i + static_cast<std::size_t>(k)];
            if (index >= vertices.size()) {
                drawable = false;
                break;
            }
            const auto& source = vertices[index];
            auto& corner = corners[k];
            drawable = project(command, source, corner.x, corner.y);
            corner.u = source.u;
            corner.v = source.v;
            corner.premultiplied_rgba = source.colour;
        }
        if (!drawable)
            continue;
        for (const auto& corner : corners) {
            mesh->indices.push_back(static_cast<std::uint32_t>(mesh->vertices.size()));
            mesh->vertices.push_back(corner);
        }
        ++triangles_;
    }
    if (mesh->indices.empty())
        return;

    graphics::SceneNode node;
    node.id = "draw-" + std::to_string(scene_.root.children.size());
    node.kind = graphics::SceneNode::Kind::mesh;
    node.mesh = std::move(mesh);
    scene_.root.children.push_back(std::move(node));
}

void VellumRenderer::end_frame() {
    if (width_ == 0 || height_ == 0) {
        error_ = "end_frame without a frame of non-zero size";
        frame_ok_ = false;
        return;
    }
    frame_ok_ = surface_.render(scene_, &error_);
}

} // namespace vellum::rmlui
