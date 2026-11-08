#include "support/scene_capture_harness.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

namespace harness = vellum::graphics::capture_harness;
namespace fixtures = vellum::graphics::capture_harness::fixtures;

constexpr int skip_exit_code = 77;
constexpr std::uint32_t width = 400;
constexpr std::uint32_t height = 300;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

long long median(std::vector<long long> values) {
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

void negative_controls(const harness::CaptureResult& empty) {
    const auto floor = harness::assert_content_floor(empty);
    expect(empty.ok && !floor, "empty scene fails the content floor", floor.message);

    harness::CaptureResult failed;
    failed.error = "simulated readback failure";
    expect(!harness::assert_content_floor(failed),
           "failed capture fails the content floor");
    expect(!harness::assert_identical(failed, empty),
           "failed capture is never identical");
}

void determinism(const harness::CaptureResult& text, const harness::CaptureResult& mesh) {
    const auto different = harness::assert_identical(text, mesh);
    expect(!different && different.message.find("first at") != std::string::npos,
           "different scenes are reported with their first differing pixel",
           different.message);

    auto perturbed = text;
    const std::uint32_t x = 123;
    const std::uint32_t y = 45;
    auto& channel = perturbed.rgba[(static_cast<std::size_t>(y) * width + x) * 4U + 1U];
    channel = static_cast<std::uint8_t>(channel ^ 0x01U);
    const auto one_bit = harness::assert_identical(text, perturbed);
    expect(!one_bit && one_bit.message.find("first at (123, 45)") != std::string::npos,
           "a one-bit change is caught at its exact pixel", one_bit.message);

    const auto scaled = harness::assert_deterministic(
        fixtures::combined(width, height), width, height, 2.0F);
    expect(static_cast<bool>(scaled), "combined scene is deterministic at scale 2",
           scaled.message);
}

void goldens(const harness::CaptureResult& first, const harness::CaptureResult& second,
             const std::filesystem::path& directory) {
    std::error_code ignored;
    std::filesystem::remove_all(directory, ignored);
    const auto golden = directory / "combined.rgba";

    const auto missing = harness::compare_to_golden(
        first, golden, 0, 0.0, harness::GoldenMode::compare);
    expect(!missing, "a missing golden fails", missing.message);

    const auto recorded = harness::compare_to_golden(
        first, golden, 0, 0.0, harness::GoldenMode::update);
    expect(static_cast<bool>(recorded) && std::filesystem::exists(golden),
           "update mode records the golden", recorded.message);

    const auto exact = harness::compare_to_golden(
        second, golden, 0, 0.0, harness::GoldenMode::compare);
    expect(static_cast<bool>(exact), "a second run matches the golden exactly",
           exact.message);

    auto perturbed = second;
    for (std::size_t index = 0; index < 10; ++index) {
        auto& value = perturbed.rgba[index * 4096U];
        value = static_cast<std::uint8_t>(value > 127 ? value - 5 : value + 5);
    }
    expect(!harness::compare_to_golden(perturbed, golden, 2, 0.0,
                                        harness::GoldenMode::compare),
           "a delta above the channel tolerance fails");
    expect(static_cast<bool>(harness::compare_to_golden(
               perturbed, golden, 8, 0.0, harness::GoldenMode::compare)),
           "a delta within the channel tolerance passes");
    expect(static_cast<bool>(harness::compare_to_golden(
               perturbed, golden, 2, 0.001, harness::GoldenMode::compare)),
           "differing pixels within the fraction tolerance pass");

    auto cropped = second;
    cropped.pixel_height -= 1;
    cropped.rgba.resize(cropped.rgba.size() - static_cast<std::size_t>(width) * 4U);
    expect(!harness::compare_to_golden(cropped, golden, 255, 1.0,
                                        harness::GoldenMode::compare),
           "a size mismatch fails at any tolerance");

    { std::ofstream(directory / "truncated.rgba", std::ios::binary) << "VLMRGBA1"; }
    expect(!harness::compare_to_golden(second, directory / "truncated.rgba", 255, 1.0,
                                        harness::GoldenMode::compare),
           "a truncated golden fails");
}

void timings(const std::filesystem::path& evidence_path, long long cold_creation) {
    constexpr int runs = 5;
    std::vector<long long> creation;
    std::vector<long long> render;
    std::vector<long long> capture;
    harness::CaptureResult last;
    const auto scene = fixtures::combined(width, height);
    for (int run = 0; run < runs; ++run) {
        last = harness::render_offscreen(scene, width, height);
        if (!last.ok) {
            expect(false, "timing capture", last.error);
            return;
        }
        creation.push_back(last.surface_creation.count());
        render.push_back(last.render.count());
        capture.push_back(last.capture.count());
    }
    std::cout << "TIMING 400x300 combined scene over " << runs
              << " fresh surfaces (microseconds): cold_surface_creation="
              << cold_creation << " median_surface_creation=" << median(creation)
              << " median_render=" << median(render)
              << " median_capture_rgba=" << median(capture) << '\n';
    std::cout << "EVIDENCE renderer=" << last.evidence.renderer
              << " backend=" << last.evidence.backend
              << " adapter=" << last.evidence.adapter
              << " texture_format=" << last.evidence.texture_format << '\n';
    std::ofstream out(evidence_path);
    out << "{\n"
        << "  \"scene\": \"combined\",\n"
        << "  \"width\": " << width << ",\n"
        << "  \"height\": " << height << ",\n"
        << "  \"runs\": " << runs << ",\n"
        << "  \"cold_surface_creation_us\": " << cold_creation << ",\n"
        << "  \"median_surface_creation_us\": " << median(creation) << ",\n"
        << "  \"median_render_us\": " << median(render) << ",\n"
        << "  \"median_capture_rgba_us\": " << median(capture) << ",\n"
        << "  \"renderer\": \"" << last.evidence.renderer << "\",\n"
        << "  \"backend\": \"" << last.evidence.backend << "\",\n"
        << "  \"adapter\": \"" << last.evidence.adapter << "\",\n"
        << "  \"unique_colors\": " << last.stats.unique_colors << ",\n"
        << "  \"non_background_pixels\": " << last.stats.non_background_pixels << "\n"
        << "}\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: vellum-scene-capture-harness-test <golden-dir> <evidence.json>\n";
        return 2;
    }
    try {
        const auto empty = harness::render_offscreen(
            fixtures::empty(width, height), width, height);
        if (empty.device_unavailable) {
#if VELLUM_SCENE_CAPTURE_REQUIRE_DEVICE
            std::cerr << "FAIL no GPU device although VELLUM_REQUIRE_GPU is ON: "
                      << empty.error << '\n';
            return 1;
#else
            std::cout << "SKIP no GPU device for offscreen capture: " << empty.error << '\n';
            return skip_exit_code;
#endif
        }
        expect(empty.ok, "empty scene captures", empty.error);
        negative_controls(empty);

        harness::CaptureResult text;
        harness::CaptureResult mesh;
        harness::CaptureResult combined_first;
        harness::CaptureResult combined_second;
        for (const auto& [name, scene] : fixtures::all(width, height)) {
            harness::CaptureResult first;
            harness::CaptureResult second;
            const auto deterministic =
                harness::assert_deterministic(scene, width, height, 1.0F, &first, &second);
            expect(static_cast<bool>(deterministic), name + " is deterministic",
                   deterministic.message);
            const auto floor = harness::assert_content_floor(first);
            expect(static_cast<bool>(floor), name + " passes the content floor",
                   floor.message);
            if (name == "text") text = first;
            if (name == "mesh") mesh = first;
            if (name == "combined") {
                combined_first = std::move(first);
                combined_second = std::move(second);
            }
        }
        if (text.ok && mesh.ok) determinism(text, mesh);
        if (combined_first.ok && combined_second.ok) {
            goldens(combined_first, combined_second, argv[1]);
        }
        timings(argv[2], empty.surface_creation.count());
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << (failures == 0 ? "scene capture harness passed" : "scene capture harness failed")
              << '\n';
    return failures == 0 ? 0 : 1;
}
