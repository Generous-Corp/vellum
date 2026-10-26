// Installed-package consumer of the units built from the landed mirror.
//
// It touches each library through real work rather than a symbol that a
// linker could satisfy without the unit: SheenBidi through the bidi
// analyzer, Skia text shaping through the bundled Inter face, CHOC through the
// runtime queue, and the rendered build info that names the export's origin.
// font_resolver.hpp exercises the provider-owned text path without making
// private Skia/Dawn definitions part of the consumer ABI.

#include <pulp/canvas/bidi.hpp>
#include <pulp/canvas/canvas.hpp>
#include <pulp/canvas/font_resolver.hpp>
#include <pulp/canvas/text_shaper.hpp>
#include <pulp/runtime/base64.hpp>
#include <pulp/runtime/build_info.hpp>
#include <pulp/runtime/spsc_queue.hpp>

#include <cstdio>
#include <string>
#include <string_view>

namespace {

int fail(const char* what) {
    std::fprintf(stderr, "mirror consumer: %s\n", what);
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return fail("usage: vellum-mirror-consumer <expected origin commit>");
    if (pulp::runtime::kGitSha != std::string_view(argv[1]) || pulp::runtime::kGitDirty)
        return fail("build info does not name the export's origin commit");

    const auto color = pulp::canvas::Color::from_hsv({210.0f, 1.0f, 1.0f});
    if (color.r8() != 0 || color.g8() != 128 || color.b8() != 255)
        return fail("color conversion");

    const auto bidi = pulp::canvas::BidiAnalyzer::analyze("hello \xD7\xA9\xD7\x9C\xD7\x95\xD7\x9D");
    if (!pulp::canvas::BidiAnalyzer::has_sheenbidi() || !bidi.used_sheenbidi || bidi.runs.size() != 2 ||
        !bidi.runs[1].is_rtl())
        return fail("bidi analysis did not run SheenBidi");

    pulp::canvas::TextShaper shaper;
    const auto prepared = shaper.prepare("Vellum mirror package", "Inter", 16.0f);
    const auto narrow = shaper.layout(prepared, 60.0f);
    const auto wide = shaper.layout(prepared, 1000.0f);
    if (wide.lines.size() != 1 || narrow.lines.size() < 2 || wide.total_width <= 60.0f)
        return fail("text shaping through the bundled face");

    pulp::canvas::FontOptions options;
    options.family_stack = {"Inter"};
    const auto resolved = pulp::canvas::FontResolver::instance().resolve_family_list(options);
    if (!resolved.has_typeface() || resolved.actual_family != "Inter")
        return fail("font resolution through the package's Skia declarations");

    pulp::runtime::SpscQueue<int, 8> queue;
    int value = 0;
    if (!queue.try_push(41) || !queue.try_pop(value) || value != 41)
        return fail("runtime queue");
    if (pulp::runtime::base64_encode(std::string_view("vellum")) != "dmVsbHVt")
        return fail("runtime base64");

    std::printf("mirror consumer: ok (%s)\n", std::string(pulp::runtime::kStampLabel).c_str());
    return 0;
}
