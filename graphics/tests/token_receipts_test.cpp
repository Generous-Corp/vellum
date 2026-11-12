#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/token_receipts.hpp>

#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAILED: %s\n", what);
        ++failures;
    }
}

constexpr Color kAccent = Color::hex(0x14B8A6);
constexpr Color kCanvas = Color::hex(0x0F172A);
constexpr Color kLabel = Color::hex(0xE5E7EB);
constexpr Color kLiteral = Color::hex(0x123456);

SceneNode square(const std::string& id, float column, float row, Color fill,
                 std::optional<std::uint32_t> token) {
    return {
        .id = id,
        .kind = SceneNode::Kind::rectangle,
        .bounds = {20.0F + column * 100.0F, 20.0F + row * 100.0F, 40.0F, 40.0F},
        .fill = fill,
        .fill_token = token,
    };
}

std::vector<TokenReceipt> receipts(Color accent, Color canvas, Color label,
                                   const char* accent_layer = "primitive") {
    return {
        {"main.color.accent", accent_layer, accent},
        {"main.color.canvas", "primitive", canvas},
        {"main.color.label", "primitive", label},
    };
}

Scene themed_scene() {
    Scene scene;
    scene.width = 500.0F;
    scene.height = 300.0F;
    scene.tokens = receipts(kAccent, kCanvas, kLabel);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 500, 300}};
    scene.root.children.push_back(square("a0", 0, 0, kAccent, 0U));
    scene.root.children.push_back(square("a1", 1, 0, kAccent, 0U));
    scene.root.children.push_back(square("a2", 2, 0, kAccent, 0U));
    scene.root.children.push_back(square("c0", 3, 0, kCanvas, 1U));
    scene.root.children.push_back(square("lit", 4, 0, kLiteral, std::nullopt));
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .bounds = {20.0F, 200.0F, 200.0F, 20.0F},
        .fill = kLabel,
        .text_runs = {{.text = "Board", .style = {.color = kLabel}},
                      {.text = "!", .style = {.color = kLiteral}}},
        .fill_token = 2U,
    });
    return scene;
}

DamagePolicy exact() {
    return {.max_rects = 16, .full_repaint_fraction = 2.0, .antialias_margin = 0};
}

const SceneNode* find(const Scene& scene, std::string_view id) { return find_node(scene, id); }

void sound_scene_is_accepted() {
    const Scene scene = themed_scene();
    check(!find_invalid_token_reference(scene).has_value(), "a sound table has no refusal");
    const auto accepted = accept_scene(scene);
    check(accepted.accepted && accepted.diagnostics.empty(), "a version 7 token scene is drawn");
    check(!make_paint_commands(scene).empty(), "a version 7 token scene lowers to commands");
}

void older_versions_refuse_the_table() {
    Scene scene = themed_scene();
    for (const std::uint32_t version : {1U, 2U, 3U, 4U, 5U, 6U}) {
        scene.scene_version = version;
        check(!scene_fields_match_version(scene), "a scene before 7 cannot carry tokens");
        const auto verdict = accept_scene(scene);
        check(!verdict.accepted && verdict.diagnostics.size() == 1U &&
                  verdict.diagnostics[0].code == kSceneVersionMismatchCode,
              "an older scene using the token table is refused with a version diagnostic");
        check(make_paint_commands(scene).empty(), "an older scene using the table draws nothing");
    }
    Scene table_only = themed_scene();
    for (auto& child : table_only.root.children) child.fill_token.reset();
    table_only.scene_version = 6U;
    check(!accept_scene(table_only).accepted, "a version 6 scene with only the table is refused");
    Scene references_only = themed_scene();
    references_only.tokens.clear();
    references_only.scene_version = 6U;
    check(!accept_scene(references_only).accepted,
          "a version 6 scene with only references is refused");
}

void negative_control_older_scene_without_tokens() {
    Scene scene = themed_scene();
    scene.tokens.clear();
    for (auto& child : scene.root.children) child.fill_token.reset();
    scene.scene_version = 6U;
    check(accept_scene(scene).accepted, "a version 6 scene without tokens is still drawn");
    scene.scene_version = 7U;
    check(accept_scene(scene).accepted, "a version 7 scene without tokens is drawn");
}

void unsound_tables_are_refused() {
    const auto refused = [](Scene scene, const char* what) {
        const auto verdict = accept_scene(scene);
        check(!verdict.accepted && verdict.diagnostics.size() == 1U &&
                  verdict.diagnostics[0].code == kInvalidTokenReferenceCode,
              what);
    };
    Scene out_of_range = themed_scene();
    out_of_range.root.children[0].fill_token = 3U;
    refused(out_of_range, "a reference past the table is refused");
    Scene stale = themed_scene();
    stale.root.children[0].fill = kCanvas;
    refused(stale, "a fill that differs from its receipt is refused");
    Scene duplicate = themed_scene();
    duplicate.tokens[1].path = duplicate.tokens[0].path;
    refused(duplicate, "a repeated token path is refused");
    Scene non_finite = themed_scene();
    non_finite.tokens[2].value.red = std::numeric_limits<float>::quiet_NaN();
    refused(non_finite, "a non-finite receipt value is refused");
}

void theme_switch_rewrites_only_referencing_nodes() {
    const Scene before = themed_scene();
    Scene after = before;
    const auto next = receipts(Color::hex(0xEF4444), kCanvas, kLabel);
    const auto result = apply_token_values(after, next);
    check(result.applied && result.changed == std::vector<std::uint32_t>{0U},
          "only the accent entry changed");
    check(result.nodes_updated == 3U, "three nodes reference the accent");
    check(count_token_references(before, result.changed) == result.nodes_updated,
          "the reference count matches the nodes rewritten");
    check(changed_token_entries(before, after) == result.changed,
          "the table diff names the same entry");
    check(find(after, "a1")->fill.red == Color::hex(0xEF4444).red, "a referencing node follows");
    check(same_token_color(find(after, "c0")->fill, kCanvas), "another token's node is untouched");
    check(same_token_color(find(after, "lit")->fill, kLiteral), "a literal node is untouched");
    check(!find_invalid_token_reference(after).has_value(), "the switched scene stays sound");
    check(accept_scene(after).accepted, "the switched scene is drawn");
}

void text_runs_follow_their_node() {
    Scene scene = themed_scene();
    const Color next = Color::hex(0x111827);
    const auto result = apply_token_values(scene, receipts(kAccent, kCanvas, next));
    check(result.applied && result.nodes_updated == 1U, "the label is rewritten");
    const auto* label = find(scene, "label");
    check(same_token_color(label->fill, next), "the label fill follows");
    check(same_token_color(label->text_runs[0].style.color, next),
          "a run that inherited the label colour follows");
    check(same_token_color(label->text_runs[1].style.color, kLiteral),
          "a run with its own colour keeps it");
}

void switch_failures_and_no_ops() {
    Scene scene = themed_scene();
    const Scene original = scene;
    std::vector<TokenReceipt> missing = {{"main.color.accent", "primitive", kAccent}};
    const auto failed = apply_token_values(scene, missing);
    check(!failed.applied && failed.missing_path == "main.color.canvas",
          "a theme missing a path is not applied");
    check(changed_token_entries(original, scene).empty() &&
              same_token_color(scene.root.children[0].fill, kAccent),
          "a failed switch leaves the scene unchanged");
    const auto same = apply_token_values(scene, receipts(kAccent, kCanvas, kLabel));
    check(same.applied && same.changed.empty() && same.nodes_updated == 0U,
          "switching to identical values changes nothing");
    const auto relayered = apply_token_values(scene, receipts(kAccent, kCanvas, kLabel, "theme"));
    check(relayered.applied && relayered.changed == std::vector<std::uint32_t>{0U} &&
              relayered.nodes_updated == 0U && scene.tokens[0].layer == "theme",
          "a layer change alone updates the receipt but no node");
}

void damage_covers_exactly_the_referencing_nodes() {
    const Scene before = themed_scene();
    Scene accent_switched = before;
    (void)apply_token_values(accent_switched, receipts(Color::hex(0xEF4444), kCanvas, kLabel));
    const auto damage = compute_scene_damage(before, accent_switched, 1.0F, exact());
    check(!damage.is_full_frame() && damage.rects().size() == 3U,
          "an accent change damages three rectangles");
    const auto square_area = static_cast<std::int64_t>(40) * 40;
    check(damage.area() == 3 * square_area, "the damage is exactly the three squares");
    for (const char* id : {"a0", "a1", "a2"}) {
        const auto* node = find(before, id);
        const auto rect = to_pixel_rect(node->bounds, 1.0F).value();
        check(damage.intersects(rect), "each accent node is damaged");
    }
    const auto lit = to_pixel_rect(find(before, "lit")->bounds, 1.0F).value();
    const auto canvas = to_pixel_rect(find(before, "c0")->bounds, 1.0F).value();
    check(!damage.intersects(lit) && !damage.intersects(canvas),
          "the literal and canvas nodes are not damaged");

    Scene canvas_switched = before;
    const auto canvas_result =
        apply_token_values(canvas_switched, receipts(kAccent, Color::hex(0xFFFFFF), kLabel));
    const auto canvas_damage = compute_scene_damage(before, canvas_switched, 1.0F, exact());
    check(canvas_result.nodes_updated == 1U && canvas_damage.rects().size() == 1U &&
              canvas_damage.area() == square_area,
          "a canvas change damages only its one node");

    Scene unchanged = before;
    (void)apply_token_values(unchanged, receipts(kAccent, kCanvas, kLabel));
    check(compute_scene_damage(before, unchanged, 1.0F, exact()).empty(),
          "a switch to identical values damages nothing");

    DamageTracker tracker(exact());
    tracker.commit(before, 1.0F);
    (void)tracker.take_damage();
    tracker.commit(accent_switched, 1.0F);
    check(tracker.pending_damage().rects().size() == 3U &&
              tracker.pending_damage().area() == 3 * square_area,
          "the tracker accumulates the same targeted damage");
}

void negative_control_stale_fill_would_not_damage() {
    const Scene before = themed_scene();
    Scene table_only = before;
    table_only.tokens[0].value = Color::hex(0xEF4444);
    check(compute_scene_damage(before, table_only, 1.0F, exact()).empty(),
          "a table edit that leaves node fills alone damages nothing");
    check(find_invalid_token_reference(table_only).has_value() && !accept_scene(table_only).accepted,
          "and such a scene is refused rather than drawn stale");
}

}  // namespace

int main() {
    static_assert(kSceneVersion == 7U);
    static_assert(kDamageModelSceneVersion == 7U);
    sound_scene_is_accepted();
    older_versions_refuse_the_table();
    negative_control_older_scene_without_tokens();
    unsound_tables_are_refused();
    theme_switch_rewrites_only_referencing_nodes();
    text_runs_follow_their_node();
    switch_failures_and_no_ops();
    damage_covers_exactly_the_referencing_nodes();
    negative_control_stale_fill_would_not_damage();
    if (failures != 0) {
        std::fprintf(stderr, "%d token receipt checks failed\n", failures);
        return 1;
    }
    std::puts("token receipts passed");
    return 0;
}
