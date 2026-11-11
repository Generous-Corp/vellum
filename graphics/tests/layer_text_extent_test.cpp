#include "skia_dawn_surface_test_access.hpp"

#include <cmath>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    using namespace vellum::graphics;
    if (argc != 2) return 2;
    const std::string directory = argv[1];
    PaintCommand command;
    command.kind = PaintCommand::Kind::text;
    command.bounds = {14.0F, 20.0F, 1.0F, 1.0F};
    command.font_size = 16.0F;
    command.text_runs = {{.text = "Faded group",
                          .style = {.font_family = "Inter", .font_size = 16.0F}}};
    TextMetrics metrics;
    std::string error;
    if (!SkiaDawnSurface::measure_text(command.text_runs, metrics, directory, &error)) {
        std::cerr << "FAIL font fixture: " << error << '\n';
        return 1;
    }
    int failures = 0;
    const auto expect = [&](bool ok, const char* message) {
        std::cout << (ok ? "PASS " : "FAIL ") << message << '\n';
        if (!ok) ++failures;
    };
    const auto extent = SkiaDawnSurfaceTestAccess::measure_layer_text_extent(
        command, directory, &error);
    expect(extent.has_value() && error.empty(),
           "a newly shaped paragraph supplies a layer extent");
    if (extent) {
        expect(extent->x == -2.0F && extent->y == 4.0F &&
                   std::abs(extent->width - (metrics.width + 32.0F)) < 0.01F &&
                   std::abs(extent->height - (metrics.ascent + metrics.descent + 32.0F)) < 0.01F,
               "the extent includes shaped overflow and font overhang");
    }
    // A regular font file cannot be a directory, so this fails deterministically.
    error.clear();
    const auto failed = SkiaDawnSurfaceTestAccess::measure_layer_text_extent(
        command, directory + "/Inter-Regular.ttf", &error);
    expect(!failed.has_value() && error.find("font directory is unavailable") != std::string::npos,
           "failed paragraph creation refuses the extent and preserves the error");
    return failures == 0 ? 0 : 1;
}
