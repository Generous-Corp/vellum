#include "support/scene_capture_harness.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

namespace harness = vellum::graphics::capture_harness;
namespace fixtures = vellum::graphics::capture_harness::fixtures;

constexpr std::uint32_t width = 400;
constexpr std::uint32_t height = 300;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

std::string json_string(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (const char character : value) {
        const auto byte = static_cast<unsigned char>(character);
        if (character == '"' || character == '\\') {
            out << '\\' << character;
        } else if (byte < 0x20U) {
            out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(byte)
                << std::dec;
        } else {
            out << character;
        }
    }
    out << '"';
    return out.str();
}

std::optional<std::string> environment(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr) return std::nullopt;
    return std::string(value);
}

void restore(const char* name, const std::optional<std::string>& value) {
    if (value) {
        setenv(name, value->c_str(), 1);
    } else {
        unsetenv(name);
    }
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

    const auto saved_update = environment("VELLUM_UPDATE_GOLDENS");
    const auto saved_ci = environment("CI");
    setenv("VELLUM_UPDATE_GOLDENS", "1", 1);
    setenv("CI", "true", 1);
    const auto refused_path = directory / "refused.rgba";
    const auto refused = harness::compare_to_golden(second, refused_path, 0, 0.0);
    restore("VELLUM_UPDATE_GOLDENS", saved_update);
    restore("CI", saved_ci);
    expect(!refused && !std::filesystem::exists(refused_path),
           "VELLUM_UPDATE_GOLDENS=1 is refused under CI", refused.message);

    const auto outside = std::filesystem::temp_directory_path() /
                         ("vellum-golden-escape-" + std::to_string(getpid()));
    std::filesystem::create_directories(outside, ignored);
    const auto absolute_escape = outside / "absolute.rgba";
    const auto absolute = harness::compare_to_golden(
        second, absolute_escape, 0, 0.0, harness::GoldenMode::update);
    expect(!absolute && !std::filesystem::exists(absolute_escape) &&
               absolute.message.find("outside the golden roots") != std::string::npos,
           "update refuses an absolute path outside the golden roots", absolute.message);

    const auto dotdot_escape = directory / ".." / ".." / "dotdot-escape.rgba";
    const auto dotdot = harness::compare_to_golden(
        second, dotdot_escape, 0, 0.0, harness::GoldenMode::update);
    expect(!dotdot && !std::filesystem::exists(dotdot_escape),
           "update refuses a path that escapes through ..", dotdot.message);

    const auto link = directory / "escape-link";
    std::filesystem::create_directory_symlink(outside, link, ignored);
    const auto linked = harness::compare_to_golden(
        second, link / "linked.rgba", 0, 0.0, harness::GoldenMode::update);
    expect(!ignored && !linked && !std::filesystem::exists(outside / "linked.rgba"),
           "update refuses a path that escapes through a symlink", linked.message);
    std::filesystem::remove(link, ignored);
    std::filesystem::remove_all(outside, ignored);

    expect(harness::is_within_golden_roots(harness::default_golden_directory() / "a.rgba") &&
               !harness::is_within_golden_roots(harness::default_golden_directory()),
           "the fixture directory accepts files but is not itself a golden");
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
        << "  \"renderer\": " << json_string(last.evidence.renderer) << ",\n"
        << "  \"backend\": " << json_string(last.evidence.backend) << ",\n"
        << "  \"adapter\": " << json_string(last.evidence.adapter) << ",\n"
        << "  \"frame_full_repaint\": " << (last.frame_stats.full_repaint ? "true" : "false")
        << ",\n"
        << "  \"frame_full_repaint_reason\": "
        << json_string(std::string(vellum::graphics::to_string(
               last.frame_stats.full_repaint_reason)))
        << ",\n"
        << "  \"frame_damage_fraction\": " << last.frame_stats.damage_fraction << ",\n"
        << "  \"frame_paint_commands_recorded\": "
        << last.frame_stats.paint_commands_recorded << ",\n"
        << "  \"frame_paint_commands_total\": " << last.frame_stats.paint_commands_total
        << ",\n"
        << "  \"unique_colors\": " << last.stats.unique_colors << ",\n"
        << "  \"non_background_pixels\": " << last.stats.non_background_pixels << ",\n"
        << "  \"telemetry\": " << vellum::graphics::to_json(last.telemetry) << "\n"
        << "}\n";
    out.close();

    // The evidence carries the renderer's telemetry for the capture: one full
    // frame of the combined scene, whose first render misses every cache.
    std::ifstream written(evidence_path);
    const std::string evidence((std::istreambuf_iterator<char>(written)),
                               std::istreambuf_iterator<char>());
    const auto& telemetry = last.telemetry;
    expect(evidence.find("\"telemetry\": {\"frame\":1,\"full_repaint\":true") !=
                   std::string::npos &&
               evidence.find("\"paragraphs\":{") != std::string::npos &&
               evidence.find("\"atlas_bytes\":") != std::string::npos,
           "the capture evidence records the render telemetry");
    expect(telemetry.frame == 1 && telemetry.damage_fraction == 1.0 &&
               telemetry.paragraphs.frame_misses > 0 && telemetry.paragraphs.frame_hits == 0 &&
               telemetry.textures.entries > 0 &&
               telemetry.atlas_bytes == telemetry.textures.bytes,
           "the combined capture's telemetry shows a cold first frame",
           vellum::graphics::to_json(telemetry));
}

int run_checks(const std::filesystem::path& helper_goldens,
               const std::filesystem::path& evidence) {
    const auto empty = harness::render_offscreen(fixtures::empty(width, height), width, height);
    expect(empty.ok, "empty scene captures", empty.error);
    if (!empty.ok) return 1;
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
        expect(static_cast<bool>(deterministic), name + " is deterministic in one process",
               deterministic.message);
        const auto floor = harness::assert_content_floor(first);
        expect(static_cast<bool>(floor), name + " passes the content floor", floor.message);
        if (name == "text") text = first;
        if (name == "mesh") mesh = first;
        if (name == "combined") {
            combined_first = std::move(first);
            combined_second = std::move(second);
        }
    }
    if (text.ok && mesh.ok) determinism(text, mesh);
    if (combined_first.ok && combined_second.ok) {
        goldens(combined_first, combined_second, helper_goldens);
    }
    timings(evidence, empty.surface_creation.count());
    return 0;
}

int run_golden_pass(const std::filesystem::path& directory, harness::GoldenMode mode) {
    const bool record = mode == harness::GoldenMode::update;
    if (record) {
        std::error_code ignored;
        std::filesystem::remove_all(directory, ignored);
    }
    for (const auto& [name, scene] : fixtures::all(width, height)) {
        const auto result = harness::render_offscreen(scene, width, height);
        const auto floor = harness::assert_content_floor(result);
        expect(static_cast<bool>(floor), name + " passes the content floor", floor.message);
        const auto golden = harness::compare_to_golden(
            result, directory / (name + ".rgba"), 0, 0.0, mode);
        expect(static_cast<bool>(golden),
               name + (record ? " is recorded" : " matches the recording process exactly"),
               golden.message);
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string usage =
        "usage: vellum-scene-capture-harness-test checks <helper-golden-dir> <evidence.json>\n"
        "       vellum-scene-capture-harness-test record|compare <golden-dir>\n";
    if (argc < 3) {
        std::cerr << usage;
        return 2;
    }
    const std::string mode = argv[1];
    try {
        if (mode == "checks" && argc == 4) {
            if (run_checks(argv[2], argv[3]) != 0) ++failures;
        } else if (mode == "record" && argc == 3) {
            run_golden_pass(argv[2], harness::GoldenMode::update);
        } else if (mode == "compare" && argc == 3) {
            run_golden_pass(argv[2], harness::GoldenMode::compare);
        } else {
            std::cerr << usage;
            return 2;
        }
    } catch (const std::exception& exception) {
        std::cerr << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "scene capture harness " << mode << (failures == 0 ? " passed" : " failed")
              << '\n';
    return failures == 0 ? 0 : 1;
}
