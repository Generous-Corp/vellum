// Renders the knob and LED document at gain 0.75 through VellumRenderer on an
// offscreen SkiaDawnSurface, and compares it with the soft renderer's output,
// which must itself equal the committed golden image byte for byte.
//
// The two renderers rasterise differently (GPU coverage rules, GPU colour
// arithmetic), so the comparison is a stated tolerance, not identity:
//   a pixel differs when any channel differs by more than kChannelTolerance,
//   and at most kMaxDifferingPercent of the pixels may differ.
// The empty document must fail that same tolerance, so the comparison is
// proved able to reject a wrong render.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>
#include <vellum/rmlui/vellum_renderer.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

using namespace vellum::rmlui;
namespace test = vellum::rmlui::test;

namespace {

const std::filesystem::path fixtures = VELLUM_RMLUI_TEST_FIXTURES;
constexpr std::uint32_t width = 160;
constexpr std::uint32_t height = 96;
constexpr int kChannelTolerance = 16;
constexpr double kMaxDifferingPercent = 1.0;

struct GainModel final : ModelHost {
    explicit GainModel(double value) : gain(value) {}
    Status read(std::string_view field, ModelValue& out) override {
        if (field != "gain")
            return Status::rejected;
        out = ModelValue::number_value(gain);
        return Status::ok;
    }
    Status write(std::string_view field, const ModelValue& value) override {
        if (field != "gain" || value.kind != ModelValue::Kind::number)
            return Status::rejected;
        gain = value.number;
        return Status::ok;
    }
    double gain;
};

// Loads `document` with the gain model and renders it twice: the first update
// evaluates the data bindings, the second lays out what they produced.
void render(RenderInterface& renderer, const std::string& document, double gain) {
    std::vector<AssetManifestEntry> manifest;
    for (const auto* name : {"knob_led.rml", "empty.rml"})
        manifest.push_back({name, test::sha256_hex(test::read_binary(fixtures / name))});
    RootedAssetProvider assets(fixtures, manifest, test::sha256_hex);
    HeadlessRecorder no_lua;
    Backend backend(assets, renderer, no_lua);
    VELLUM_RMLUI_CHECK(backend.open(width, height) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.bind_model("controls", std::make_shared<GainModel>(gain), {"gain"}) ==
                       Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document(document) == Status::ok);
    backend.render();
    backend.render();
    backend.close();
}

struct Comparison {
    int max_delta = 0;
    std::size_t differing = 0;
    double differing_percent = 0.0;
    bool within_tolerance = false;
};

Comparison compare(const std::vector<std::uint8_t>& actual, const std::vector<std::uint8_t>& expected) {
    VELLUM_RMLUI_CHECK(actual.size() == expected.size());
    Comparison result;
    for (std::size_t i = 0; i < actual.size(); i += 4) {
        int delta = 0;
        for (int k = 0; k < 4; ++k)
            delta = std::max(delta, std::abs(int(actual[i + k]) - int(expected[i + k])));
        result.max_delta = std::max(result.max_delta, delta);
        if (delta > kChannelTolerance)
            ++result.differing;
    }
    result.differing_percent = 100.0 * static_cast<double>(result.differing) /
                               static_cast<double>(actual.size() / 4);
    result.within_tolerance = result.differing_percent <= kMaxDifferingPercent;
    return result;
}

std::vector<std::uint8_t> render_vellum(vellum::graphics::SkiaDawnSurface& surface,
                                        const std::string& document, double gain,
                                        std::size_t& triangles) {
    VellumRenderer renderer(surface, 0x000000ffu);
    render(renderer, document, gain);
    VELLUM_RMLUI_CHECK(renderer.frame_ok());
    if (!renderer.last_error().empty())
        std::fprintf(stderr, "%s\n", renderer.last_error().c_str());
    VELLUM_RMLUI_CHECK(renderer.skipped_draws() == 0);
    triangles = renderer.triangles_submitted();
    std::vector<std::uint8_t> rgba;
    std::uint32_t pixel_width = 0;
    std::uint32_t pixel_height = 0;
    std::string error;
    const bool captured = surface.capture_rgba(rgba, pixel_width, pixel_height, &error);
    if (!captured)
        std::fprintf(stderr, "capture failed: %s\n", error.c_str());
    VELLUM_RMLUI_CHECK(captured && pixel_width == width && pixel_height == height);
    return rgba;
}

void report(const char* label, const Comparison& comparison) {
    std::printf("%s: max channel delta %d, %zu pixels (%.3f%%) differ by more than %d\n", label,
                comparison.max_delta, comparison.differing, comparison.differing_percent,
                kChannelTolerance);
}


// The mapping from a DrawCommand to a scene mesh, checked on the scene the
// renderer builds: translation then transform per vertex, the scissor as the
// mesh clip, a generated texture's bytes, a triangle with an unprojectable
// vertex dropped, and an image without a decoder skipped.
void check_command_mapping(vellum::graphics::SkiaDawnSurface& surface) {
    VellumRenderer renderer(surface);
    renderer.begin_frame(width, height);

    auto vertices = std::make_shared<std::vector<DrawVertex>>(std::vector<DrawVertex>{
        {1.0f, 2.0f, 0.0f, 0.0f, 0xff0000ffu},
        {5.0f, 2.0f, 1.0f, 0.0f, 0x00ff00ffu},
        {1.0f, 6.0f, 0.0f, 1.0f, 0x0000ffffu},
        {1.0f, 2.0f, 0.5f, 0.5f, 0xffffffffu},
    });
    DrawCommand command;
    command.kind = DrawCommand::Kind::texture;
    command.texture_bytes = std::make_shared<const std::string>(std::string(2 * 1 * 4, '\x7f'));
    command.texture_width = 2;
    command.texture_height = 1;
    command.translation_x = 10.0f;
    command.translation_y = 20.0f;
    command.vertices = vertices;
    command.indices = std::make_shared<std::vector<std::uint32_t>>(
        std::vector<std::uint32_t>{0, 1, 2, 0, 1, 3});
    command.scissor_enabled = true;
    command.scissor = {3, 4, 50, 60};
    // Scale x by 2.
    command.transform_enabled = true;
    command.transform = {2.0f, 0.0f, 0.0f, 0.0f,  0.0f, 1.0f, 0.0f, 0.0f,
                         0.0f, 0.0f, 1.0f, 0.0f,  0.0f, 0.0f, 0.0f, 1.0f};
    renderer.submit(command);
    VELLUM_RMLUI_CHECK(renderer.triangles_submitted() == 2);

    auto projective = command;
    // w = 1 - 0.1 * x: the fourth vertex lands at x = 11 after translation,
    // where w is negative, so the triangle that uses it is dropped.
    projective.transform[3] = -0.1f;
    projective.vertices = std::make_shared<std::vector<DrawVertex>>(std::vector<DrawVertex>{
        {-10.0f, 0.0f, 0.0f, 0.0f, 0xffffffffu},
        {-9.0f, 0.0f, 0.0f, 0.0f, 0xffffffffu},
        {-10.0f, 1.0f, 0.0f, 0.0f, 0xffffffffu},
        {1.0f, 0.0f, 0.0f, 0.0f, 0xffffffffu},
    });
    renderer.submit(projective);
    VELLUM_RMLUI_CHECK(renderer.triangles_submitted() == 3);

    auto image = command;
    image.texture_path = "missing.png";
    renderer.submit(image);
    VELLUM_RMLUI_CHECK(renderer.skipped_draws() == 1);

    const auto& nodes = renderer.scene().root.children;
    VELLUM_RMLUI_CHECK(nodes.size() == 2);
    const auto& mesh = *nodes[0].mesh;
    VELLUM_RMLUI_CHECK(nodes[0].kind == vellum::graphics::SceneNode::Kind::mesh);
    VELLUM_RMLUI_CHECK(mesh.vertices.size() == 6 && mesh.indices.size() == 6);
    VELLUM_RMLUI_CHECK(mesh.vertices[0].x == 22.0f && mesh.vertices[0].y == 22.0f);
    VELLUM_RMLUI_CHECK(mesh.vertices[1].x == 30.0f && mesh.vertices[1].u == 1.0f);
    VELLUM_RMLUI_CHECK(mesh.vertices[2].y == 26.0f && mesh.vertices[2].premultiplied_rgba == 0x0000ffffu);
    VELLUM_RMLUI_CHECK(mesh.clip_enabled && mesh.clip.x == 3.0f && mesh.clip.y == 4.0f &&
                       mesh.clip.width == 50.0f && mesh.clip.height == 60.0f);
    VELLUM_RMLUI_CHECK(mesh.texture && mesh.texture->width == 2 && mesh.texture->height == 1 &&
                       mesh.texture->premultiplied_rgba ==
                           std::vector<std::uint8_t>(8, std::uint8_t{0x7f}));
    // The projective command keeps only its first triangle; the second uses
    // the vertex at x = 11, where w is negative.
    VELLUM_RMLUI_CHECK(nodes[1].mesh->vertices.size() == 3);
    // A shared generated texture is converted once.
    VELLUM_RMLUI_CHECK(nodes[1].mesh->texture == mesh.texture);
}

} // namespace

int main() {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::fprintf(stderr, "Dawn bootstrap: %s\n", error.c_str());
        return 1;
    }
    auto surface = vellum::graphics::SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = 1.0f, .native_surface_handle = nullptr},
        &error);
    if (!surface) {
        std::fprintf(stderr, "SkiaDawnSurface: %s\n", error.c_str());
        return 1;
    }

    check_command_mapping(*surface);

    // The reference: the soft renderer's frame, which is the golden image.
    SoftRenderer soft(0x000000ffu);
    render(soft, "knob_led.rml", 0.75);
    VELLUM_RMLUI_CHECK(soft.skipped_draws() == 0 && soft.triangles_drawn() > 0);
    VELLUM_RMLUI_CHECK(soft.encode_png() == test::read_binary(fixtures / "knob_led.golden.png"));
    const auto& reference = soft.pixels();

    std::size_t knob_triangles = 0;
    const auto knob = render_vellum(*surface, "knob_led.rml", 0.75, knob_triangles);
    VELLUM_RMLUI_CHECK(knob_triangles > 0);
    const auto knob_comparison = compare(knob, reference);
    report("knob_led.rml at gain 0.75", knob_comparison);

    // Negative control: an empty document through the same renderer and the
    // same comparison must fall outside the tolerance.
    std::size_t empty_triangles = 0;
    const auto empty = render_vellum(*surface, "empty.rml", 0.75, empty_triangles);
    const auto empty_comparison = compare(empty, reference);
    report("empty.rml (negative control)", empty_comparison);

    // A second, harder control: the same document with the LED off and the
    // pointer turned down differs only where those two parts are drawn.
    std::size_t low_triangles = 0;
    const auto low = render_vellum(*surface, "knob_led.rml", 0.2, low_triangles);
    const auto low_comparison = compare(low, reference);
    report("knob_led.rml at gain 0.2 (negative control)", low_comparison);

    if (!knob_comparison.within_tolerance) {
        std::fprintf(stderr, "VellumRenderer output is outside the tolerance (%.1f%% allowed)\n",
                     kMaxDifferingPercent);
        return 1;
    }
    if (empty_comparison.within_tolerance || low_comparison.within_tolerance) {
        std::fprintf(stderr, "a negative control passed the tolerance, so it proves nothing\n");
        return 1;
    }
    std::printf("vellum-rmlui vellum renderer: ok (%zu triangles)\n", knob_triangles);
    return 0;
}
