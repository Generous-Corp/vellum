#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

std::string bounds_of(const DamageRegion& damage) {
    if (damage.empty()) return "empty";
    const auto box = damage.bounds();
    return std::to_string(box.left) + "," + std::to_string(box.top) + " " +
           std::to_string(box.right) + "," + std::to_string(box.bottom);
}

constexpr float kWidth = 320.0F;
constexpr float kHeight = 240.0F;
const Rect kBox{40.0F, 60.0F, 100.0F, 20.0F};

Scene text_scene(std::optional<TextLayout> layout, std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = kWidth;
    scene.height = kHeight;
    scene.scene_version = version;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, kWidth, kHeight}};
    scene.root.children.push_back({.id = "para", .kind = SceneNode::Kind::text, .bounds = kBox,
                                   .fill = Color::hex(0xF8FAFC),
                                   .text = "alpha beta gamma delta epsilon zeta eta theta",
                                   .font_size = 16.0F});
    scene.root.children.back().text_layout = layout;
    return scene;
}

void versioning() {
    static_assert(kSceneVersion == 13U);
    static_assert(kDamageModelSceneVersion == 13U);
    static_assert(partial_redraw_supported(13U));
    static_assert(!draws_missing_glyph_box(12U) && draws_missing_glyph_box(13U));
    const TextLayout layout{.align = TextAlign::center, .line_height = 1.5F, .max_lines = 2U,
                            .ellipsis = true};
    expect(accept_scene(text_scene(layout)).accepted,
           "a version 13 scene with a text layout is accepted");
    for (std::uint32_t version = kMinSupportedSceneVersion; version < 13U; ++version) {
        const auto scene = text_scene(layout, version);
        const auto refused = accept_scene(scene);
        const auto mismatch = find_scene_version_mismatch(scene);
        expect(!refused.accepted && refused.diagnostics.size() == 1U &&
                   refused.diagnostics[0].code == kSceneVersionMismatchCode &&
                   refused.diagnostics[0].node_id == "para" &&
                   refused.diagnostics[0].property == "text_layout" &&
                   refused.reason == "scene version " + std::to_string(version) +
                                         " cannot carry the text layout of para" &&
                   mismatch && mismatch->introduced == 13U && mismatch->property == "text_layout" &&
                   !scene_fields_match_version(scene) && make_paint_commands(scene).empty(),
               "a version " + std::to_string(version) + " scene with a text layout is refused",
               refused.reason);
        expect(accept_scene(text_scene(std::nullopt, version)).accepted,
               "a version " + std::to_string(version) + " scene without a text layout is accepted");
    }
    Scene nested = text_scene(std::nullopt, 12U);
    nested.root.children.push_back({.id = "group", .kind = SceneNode::Kind::group});
    nested.root.children.back().children.push_back(
        {.id = "deep", .kind = SceneNode::Kind::text, .text = "x"});
    nested.root.children.back().children.back().text_layout = TextLayout{};
    const auto deep = find_scene_version_mismatch(nested);
    expect(deep && deep->node && deep->node->id == "deep" && deep->introduced == 13U,
           "a default text layout deep in a version 12 scene is still a version 13 field");
}

void invalid_layouts() {
    const auto refused_for = [](Scene scene, const std::string& reason, const std::string& what) {
        const auto acceptance = accept_scene(scene);
        expect(!acceptance.accepted &&
                   acceptance.reason == "the text layout of para cannot be drawn: " + reason &&
                   acceptance.diagnostics.size() == 1U &&
                   acceptance.diagnostics[0].code == kInvalidTextLayoutCode &&
                   acceptance.diagnostics[0].node_id == "para" &&
                   acceptance.diagnostics[0].severity == LoweringDiagnostic::Severity::error,
               what, acceptance.reason);
    };
    refused_for(text_scene(TextLayout{.ellipsis = true}), "an ellipsis needs max_lines",
                "an ellipsis without max_lines is refused, not ignored");
    refused_for(text_scene(TextLayout{.line_height = -1.0F}),
                "its line height is negative or not finite", "a negative line height is refused");
    refused_for(text_scene(TextLayout{.line_height = std::numeric_limits<float>::quiet_NaN()}),
                "its line height is negative or not finite", "a NaN line height is refused");
    refused_for(text_scene(TextLayout{.line_height = std::numeric_limits<float>::infinity()}),
                "its line height is negative or not finite", "an infinite line height is refused");
    refused_for(text_scene(TextLayout{.align = static_cast<TextAlign>(9)}),
                "its alignment is not one this build knows", "an unknown alignment is refused");
    Scene boxed = text_scene(TextLayout{});
    boxed.root.children[0].kind = SceneNode::Kind::rectangle;
    refused_for(boxed, "only a text node carries a text layout",
                "a text layout on a rectangle is refused");
    expect(accept_scene(text_scene(TextLayout{.line_height = 0.0F, .max_lines = 1U,
                                              .ellipsis = true}))
               .accepted,
           "an ellipsis with max_lines and the fonts' own line height is accepted");
}

void lowering() {
    const TextLayout layout{.align = TextAlign::justify, .max_lines = 3U};
    const auto current = make_paint_commands(text_scene(layout));
    const auto legacy = make_paint_commands(text_scene(std::nullopt, 12U));
    const auto plain = make_paint_commands(text_scene(std::nullopt));
    expect(current.size() == 1U && current[0].text_layout &&
               current[0].text_layout->align == TextAlign::justify &&
               current[0].text_layout->max_lines == 3U && current[0].missing_glyph_box,
           "a version 13 text command carries the layout and draws missing glyphs as boxes");
    expect(legacy.size() == 1U && !legacy[0].text_layout && !legacy[0].missing_glyph_box,
           "a version 12 text command keeps the question-mark policy");
    expect(plain.size() == 1U && !plain[0].text_layout && plain[0].missing_glyph_box,
           "a version 13 text command without a layout still draws missing glyphs as boxes");
    Scene animated = text_scene(layout);
    animated.root.children[0].tracks.push_back(
        {.property = AnimatedProperty::opacity,
         .keyframes = {{.time = 0.0, .value = 1.0F}, {.time = 1.0, .value = 0.5F}}});
    const auto evaluated = make_paint_commands(animated);
    expect(!evaluated.empty() && evaluated.back().missing_glyph_box &&
               evaluated.back().text_layout,
           "a text command with tracks keeps the layout and the missing-glyph policy");
}

void damage() {
    const TextLayout start{.line_height = 1.25F, .max_lines = 2U};
    TextLayout centered = start;
    centered.align = TextAlign::center;
    const Scene before = text_scene(start);
    const Scene after = text_scene(centered);
    expect(paint_differs(before.root.children[0], after.root.children[0]),
           "an alignment change is a paint change");
    for (const auto mutate : std::vector<void (*)(TextLayout&)>{
             [](TextLayout& l) { l.line_height = 2.0F; },
             [](TextLayout& l) { l.max_lines = 3U; },
             [](TextLayout& l) { l.ellipsis = true; },
             [](TextLayout& l) { l.align = TextAlign::justify; }}) {
        auto changed = before;
        mutate(*changed.root.children[0].text_layout);
        expect(paint_differs(before.root.children[0], changed.root.children[0]),
               "each text layout field is a paint change");
    }
    auto removed = before;
    removed.root.children[0].text_layout.reset();
    expect(paint_differs(before.root.children[0], removed.root.children[0]),
           "removing a text layout is a paint change");

    const auto bounded = compute_scene_damage(before, after, 1.0F);
    const auto box = bounded.bounds();
    const float lines = 2.0F * 1.25F * 16.0F;
    expect(!bounded.empty() && box.left <= 0 && box.right >= static_cast<int>(kWidth) &&
               box.top <= static_cast<int>(kBox.y - 32.0F) &&
               box.bottom >= static_cast<int>(kBox.y + lines + 32.0F) &&
               box.bottom < static_cast<int>(kHeight),
           "a line-limited paragraph damages the frame's width down to its last line",
           bounds_of(bounded));

    TextLayout open{};
    TextLayout open_centered{.align = TextAlign::center};
    const auto unbounded = compute_scene_damage(text_scene(open), text_scene(open_centered), 1.0F);
    expect(!unbounded.empty() && unbounded.bounds().bottom >= static_cast<int>(kHeight) &&
               unbounded.bounds().left <= 0 &&
               unbounded.bounds().right >= static_cast<int>(kWidth),
           "a paragraph without a line limit damages down to the frame's bottom",
           bounds_of(unbounded));

    TextLayout natural{.max_lines = 3U};
    TextLayout natural_end{.align = TextAlign::end, .max_lines = 3U};
    const auto spacing =
        compute_scene_damage(text_scene(natural), text_scene(natural_end), 1.0F);
    expect(!spacing.empty() &&
               spacing.bounds().bottom >=
                   static_cast<int>(kBox.y + 3.0F * kTextNaturalLineHeightBound * 16.0F),
           "a line-limited paragraph at the fonts' own spacing damages its bound on line height",
           bounds_of(spacing));

    Scene legacy_before = text_scene(std::nullopt);
    Scene legacy_after = legacy_before;
    legacy_after.root.children[0].fill = Color::hex(0x22C55E);
    const auto legacy = compute_scene_damage(legacy_before, legacy_after, 1.0F);
    expect(!legacy.empty() && legacy.bounds().top <= static_cast<int>(kBox.y - 16.0F) &&
               legacy.bounds().bottom < 110,
           "text without a layout keeps its one-line damage", bounds_of(legacy));
}

}  // namespace

int main() {
    versioning();
    invalid_layouts();
    lowering();
    damage();
    if (failures != 0) {
        std::cerr << failures << " text layout check(s) failed\n";
        return 1;
    }
    std::cout << "text layout checks passed\n";
    return 0;
}
