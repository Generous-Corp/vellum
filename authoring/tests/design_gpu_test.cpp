#include "rendered_tree_materializer.hpp"

#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/graphics/scene.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// DesignIR to GPU pixels: a normalized DesignIR document with blend, opacity,
// isolation and a mask, materialized by @vellum/ui into a retained tree (see
// design_gpu_tree.mjs), lowered by the native materializer and drawn by Skia
// Graphite on the real device. The same document without its compositing is
// the negative control: it must fail the same probes.
namespace {

using Rgb = std::array<float, 3>;

constexpr Rgb hex(std::uint32_t value) {
    return {static_cast<float>((value >> 16U) & 0xFFU) / 255.0F,
            static_cast<float>((value >> 8U) & 0xFFU) / 255.0F,
            static_cast<float>(value & 0xFFU) / 255.0F};
}

constexpr Rgb kBackground = hex(0x336699);
constexpr Rgb kBackdrop = hex(0xCC5926);
constexpr Rgb kSource = hex(0x40A6E6);

Rgb half_multiply(const Rgb& backdrop) {
    Rgb out{};
    for (int channel = 0; channel < 3; ++channel) {
        out[channel] = 0.5F * kSource[channel] * backdrop[channel] + 0.5F * backdrop[channel];
    }
    return out;
}

struct Probe final {
    const char* what;
    std::uint32_t x;
    std::uint32_t y;
    Rgb expected;
};

bool read(const char* path, std::string& text) {
    std::ifstream file(path, std::ios::binary);
    std::stringstream buffer;
    buffer << file.rdbuf();
    text = buffer.str();
    return file.good() || file.eof();
}

bool draw(const char* path, std::vector<std::uint8_t>& rgba, std::uint32_t& width,
          vellum::graphics::Scene& scene) {
    std::string json;
    std::string error;
    vellum::authoring::RenderedApplication rendered;
    if (!read(path, json) || !vellum::authoring::materialize_rendered_tree(json, rendered, &error)) {
        std::cerr << path << ": " << error << '\n';
        return false;
    }
    scene = rendered.scene;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = vellum::graphics::SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height)},
        &error);
    std::uint32_t height = 0;
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(rgba, width, height, &error)) {
        std::cerr << path << ": " << error << '\n';
        return false;
    }
    return true;
}

bool holds(const std::string& name, const std::vector<std::uint8_t>& rgba, std::uint32_t width,
           const std::vector<Probe>& probes, bool report) {
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* pixel = rgba.data() + (static_cast<std::size_t>(probe.y) * width + probe.x) * 4U;
        for (int channel = 0; channel < 3; ++channel) {
            const int want = static_cast<int>(std::lround(probe.expected[channel] * 255.0F));
            if (std::abs(static_cast<int>(pixel[channel]) - want) > 3 || pixel[3] != 255U) {
                if (report) {
                    std::cerr << name << ": " << probe.what << ": got " << int(pixel[0]) << ','
                              << int(pixel[1]) << ',' << int(pixel[2]) << ',' << int(pixel[3])
                              << ", expected " << int(std::lround(probe.expected[0] * 255.0F))
                              << ',' << int(std::lround(probe.expected[1] * 255.0F)) << ','
                              << int(std::lround(probe.expected[2] * 255.0F)) << ",255\n";
                }
                passed = false;
                break;
            }
        }
    }
    return passed;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    using vellum::graphics::BlendMode;
    const std::vector<Probe> probes{
        {"the backdrop beside the blend", 16, 32, kBackdrop},
        {"a half-faded multiply over the backdrop", 38, 32, half_multiply(kBackdrop)},
        {"a half-faded multiply over the background", 66, 32, half_multiply(kBackground)},
        {"content inside its mask", 62, 58, kSource},
        {"content outside its mask", 86, 58, kBackground},
    };
    std::vector<std::uint8_t> composited;
    std::vector<std::uint8_t> plain;
    std::uint32_t width = 0;
    std::uint32_t plain_width = 0;
    vellum::graphics::Scene scene;
    vellum::graphics::Scene plain_scene;
    if (!draw(argv[1], composited, width, scene) || !draw(argv[2], plain, plain_width, plain_scene)) {
        return 1;
    }
    const auto* blended = vellum::graphics::find_node(scene, "screen/blended");
    const auto* mask = vellum::graphics::find_node(scene, "screen/mask");
    const auto* cut = vellum::graphics::find_node(scene, "screen/cut");
    if (scene.scene_version != vellum::graphics::kSceneVersion || blended == nullptr ||
        blended->blend_mode != BlendMode::multiply || blended->opacity != 0.5F ||
        mask == nullptr || !mask->mask || cut == nullptr || !cut->isolate) {
        std::cerr << "the DesignIR compositing did not reach the scene\n";
        return 1;
    }
    if (!holds("composited", composited, width, probes, true)) return 1;
    if (holds("plain", plain, plain_width, probes, false)) {
        std::cerr << "negative control: the design without compositing passed the probes\n";
        return 1;
    }
    std::cout << "DesignIR compositing reached the GPU pixels\n";
    return 0;
}
