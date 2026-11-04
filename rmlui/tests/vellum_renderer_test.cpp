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
constexpr double kMaxDifferingPercent = 2.0;

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
