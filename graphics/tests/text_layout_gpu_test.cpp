#include "../src/skia_dawn_surface_test_access.hpp"
#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

int failures = 0;
bool gpu_missing = false;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

constexpr float kWidth = 320.0F;
constexpr float kHeight = 240.0F;
const Color kBackground = Color::hex(0x0B1020);
const Color kInk = Color::hex(0xF8FAFC);
const std::string kWords = "alpha beta gamma delta epsilon zeta eta theta iota kappa";

std::unique_ptr<SkiaDawnSurface> make_surface(float scale, bool layer_cache = false) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(kWidth),
         .height = static_cast<std::uint32_t>(kHeight),
         .scale = scale},
        &error);
    if (!surface) {
        gpu_missing = true;
        std::cout << "SKIP no GPU surface: " << error << '\n';
        return nullptr;
    }
    if (!layer_cache) {
        LayerCachePolicy policy;
        policy.enabled = false;
        surface->set_layer_cache_policy(policy);
    }
    return surface;
}

struct Capture final {
    bool ok = false;
    std::string error;
    harness::CaptureResult result;
};

Capture capture(SkiaDawnSurface& surface) {
    Capture out;
    out.result.evidence = surface.evidence();
    out.ok = surface.capture_rgba(out.result.rgba, out.result.pixel_width,
                                  out.result.pixel_height, &out.error);
    out.result.ok = out.ok;
    return out;
}

bool identical(SkiaDawnSurface& a, SkiaDawnSurface& b, std::string& message) {
    const auto left = capture(a);
    const auto right = capture(b);
    if (!left.ok || !right.ok) {
        message = left.error + right.error;
        return false;
    }
    const auto verdict = harness::assert_identical(left.result, right.result);
    message = verdict.message;
    return static_cast<bool>(verdict);
}

bool same_pixels(const Capture& a, const Capture& b) {
    return a.ok && b.ok && a.result.pixel_width == b.result.pixel_width &&
           a.result.pixel_height == b.result.pixel_height && a.result.rgba == b.result.rgba;
}

// The device pixels in `area` that differ from the background.
struct Ink final {
    int left = 0;
    int top = 0;
    int right = 0;
    int bottom = 0;
    int count = 0;

    [[nodiscard]] std::string text() const {
        return std::to_string(count) + " px in " + std::to_string(left) + "," +
               std::to_string(top) + " .. " + std::to_string(right) + "," +
               std::to_string(bottom);
    }
};

Ink ink(const Capture& shot, PixelRect area) {
    Ink found{.left = area.right, .top = area.bottom, .right = area.left, .bottom = area.top};
    const auto width = static_cast<int>(shot.result.pixel_width);
    const auto height = static_cast<int>(shot.result.pixel_height);
    for (int y = std::max(0, area.top); y < std::min(height, area.bottom); ++y) {
        for (int x = std::max(0, area.left); x < std::min(width, area.right); ++x) {
            const auto offset = (static_cast<std::size_t>(y) * width + x) * 4U;
            const auto* pixel = &shot.result.rgba[offset];
            if (pixel[0] == kBackground.red8() && pixel[1] == kBackground.green8() &&
                pixel[2] == kBackground.blue8()) {
                continue;
            }
            ++found.count;
            found.left = std::min(found.left, x);
            found.top = std::min(found.top, y);
            found.right = std::max(found.right, x + 1);
            found.bottom = std::max(found.bottom, y + 1);
        }
    }
    return found;
}

const PixelRect kEverywhere{0, 0, 10000, 10000};

Scene empty_scene(std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.background = kBackground;
    scene.scene_version = version;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    return scene;
}

SceneNode text_node(std::string id, Rect bounds, std::string text,
                    std::optional<TextLayout> layout, float size = 16.0F) {
    SceneNode node{.id = std::move(id), .kind = SceneNode::Kind::text, .bounds = bounds,
                   .fill = kInk, .text = std::move(text), .font_size = size};
    node.text_layout = layout;
    return node;
}

Scene one_text(SceneNode node, std::uint32_t version = kSceneVersion) {
    Scene scene = empty_scene(version);
    scene.root.children.push_back(std::move(node));
    return scene;
}

Capture draw(const Scene& scene, float scale = 1.0F, std::string* error = nullptr) {
    auto surface = make_surface(scale);
    if (!surface) return {};
    std::string message;
    if (!surface->render(scene, &message)) {
        if (error != nullptr) *error = message;
        return {.error = message};
    }
    return capture(*surface);
}

TextLayoutMetrics measure(const SceneNode& node, std::uint32_t version = kSceneVersion) {
    TextLayoutMetrics metrics;
    std::string error;
    const std::vector<TextRun> runs = node.text_runs.empty()
        ? std::vector<TextRun>{{.text = node.text,
                                .style = {.font_family = node.font_family,
                                          .font_size = node.font_size,
                                          .color = node.fill}}}
        : node.text_runs;
    if (!SkiaDawnSurface::measure_text(
            runs, {.layout = node.text_layout, .width = node.bounds.width, .scene_version = version},
            metrics, {}, &error)) {
        expect(false, "measure " + node.id, error);
    }
    return metrics;
}

// Text without a layout draws at version 13 exactly as at version 12: every
// path a text command takes (plain, faded, in an effect group, attributed,
// with a line break) and at two scales.
void legacy_identity() {
    const auto corpus = [](std::uint32_t version) {
        Scene scene = empty_scene(version);
        scene.root.children.push_back(
            text_node("latin", {10.0F, 10.0F, 60.0F, 20.0F}, "Latin 0123 ffi office", {}));
        SceneNode runs{.id = "runs", .kind = SceneNode::Kind::text,
                       .bounds = {10.0F, 40.0F, 80.0F, 20.0F}, .fill = kInk};
        runs.text_runs = {
            {.text = "Bold ", .style = {.font_family = "Jost", .font_weight = 700,
                                        .font_size = 18.0F, .color = kInk}},
            {.text = "\xD9\x85\xD8\xB1\xD8\xAD\xD8\xA8\xD8\xA7 ",
             .style = {.font_size = 16.0F, .letter_spacing = 0.5F, .color = kInk,
                       .underline = true}},
            {.text = "\xE6\x97\xA5\xE6\x9C\xAC\xE8\xAA\x9E",
             .style = {.font_size = 15.0F, .color = Color::hex(0x38BDF8),
                       .strikethrough = true}},
        };
        scene.root.children.push_back(std::move(runs));
        auto faded = text_node("faded", {10.0F, 70.0F, 60.0F, 20.0F}, "faded words", {});
        faded.opacity = 0.5F;
        scene.root.children.push_back(std::move(faded));
        SceneNode blurred{.id = "blurred", .kind = SceneNode::Kind::group,
                          .bounds = {0.0F, 100.0F, kWidth, 40.0F}};
        blurred.effects = {{.kind = EffectKind::blur, .sigma = 1.0F}};
        blurred.children.push_back(
            text_node("soft", {10.0F, 10.0F, 60.0F, 20.0F}, "blurred text run", {}));
        if (version >= 10U) scene.root.children.push_back(std::move(blurred));
        scene.root.children.push_back(
            text_node("broken", {10.0F, 150.0F, 60.0F, 20.0F}, "first\nsecond", {}));
        scene.root.children.push_back(
            text_node("spaced", {10.0F, 190.0F, 60.0F, 20.0F}, "s p a c e d", {}, 22.0F));
        return scene;
    };
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = " @" + std::to_string(static_cast<int>(scale));
        const auto old_shot = draw(corpus(12U), scale);
        const auto new_shot = draw(corpus(13U), scale);
        expect(old_shot.ok && new_shot.ok && same_pixels(old_shot, new_shot),
               "text without a layout draws the same at version 13 as at version 12" + at,
               old_shot.error + new_shot.error);
        const auto plain_old = draw(one_text(text_node("t", {10, 10, 50, 20}, "A?B", {}), 12U),
                                    scale);
        const auto rocket_old = draw(
            one_text(text_node("t", {10, 10, 50, 20}, "A\xF0\x9F\x9A\x80" "B", {}), 12U), scale);
        expect(same_pixels(plain_old, rocket_old),
               "a version 12 scene still draws an uncovered character as ?" + at);
    }
}

void wrapping() {
    const Rect box{20.0F, 20.0F, 120.0F, 20.0F};
    const auto wrapped = text_node("para", box, kWords, TextLayout{});
    const auto single = text_node("para", box, kWords, std::nullopt);
    const auto wrapped_shot = draw(one_text(wrapped));
    const auto single_shot = draw(one_text(single));
    const auto drawn = ink(wrapped_shot, kEverywhere);
    const auto line = ink(single_shot, kEverywhere);
    const auto metrics = measure(wrapped);
    expect(drawn.count > 0 && drawn.left >= 19 && drawn.right <= 142 &&
               drawn.bottom - drawn.top > 3 * 16,
           "a laid-out paragraph wraps at its box width", drawn.text());
    expect(line.right > 200 && line.bottom - line.top < 2 * 16,
           "control: the same text without a layout runs past the box on one line", line.text());
    expect(metrics.lines >= 4U && metrics.metrics.width <= 120.0F &&
               std::abs(metrics.metrics.ascent + metrics.metrics.descent -
                        static_cast<float>(drawn.bottom - drawn.top)) < 12.0F,
           "measure_text reports the wrapped lines and height",
           std::to_string(metrics.lines) + " lines, width " + std::to_string(metrics.metrics.width));
    const auto narrow = text_node("para", {20.0F, 20.0F, 60.0F, 20.0F}, kWords, TextLayout{});
    expect(measure(narrow).lines > metrics.lines,
           "a narrower box wraps onto more lines");
}

void alignment() {
    const float x = 40.0F;
    const float width = 200.0F;
    const auto word = [&](TextAlign align) {
        const auto shot =
            draw(one_text(text_node("word", {x, 40.0F, width, 20.0F}, "Hi", TextLayout{.align = align})));
        return ink(shot, kEverywhere);
    };
    const auto start = word(TextAlign::start);
    const auto center = word(TextAlign::center);
    const auto end = word(TextAlign::end);
    expect(start.left >= 40 && start.left <= 43, "start alignment sits at the left edge",
           start.text());
    const float middle = 0.5F * static_cast<float>(center.left + center.right);
    expect(std::abs(middle - (x + width * 0.5F)) <= 2.0F, "center alignment centres the line",
           center.text());
    expect(end.right <= 241 && end.right >= 237, "end alignment sits at the right edge",
           end.text());
    expect(start.count == center.count && center.count == end.count,
           "alignment moves the glyphs without changing them");

    const Rect box{40.0F, 40.0F, 150.0F, 20.0F};
    const std::string text = "aa bb cc dd ee ff gg hh ii jj kk";
    const auto first_line = [&](TextAlign align) {
        const auto shot = draw(one_text(
            text_node("para", box, text, TextLayout{.align = align, .line_height = 1.5F})));
        return ink(shot, {0, 40, 320, 40 + 24});
    };
    const auto justified = first_line(TextAlign::justify);
    const auto ragged = first_line(TextAlign::start);
    expect(justified.right >= 187 && justified.right <= 191 && justified.left <= 42,
           "justify stretches a full line to both edges", justified.text());
    expect(ragged.right < 185, "control: start alignment leaves the same line ragged",
           ragged.text());
    const auto lines =
        measure(text_node("para", box, text, TextLayout{.line_height = 1.5F})).lines;
    expect(lines >= 2U, "the justified paragraph spans several lines", std::to_string(lines));
    const int last_top = 40 + 24 * static_cast<int>(lines - 1U);
    const auto last_lines = [&](TextAlign align) {
        const auto shot = draw(one_text(
            text_node("para", box, text, TextLayout{.align = align, .line_height = 1.5F})));
        return ink(shot, {0, last_top, 320, last_top + 24});
    };
    const auto justified_last = last_lines(TextAlign::justify);
    const auto start_last = last_lines(TextAlign::start);
    expect(justified_last.count > 0 && justified_last.right == start_last.right,
           "justify leaves the last line at the start edge",
           justified_last.text() + " vs " + start_last.text());
}

void line_height() {
    const Rect box{20.0F, 20.0F, 120.0F, 20.0F};
    const auto lines_at = [&](float height) {
        return measure(text_node("para", box, kWords, TextLayout{.line_height = height}));
    };
    const auto natural = lines_at(0.0F);
    const auto tight = lines_at(1.0F);
    const auto loose = lines_at(2.0F);
    const auto height = [](const TextLayoutMetrics& m) {
        return m.metrics.ascent + m.metrics.descent;
    };
    expect(tight.lines == loose.lines && tight.lines == natural.lines &&
               std::abs(height(tight) - static_cast<float>(tight.lines) * 16.0F) <
                   1.0F + 0.25F * static_cast<float>(tight.lines) &&
               std::abs(height(loose) - static_cast<float>(loose.lines) * 32.0F) <
                   1.0F + 0.25F * static_cast<float>(loose.lines),
           "line height sets each line to that multiple of the font size",
           std::to_string(height(tight)) + " and " + std::to_string(height(loose)) + " over " +
               std::to_string(tight.lines) + " lines");
    const auto tight_shot =
        draw(one_text(text_node("para", box, kWords, TextLayout{.line_height = 1.0F})));
    const auto loose_shot =
        draw(one_text(text_node("para", box, kWords, TextLayout{.line_height = 2.0F})));
    const auto tight_ink = ink(tight_shot, kEverywhere);
    const auto loose_ink = ink(loose_shot, kEverywhere);
    const float span = static_cast<float>(loose_ink.bottom - tight_ink.bottom);
    expect(span >= 16.0F * static_cast<float>(tight.lines - 1U) - 3.0F &&
               span <= 16.0F * static_cast<float>(tight.lines) + 3.0F,
           "a doubled line height pushes the last line down by a line per line",
           tight_ink.text() + " vs " + loose_ink.text());
    for (const auto* sample : {"Hg", "\xD9\x85\xD8\xB1\xD8\xAD\xD8\xA8\xD8\xA7",
                               "\xE6\x97\xA5\xE6\x9C\xAC", "Hg\xD9\x85\xE6\x97\xA5"}) {
      for (const auto* family : {"Inter", "Jost", "Noto Sans JP", "Noto Sans Arabic"}) {
        TextLayoutMetrics metrics;
        std::string error;
        const bool measured = SkiaDawnSurface::measure_text(
            {{.text = sample, .style = {.font_family = family, .font_size = 20.0F}}},
            {.layout = TextLayout{}, .width = 1000.0F}, metrics, {}, &error);
        expect(measured && metrics.lines == 1U &&
                   metrics.metrics.ascent + metrics.metrics.descent <=
                       kTextNaturalLineHeightBound * 20.0F,
               std::string("the packaged ") + family +
                   " line fits the damage model's natural line height bound",
               std::to_string(metrics.metrics.ascent + metrics.metrics.descent) + error);
      }
    }
}

// The damage of a line-limited paragraph reaches past its last line however
// the shaper rounds each line, for fractional line heights, mixed scripts and
// the fonts' own spacing.
void line_limit_damage() {
    std::string lines;
    for (int line = 0; line < 120; ++line) {
        if (line != 0) lines += "\n";
        lines += line % 3 == 0 ? "A" : line % 3 == 1 ? "\xD9\x85" : "\xE6\x97\xA5";
    }
    int checked = 0;
    std::string first;
    for (const float size : {11.0F, 13.0F, 15.0F, 17.0F}) {
        for (const float height : {0.0F, 1.04F, 1.1F, 1.33F, 1.5F}) {
            const TextLayout layout{.line_height = height, .max_lines = 120U};
            const auto node = text_node("para", {10.0F, 10.0F, 200.0F, 10.0F}, lines, layout, size);
            const auto measured = measure(node);
            Scene before = one_text(node);
            before.height = 10000.0F;
            Scene after = before;
            after.root.children[0].text_layout->align = TextAlign::end;
            const auto damage = compute_scene_damage(before, after, 1.0F);
            const float bottom = 10.0F + measured.metrics.ascent + measured.metrics.descent;
            ++checked;
            if (measured.lines != 120U || damage.empty() ||
                static_cast<float>(damage.bounds().bottom) < bottom + size) {
                if (first.empty()) {
                    first = std::to_string(size) + "px x" + std::to_string(height) + ": " +
                            std::to_string(damage.bounds().bottom) + " < " +
                            std::to_string(bottom + size);
                }
            }
        }
    }
    expect(first.empty() && checked == 20,
           "a 120-line paragraph's damage reaches a font size past its last line at every "
           "fractional line height",
           first);
}

void line_limit() {
    const Rect box{20.0F, 20.0F, 120.0F, 20.0F};
    const TextLayout two{.line_height = 1.5F, .max_lines = 2U};
    TextLayout ellipsized = two;
    ellipsized.ellipsis = true;
    const auto open = measure(text_node("para", box, kWords, TextLayout{.line_height = 1.5F}));
    const auto cut = measure(text_node("para", box, kWords, two));
    const auto dotted = measure(text_node("para", box, kWords, ellipsized));
    expect(open.lines > 2U && cut.lines == 2U && dotted.lines == 2U &&
               std::abs(cut.metrics.ascent + cut.metrics.descent - 48.0F) < 1.0F,
           "max_lines stops the paragraph at two lines",
           std::to_string(open.lines) + " open, " + std::to_string(cut.lines) + " cut");
    const auto open_shot =
        draw(one_text(text_node("para", box, kWords, TextLayout{.line_height = 1.5F})));
    const auto cut_shot = draw(one_text(text_node("para", box, kWords, two)));
    const auto dotted_shot = draw(one_text(text_node("para", box, kWords, ellipsized)));
    const PixelRect below{0, 20 + 48 + 4, 320, 240};
    expect(ink(cut_shot, below).count == 0 && ink(dotted_shot, below).count == 0,
           "nothing is drawn past the last allowed line");
    expect(ink(open_shot, below).count > 0,
           "control: the same paragraph without a limit draws below two lines");
    const auto dotted_ink = ink(dotted_shot, kEverywhere);
    expect(!same_pixels(cut_shot, dotted_shot) && dotted_ink.right <= 141,
           "an ellipsis ends the cut line within the box", dotted_ink.text());
    const auto second_line = [](const Capture& shot) { return ink(shot, {0, 44, 320, 68}); };
    const auto dotted_last = second_line(dotted_shot);
    const auto plain_last = second_line(cut_shot);
    expect(dotted_last.count != plain_last.count,
           "the ellipsis changes the last line rather than adding one",
           dotted_last.text() + " vs " + plain_last.text());
    const auto ellipsis_only = draw(one_text(
        text_node("dots", {20.0F, 20.0F, 120.0F, 20.0F}, "\xE2\x80\xA6", TextLayout{})));
    expect(ink(ellipsis_only, kEverywhere).count > 0, "the packaged faces draw the ellipsis");
}

bool has_code(const std::vector<LoweringDiagnostic>& diagnostics, std::string_view code) {
    return std::any_of(diagnostics.begin(), diagnostics.end(),
                       [&](const LoweringDiagnostic& d) { return d.code == code; });
}

void mixed_scripts() {
    SceneNode node{.id = "mixed", .kind = SceneNode::Kind::text,
                   .bounds = {20.0F, 20.0F, 160.0F, 20.0F}, .fill = kInk};
    node.text_runs = {
        {.text = "Hello ", .style = {.font_size = 16.0F, .color = kInk}},
        {.text = "\xD9\x85\xD8\xB1\xD8\xAD\xD8\xA8\xD8\xA7 \xD8\xA8\xD8\xA7\xD9\x84\xD8\xB9"
                 "\xD8\xA7\xD9\x84\xD9\x85 ",
         .style = {.font_family = "Jost", .font_size = 16.0F, .color = kInk}},
        {.text = "\xE3\x81\x93\xE3\x82\x93\xE3\x81\xAB\xE3\x81\xA1\xE3\x81\xAF\xE4\xB8\x96"
                 "\xE7\x95\x8C",
         .style = {.font_size = 16.0F, .color = kInk}},
    };
    node.text_layout = TextLayout{.align = TextAlign::start};
    auto surface = make_surface(1.0F);
    if (!surface) return;
    std::string error;
    const auto scene = one_text(node);
    expect(surface->render(scene, &error), "a mixed-script paragraph renders", error);
    expect(!has_code(surface->diagnostics(), kTextGlyphMissingCode),
           "Latin, Arabic and Japanese all resolve to packaged faces");
    const auto commands = make_paint_commands(scene);
    const auto unresolved =
        SkiaDawnSurfaceTestAccess::unresolved_code_points(commands.back(), {}, &error);
    expect(unresolved.empty(), "the shaper finds a face for every mixed-script character",
           std::to_string(unresolved.size()) + " unresolved");
    const auto shot = capture(*surface);
    const auto drawn = ink(shot, kEverywhere);
    expect(drawn.count > 0 && drawn.right <= 182 && drawn.bottom - drawn.top > 24,
           "the mixed-script paragraph wraps within its box", drawn.text());
}

void missing_glyphs() {
    const Rect box{20.0F, 20.0F, 120.0F, 20.0F};
    const std::string rocket = "A\xF0\x9F\x9A\x80" "B";
    for (const bool laid_out : {false, true}) {
        const std::optional<TextLayout> layout =
            laid_out ? std::optional<TextLayout>(TextLayout{}) : std::nullopt;
        const std::string how = laid_out ? " (laid out)" : " (one line)";
        auto surface = make_surface(1.0F);
        if (!surface) return;
        std::string error;
        const auto scene = one_text(text_node("rocket", box, rocket, layout));
        expect(surface->render(scene, &error), "text with an uncovered character renders" + how,
               error);
        const auto& diagnostics = surface->diagnostics();
        const auto found = std::find_if(
            diagnostics.begin(), diagnostics.end(),
            [](const LoweringDiagnostic& d) { return d.code == kTextGlyphMissingCode; });
        expect(found != diagnostics.end() && found->node_id == "rocket" &&
                   found->severity == LoweringDiagnostic::Severity::warning &&
                   found->message.find("U+1F680") != std::string::npos &&
                   found->message.find("missing-glyph box") != std::string::npos &&
                   found->property == "text",
               "the uncovered code point is reported by name" + how,
               found == diagnostics.end() ? "none" : found->message);
        const auto boxed = capture(*surface);
        const auto question = draw(one_text(text_node("rocket", box, "A?B", layout)));
        const auto bare = draw(one_text(text_node("rocket", box, "AB", layout)));
        expect(!same_pixels(boxed, question), "the uncovered character is not drawn as ?" + how);
        expect(ink(boxed, kEverywhere).count > ink(bare, kEverywhere).count + 20,
               "the missing-glyph box has ink" + how,
               ink(boxed, kEverywhere).text() + " vs " + ink(bare, kEverywhere).text());
        const auto commands = make_paint_commands(scene);
        const auto unresolved =
            SkiaDawnSurfaceTestAccess::unresolved_code_points(commands.back(), {}, &error);
        expect(unresolved == std::vector<std::uint32_t>{0x1F680U},
               "the shaper resolves no face for exactly the reported code point" + how);
        expect(surface->render(one_text(text_node("rocket", box, "AB", layout)), &error) &&
                   !has_code(surface->diagnostics(), kTextGlyphMissingCode),
               "covered text reports nothing" + how);
    }
    auto legacy = make_surface(1.0F);
    if (!legacy) return;
    std::string error;
    expect(legacy->render(one_text(text_node("rocket", box, rocket, std::nullopt), 12U), &error),
           "a version 12 scene with an uncovered character renders", error);
    const auto old = std::find_if(
        legacy->diagnostics().begin(), legacy->diagnostics().end(),
        [](const LoweringDiagnostic& d) { return d.code == kTextGlyphMissingCode; });
    expect(old != legacy->diagnostics().end() &&
               old->message.find("U+1F680") != std::string::npos &&
               old->message.find("drawn as ?") != std::string::npos,
           "a version 12 scene reports the code point it draws as ?");
    SceneNode several{.id = "several", .kind = SceneNode::Kind::text, .bounds = box, .fill = kInk};
    several.text_runs = {{.text = "\xF0\x9F\x98\x80 x \xF0\x9F\x9A\x80", .style = {.color = kInk}},
                         {.text = "\xF0\x9F\x9A\x80", .style = {.color = kInk}}};
    expect(legacy->render(one_text(several), &error), "several uncovered characters render",
           error);
    const auto listed = std::find_if(
        legacy->diagnostics().begin(), legacy->diagnostics().end(),
        [](const LoweringDiagnostic& d) { return d.code == kTextGlyphMissingCode; });
    expect(listed != legacy->diagnostics().end() &&
               listed->message ==
                   "text several has no packaged glyph for U+1F600, U+1F680; drawn as the "
                   "missing-glyph box",
           "each code point is named once, in ascending order",
           listed == legacy->diagnostics().end() ? "none" : listed->message);
    const std::vector<std::uint32_t> tricky{0x00ADU, 0x034FU, 0x0E01U, 0x180EU, 0x200BU,
                                            0x2061U, 0xFEFFU, 0x10400U, 0x1F680U, 0xE0061U};
    std::string mixed = "A";
    for (const auto code_point : tricky) {
        if (code_point < 0x800U) {
            mixed += static_cast<char>(0xC0U | (code_point >> 6U));
            mixed += static_cast<char>(0x80U | (code_point & 0x3FU));
        } else if (code_point < 0x10000U) {
            mixed += static_cast<char>(0xE0U | (code_point >> 12U));
            mixed += static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU));
            mixed += static_cast<char>(0x80U | (code_point & 0x3FU));
        } else {
            mixed += static_cast<char>(0xF0U | (code_point >> 18U));
            mixed += static_cast<char>(0x80U | ((code_point >> 12U) & 0x3FU));
            mixed += static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU));
            mixed += static_cast<char>(0x80U | (code_point & 0x3FU));
        }
        mixed += "B";
    }
    for (const bool laid_out : {false, true}) {
        const auto scene = one_text(text_node(
            "tricky", box, mixed, laid_out ? std::optional<TextLayout>(TextLayout{}) : std::nullopt));
        std::vector<std::uint32_t> reported;
        for (int frame = 0; frame < 2; ++frame) {
            if (!legacy->render(scene, &error)) {
                expect(false, "the tricky text renders", error);
                return;
            }
            reported.clear();
            for (const auto& diagnostic : legacy->diagnostics()) {
                if (diagnostic.code != kTextGlyphMissingCode) continue;
                for (std::size_t at = diagnostic.message.find("U+"); at != std::string::npos;
                     at = diagnostic.message.find("U+", at + 2U)) {
                    reported.push_back(static_cast<std::uint32_t>(
                        std::stoul(diagnostic.message.substr(at + 2U), nullptr, 16)));
                }
            }
        }
        const auto unresolved = SkiaDawnSurfaceTestAccess::unresolved_code_points(
            make_paint_commands(scene).back(), {}, &error);
        std::string listed;
        for (const auto code_point : reported) listed += std::to_string(code_point) + " ";
        listed += "vs ";
        for (const auto code_point : unresolved) listed += std::to_string(code_point) + " ";
        expect(!reported.empty() && reported == unresolved,
               std::string("the diagnostic names exactly the code points the shaper draws as "
                           "boxes, frame after frame, leaving out default-ignorable ones") +
                   (laid_out ? " (laid out)" : " (one line)"),
               listed);
    }
    const auto invalid = draw(one_text(text_node("bytes", box, "A\xFF" "B", TextLayout{})));
    const auto replaced = draw(one_text(text_node("bytes", box, "A?B", TextLayout{})));
    expect(same_pixels(invalid, replaced), "an invalid UTF-8 byte still draws as ?");
}

// Draws `scene` on two surfaces and compares a partial redraw of each damage
// edge swept across the frame with the full frame.
struct SweepCount final {
    int differing = 0;
    int partial = 0;
    int edges = 0;
    std::string first;
};

SweepCount sweep_damage_edges(const Scene& scene, float scale) {
    SweepCount count;
    auto full = make_surface(scale);
    auto partial = make_surface(scale, true);
    if (!full || !partial) return {.differing = -1};
    std::string error;
    if (!full->render(scene, &error)) return {.differing = -1, .first = error};
    const PixelRect frame = frame_rect(scene, scale);
    for (const bool vertical : {true, false}) {
        const std::int32_t extent = vertical ? frame.right : frame.bottom;
        for (std::int32_t edge = 1; edge < extent; edge += 2) {
            DamageRegion damage(frame, {.full_repaint_fraction = 2.0});
            damage.add(vertical ? PixelRect{edge, 0, frame.right, frame.bottom}
                                : PixelRect{0, edge, frame.right, frame.bottom});
            if (!partial->render(scene, &error) || !partial->render(scene, damage, &error)) {
                return {.differing = -1, .first = error};
            }
            ++count.edges;
            if (!partial->frame_stats().full_repaint) ++count.partial;
            std::string message;
            if (!identical(*full, *partial, message)) {
                if (count.differing == 0) {
                    count.first = std::string(vertical ? "x=" : "y=") + std::to_string(edge) +
                                  ": " + message;
                }
                ++count.differing;
            }
        }
    }
    return count;
}

std::string sweep_detail(const SweepCount& count) {
    return std::to_string(count.differing) + " of " + std::to_string(count.edges) +
           " edges differ, " + std::to_string(count.partial) + " partial; first " + count.first;
}

Scene sweep_scene() {
    Scene scene = empty_scene();
    scene.root.children.push_back(
        {.id = "panel", .kind = SceneNode::Kind::rectangle, .bounds = {150.0F, 10.0F, 150.0F, 60.0F},
         .fill = Color::hex(0x1E293B), .corner_radius = 6.0F});
    scene.root.children.push_back(text_node("start", {10.0F, 10.0F, 110.0F, 18.0F}, kWords,
                                            TextLayout{.line_height = 1.25F, .max_lines = 3U}));
    scene.root.children.push_back(
        text_node("center", {160.0F, 20.0F, 130.0F, 18.0F}, kWords,
                  TextLayout{.align = TextAlign::center, .max_lines = 2U, .ellipsis = true}));
    scene.root.children.push_back(text_node(
        "end", {10.0F, 100.0F, 90.0F, 18.0F}, "end aligned words",
        TextLayout{.align = TextAlign::end, .line_height = 1.6F, .max_lines = 2U}));
    auto justified = text_node("justify", {150.0F, 110.0F, 140.0F, 18.0F}, kWords,
                               TextLayout{.align = TextAlign::justify, .max_lines = 3U}, 14.0F);
    justified.opacity = 0.6F;
    scene.root.children.push_back(std::move(justified));
    scene.root.children.push_back(
        text_node("wide", {20.0F, 200.0F, 8.0F, 18.0F}, "Wwide", TextLayout{.align = TextAlign::center,
                                                                     .max_lines = 2U}));
    return scene;
}

void sweeps() {
    for (const float scale : {1.0F, 2.0F}) {
        const auto count = sweep_damage_edges(sweep_scene(), scale);
        expect(count.differing == 0 && count.partial > 0,
               "a damage edge swept across wrapped, aligned and truncated text leaves every "
               "frame byte-identical @" + std::to_string(static_cast<int>(scale)),
               sweep_detail(count));
    }
}

SceneNode* find_mutable(SceneNode& node, const std::string& id) {
    if (node.id == id) return &node;
    for (auto& child : node.children) {
        if (auto* found = find_mutable(child, id); found != nullptr) return found;
    }
    return nullptr;
}

// A layer holding laid-out text covers every line: a centred cluster wider
// than its box overflows to the right, and wrapped lines reach below the box.
void layer_extents() {
    const Rect box{100.0F, 40.0F, 20.0F, 20.0F};
    PaintCommand command{.kind = PaintCommand::Kind::text, .node_id = "wide", .bounds = box,
                         .fill = kInk, .font_size = 16.0F};
    command.text_runs = {{.text = "WW", .style = {.font_size = 16.0F, .letter_spacing = 40.0F,
                                                  .color = kInk}}};
    command.text_layout = TextLayout{.align = TextAlign::center};
    command.missing_glyph_box = true;
    std::string error;
    const auto centred = SkiaDawnSurfaceTestAccess::measure_layer_text_extent(command, {}, &error);
    TextLayoutMetrics wide;
    const bool measured = SkiaDawnSurface::measure_text(
        command.text_runs, {.layout = command.text_layout, .width = box.width}, wide, {}, &error);
    expect(measured && wide.metrics.width > box.width + 20.0F && centred &&
               centred->x == box.x - 16.0F &&
               centred->x + centred->width >= box.x + wide.metrics.width + 16.0F,
           "a centred cluster wider than its box widens the layer to the right",
           centred ? std::to_string(centred->x) + " + " + std::to_string(centred->width) +
                         " for a line of " + std::to_string(wide.metrics.width)
                   : error);
    command.text_layout.reset();
    const auto single = SkiaDawnSurfaceTestAccess::measure_layer_text_extent(command, {}, &error);
    expect(single && single->x == box.x - 16.0F,
           "control: the one-line extent starts one font size left of the box",
           single ? std::to_string(single->x) : error);
    command.text_layout = TextLayout{.line_height = 2.0F};
    const auto tall = SkiaDawnSurfaceTestAccess::measure_layer_text_extent(command, {}, &error);
    expect(tall && tall->y + tall->height >= box.y + 2.0F * 32.0F + 16.0F,
           "the layer extent reaches below the last wrapped line",
           tall ? std::to_string(tall->y + tall->height) : error);
}

// One surface drawing the same runs under each layout, width, version and
// opacity in turn draws what a fresh surface draws, so no cached paragraph or
// layer image survives a change to any of them.
void cache_keys() {
    const Rect box{20.0F, 20.0F, 120.0F, 20.0F};
    const std::string text = "\xF0\x9F\x9A\x80 " + kWords;
    std::vector<Scene> variants;
    const auto add = [&](std::optional<TextLayout> layout, float width,
                         std::uint32_t version = kSceneVersion, float opacity = 1.0F) {
        auto node = text_node("para", {box.x, box.y, width, box.height}, text, layout);
        node.opacity = opacity;
        variants.push_back(one_text(std::move(node), version));
    };
    add(std::nullopt, 120.0F, 12U);
    add(std::nullopt, 120.0F);
    add(TextLayout{}, 120.0F);
    add(TextLayout{}, 90.0F);
    add(TextLayout{.align = TextAlign::center}, 90.0F);
    add(TextLayout{.align = TextAlign::end}, 90.0F);
    add(TextLayout{.align = TextAlign::justify}, 90.0F);
    add(TextLayout{.align = TextAlign::justify, .line_height = 1.75F}, 90.0F);
    add(TextLayout{.align = TextAlign::justify, .line_height = 1.75F, .max_lines = 2U}, 90.0F);
    add(TextLayout{.align = TextAlign::justify, .line_height = 1.75F, .max_lines = 2U,
                   .ellipsis = true},
        90.0F);
    add(TextLayout{.max_lines = 2U, .ellipsis = true}, 90.0F, kSceneVersion, 0.5F);
    add(TextLayout{.max_lines = 2U, .ellipsis = true}, 90.0F);
    add(std::nullopt, 90.0F, 12U, 0.5F);
    for (const bool layer_cache : {false, true}) {
        auto reused = make_surface(1.0F, layer_cache);
        if (!reused) return;
        int differing = 0;
        std::string first;
        for (int pass = 0; pass < 2; ++pass) {
            for (std::size_t index = 0; index < variants.size(); ++index) {
                std::string error;
                if (!reused->render(variants[index], &error)) {
                    expect(false, "a cache variant renders", error);
                    return;
                }
                const auto again = capture(*reused);
                const auto fresh = draw(variants[index]);
                if (!same_pixels(again, fresh)) {
                    if (differing == 0) first = "variant " + std::to_string(index);
                    ++differing;
                }
            }
        }
        expect(differing == 0,
               std::string("a reused surface draws every layout, width, version and opacity as a "
                           "fresh one") + (layer_cache ? " with the layer cache" : ""),
               std::to_string(differing) + " differ; " + first);
    }
    std::string message;
    expect(!same_pixels(draw(variants[0]), draw(variants[1])),
           "control: the cache variants at versions 12 and 13 do draw differently");
}

// Layout edits through `render_incremental` match a full redraw, and at least
// some of them take the partial path.
void layout_updates() {
    for (const float scale : {1.0F, 2.0F}) {
        const std::string at = " @" + std::to_string(static_cast<int>(scale));
        auto full = make_surface(scale);
        auto partial = make_surface(scale, true);
        if (!full || !partial) return;
        Scene scene = sweep_scene();
        std::string error;
        if (!full->render(scene, &error) || !partial->render_incremental(scene, &error)) {
            expect(false, "the update scene renders" + at, error);
            return;
        }
        const std::vector<std::function<void(Scene&)>> edits{
            [](Scene& s) { find_mutable(s.root, "end")->text_layout->align = TextAlign::center; },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout->align = TextAlign::justify; },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout->align = TextAlign::start; },
            [](Scene& s) { find_mutable(s.root, "end")->bounds.width = 60.0F; },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout->max_lines = 1U; },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout->line_height = 2.2F; },
            [](Scene& s) { find_mutable(s.root, "justify")->text_layout->align = TextAlign::end; },
            [](Scene& s) { find_mutable(s.root, "justify")->bounds.width = 100.0F; },
            [](Scene& s) { find_mutable(s.root, "justify")->text_layout->max_lines = 1U; },
            [](Scene& s) {
                auto& layout = *find_mutable(s.root, "justify")->text_layout;
                layout.ellipsis = true;
            },
            [](Scene& s) { find_mutable(s.root, "wide")->text_layout->align = TextAlign::end; },
            [](Scene& s) { find_mutable(s.root, "wide")->text = "Wider words"; },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout.reset(); },
            [](Scene& s) { find_mutable(s.root, "end")->text_layout = TextLayout{.max_lines = 2U}; },
        };
        int differing = 0;
        int partial_frames = 0;
        std::string first;
        for (std::size_t step = 0; step < edits.size(); ++step) {
            edits[step](scene);
            if (!full->render(scene, &error) || !partial->render_incremental(scene, &error)) {
                expect(false, "an edited scene renders" + at, error);
                return;
            }
            if (!partial->frame_stats().full_repaint) ++partial_frames;
            std::string message;
            if (!identical(*full, *partial, message)) {
                if (differing == 0) first = "step " + std::to_string(step) + ": " + message;
                ++differing;
            }
        }
        expect(differing == 0 && partial_frames > 0,
               "wrap, alignment, line height and line limit edits redraw byte-identically from "
               "damage" + at,
               std::to_string(differing) + " differ, " + std::to_string(partial_frames) +
                   " partial; " + first);
    }

    // Damaging only the node's own box misses the lines a laid-out paragraph
    // draws below it, so the frame is left stale.
    auto full = make_surface(1.0F);
    auto partial = make_surface(1.0F);
    if (!full || !partial) return;
    const Rect box{20.0F, 40.0F, 120.0F, 18.0F};
    Scene before = one_text(text_node("para", box, kWords, TextLayout{.line_height = 1.5F}));
    Scene after = before;
    after.root.children[0].text_layout->align = TextAlign::end;
    std::string error;
    if (!full->render(after, &error) || !partial->render(before, &error)) return;
    const PixelRect frame = frame_rect(after, 1.0F);
    DamageRegion boxed(frame, {.full_repaint_fraction = 2.0});
    boxed.add({20, 40, 140, 58});
    expect(partial->render(after, boxed, &error) && !partial->frame_stats().full_repaint,
           "negative control: a box-only damage takes the partial path", error);
    std::string message;
    expect(!identical(*full, *partial, message),
           "negative control: damage limited to the box leaves the wrapped lines stale");
    const auto modelled =
        compute_scene_damage(before, after, 1.0F, {.full_repaint_fraction = 2.0});
    DamageRegion region(frame, {.full_repaint_fraction = 2.0});
    for (const auto& rect : modelled.rects()) region.add(rect);
    if (!partial->render(before, &error) || !partial->render(after, region, &error)) return;
    expect(!partial->frame_stats().full_repaint && identical(*full, *partial, message),
           "the modelled damage of the same change covers the wrapped lines", message);
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "FAIL device bootstrap: " << error << '\n';
        return 1;
    }
    legacy_identity();
    if (gpu_missing) return 77;
    wrapping();
    alignment();
    line_height();
    line_limit();
    line_limit_damage();
    mixed_scripts();
    missing_glyphs();
    layer_extents();
    cache_keys();
    sweeps();
    layout_updates();
    if (failures != 0) {
        std::cerr << failures << " text layout GPU check(s) failed\n";
        return 1;
    }
    std::cout << "text layout GPU checks passed\n";
    return 0;
}
