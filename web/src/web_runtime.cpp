#include <vellum/graphics/paint_command.hpp>
#include <vellum/runtime/kernel.hpp>

#include <emscripten.h>

#include <bit>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace {

vellum::graphics::Scene scene;
std::vector<vellum::graphics::PaintCommand> commands;
std::unique_ptr<vellum::runtime::Kernel> kernel;

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
    commands.clear();
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

EMSCRIPTEN_KEEPALIVE int vellum_web_render() {
    if (!kernel || scene.width <= 0.0F || scene.height <= 0.0F) return 0;
    commands = vellum::graphics::make_paint_commands(scene);
    const auto background = scene.background;
    canvas_begin(scene.width, scene.height, background.red, background.green,
                 background.blue, background.alpha);
    for (const auto& command : commands) {
        const auto color = command.fill;
        if (command.kind == vellum::graphics::PaintCommand::Kind::rectangle) {
            canvas_rectangle(
                command.bounds.x, command.bounds.y, command.bounds.width,
                command.bounds.height, command.corner_radius,
                color.red, color.green, color.blue, color.alpha);
        } else {
            canvas_text(
                command.text.c_str(), command.bounds.x,
                command.bounds.y + command.font_size, command.font_size,
                color.red, color.green, color.blue, color.alpha);
        }
    }
    canvas_finish();
    return 1;
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
    }
    return hash;
}

EMSCRIPTEN_KEEPALIVE const char* vellum_web_backend_name() {
    return "wasm-shared-cpp-core+canvas2d-shell";
}

}  // extern "C"
