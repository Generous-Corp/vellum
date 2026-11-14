#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/token_receipts.hpp>

#include <cstdint>
#include <cstdio>
#include <memory>
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

constexpr Color kStroke = Color::hex(0xF59E0B);
constexpr Color kShadow = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F);
constexpr Color kStopA = Color::hex(0x6366F1);
constexpr Color kStopB = Color::hex(0xEC4899);

std::vector<TokenReceipt> slot_receipts(Color stroke, Color shadow, Color stop_a, Color run) {
    return {
        {"s.stroke", "primitive", stroke},
        {"s.shadow", "primitive", shadow},
        {"s.stop", "primitive", stop_a},
        {"s.run", "primitive", run},
        {"s.fixed", "primitive", kStopB},
    };
}

Scene slotted_scene() {
    Scene scene;
    scene.width = 500.0F;
    scene.height = 300.0F;
    scene.tokens = slot_receipts(kStroke, kShadow, kStopA, kLabel);
    scene.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 500, 300}};
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close};
    path->values = {0.0F, 0.0F, 30.0F, 0.0F, 15.0F, 30.0F};
    path->stroke = {.color = kStroke, .width = 3.0F};
    scene.root.children.push_back({
        .id = "path", .kind = SceneNode::Kind::path, .bounds = {20, 20, 40, 40},
        .path = path,
        .color_tokens = {{TokenSlot::Kind::path_stroke, 0, 0, 0U}},
    });
    scene.root.children.push_back({
        .id = "card", .kind = SceneNode::Kind::rectangle, .bounds = {120, 20, 40, 40},
        .fill = kLiteral,
        .fill_gradients = {{.stops = {{0.0F, kStopA}, {1.0F, kStopB}}}},
        .box_shadows = {{.offset_x = 1, .offset_y = 2, .blur_radius = 4, .color = kShadow}},
        .color_tokens = {{TokenSlot::Kind::box_shadow, 0, 0, 1U},
                         {TokenSlot::Kind::gradient_stop, 0, 0, 2U},
                         {TokenSlot::Kind::gradient_stop, 0, 1, 4U}},
    });
    scene.root.children.push_back({
        .id = "text", .kind = SceneNode::Kind::text, .bounds = {20, 200, 200, 20},
        .fill = kLabel,
        .text_runs = {{.text = "a", .style = {.color = kLabel}},
                      {.text = "b", .style = {.color = kLabel}}},
        .color_tokens = {{TokenSlot::Kind::text_run, 1, 0, 3U}},
    });
    return scene;
}

void every_slot_follows_its_receipt() {
    Scene scene = slotted_scene();
    check(!find_invalid_token_reference(scene).has_value() && accept_scene(scene).accepted,
          "a scene with every slot kind is sound");
    const Scene before = scene;
    const Color red = Color::hex(0xEF4444);
    const auto result = apply_token_values(
        scene, slot_receipts(red, Color::rgba(1.0F, 0.0F, 0.0F, 0.25F), red, red));
    check(result.applied && result.changed == std::vector<std::uint32_t>{0U, 1U, 2U, 3U} &&
              result.nodes_updated == 3U,
          "four receipts change three nodes");
    check(same_token_color(find(scene, "path")->path->stroke.color, red), "the stroke follows");
    check(same_token_color(before.root.children[0].path->stroke.color, kStroke),
          "the earlier scene keeps its own path object");
    const auto* card = find(scene, "card");
    check(card->box_shadows[0].color.alpha == 0.25F, "the shadow follows");
    check(same_token_color(card->fill_gradients[0].stops[0].color, red), "a stop follows");
    check(same_token_color(card->fill_gradients[0].stops[1].color, kStopB),
          "a stop on an unchanged receipt is untouched");
    check(same_token_color(card->fill, kLiteral), "the literal fill is untouched");
    const auto* text = find(scene, "text");
    check(same_token_color(text->text_runs[1].style.color, red) &&
              same_token_color(text->text_runs[0].style.color, kLabel) &&
              same_token_color(text->fill, kLabel),
          "only the run with a slot follows, and the node fill is untouched");
    check(!find_invalid_token_reference(scene).has_value() && accept_scene(scene).accepted,
          "the switched scene stays sound");
    std::vector<std::uint32_t> stroke_only = {0U};
    check(count_token_references(before, stroke_only) == 1U,
          "reference counting sees slot references");
}

void bad_slots_are_refused() {
    const auto refused = [](Scene scene, const char* what) {
        const auto verdict = accept_scene(scene);
        check(!verdict.accepted && verdict.diagnostics.size() == 1U &&
                  verdict.diagnostics[0].code == kInvalidTokenReferenceCode,
              what);
    };
    Scene past = slotted_scene();
    past.root.children[1].color_tokens[0].token = 9U;
    refused(past, "a slot reference past the table is refused");
    Scene stale = slotted_scene();
    stale.root.children[1].box_shadows[0].color = kLabel;
    refused(stale, "a shadow that differs from its receipt is refused");
    Scene missing = slotted_scene();
    missing.root.children[1].color_tokens[1].stop = 5U;
    refused(missing, "a gradient stop the node lacks is refused");
    Scene no_path = slotted_scene();
    no_path.root.children[0].path.reset();
    refused(no_path, "a stroke slot without a path is refused");
    Scene twice = slotted_scene();
    twice.root.children[1].color_tokens.push_back(twice.root.children[1].color_tokens[0]);
    refused(twice, "a slot named twice is refused");
    Scene old = slotted_scene();
    for (auto& child : old.root.children) child.color_tokens.clear();
    old.tokens.clear();
    old.scene_version = 6U;
    check(accept_scene(old).accepted, "control: the same scene without slots is drawn at 6");
    Scene slots_only = slotted_scene();
    slots_only.tokens.clear();
    slots_only.scene_version = 6U;
    check(!accept_scene(slots_only).accepted, "a version 6 scene with slots is refused");
    Scene slot_nodes_only = slotted_scene();
    slot_nodes_only.scene_version = 6U;
    check(!accept_scene(slot_nodes_only).accepted &&
              accept_scene(slot_nodes_only).diagnostics[0].code == kSceneVersionMismatchCode,
          "a version 6 scene with slots gets a version diagnostic");
}


void lowering_refuses_invalid_references() {
    const Scene sound = themed_scene();
    std::vector<PaintCommandRange> ranges;
    check(!make_paint_commands(sound, &ranges).empty() && !ranges.empty() &&
              scene_loss_diagnostics(sound).empty(),
          "control: a sound token scene lowers and reports no loss");

    Scene stale = themed_scene();
    stale.root.children[0].fill = kCanvas;
    check(make_paint_commands(stale).empty(), "make_paint_commands refuses a stale fill");
    check(make_paint_commands(stale, &ranges).empty() && ranges.empty(),
          "make_paint_commands with ranges refuses a stale fill");
    const auto loss = scene_loss_diagnostics(stale);
    check(loss.size() == 1U && loss[0].code == kInvalidTokenReferenceCode &&
              loss[0].severity == LoweringDiagnostic::Severity::error &&
              loss[0].node_id == "a0" && loss[0].property == "fill_token",
          "scene_loss_diagnostics reports a stale fill with its node and property");
    const auto verdict = accept_scene(stale);
    check(!verdict.accepted && verdict.diagnostics.size() == 1U &&
              verdict.diagnostics[0].code == kInvalidTokenReferenceCode &&
              verdict.diagnostics[0].node_id == "a0" &&
              verdict.diagnostics[0].property == "fill_token" &&
              verdict.diagnostics[0].message == verdict.reason,
          "accept_scene reports the same refusal");

    Scene bad_slot = slotted_scene();
    bad_slot.root.children[1].box_shadows[0].color = kLabel;
    const auto refusal = find_invalid_token_reference(bad_slot);
    check(refusal && refusal->node_id == "card" && refusal->property == "color_tokens" &&
              refusal->slot && refusal->slot->kind == TokenSlot::Kind::box_shadow,
          "a slot refusal carries the slot");
    const auto slot_loss = scene_loss_diagnostics(bad_slot);
    check(make_paint_commands(bad_slot).empty() && slot_loss.size() == 1U &&
              slot_loss[0].code == kInvalidTokenReferenceCode &&
              slot_loss[0].property == "color_tokens" && slot_loss[0].node_id == "card",
          "lowering refuses a stale slot and reports it");

    Scene table = themed_scene();
    table.tokens[1].path = table.tokens[0].path;
    const auto table_refusal = find_invalid_token_reference(table);
    check(table_refusal && table_refusal->property == "tokens" && !table_refusal->slot &&
              make_paint_commands(table).empty() &&
              scene_loss_diagnostics(table).at(0).property == "tokens",
          "a broken table is refused by lowering with the tokens property");
}

void id_less_root_refusal_has_a_diagnostic() {
    Scene scene = themed_scene();
    scene.root.id.clear();
    scene.root.kind = SceneNode::Kind::rectangle;
    scene.root.fill = kCanvas;
    scene.root.fill_token = 1U;
    check(accept_scene(scene).accepted, "control: an id-less root with a sound reference is drawn");
    scene.root.fill = kAccent;
    const auto verdict = accept_scene(scene);
    check(!verdict.accepted && verdict.diagnostics.size() == 1U &&
              verdict.diagnostics[0].code == kInvalidTokenReferenceCode &&
              verdict.diagnostics[0].node_id.empty() &&
              verdict.diagnostics[0].property == "fill_token",
          "an id-less root with a stale reference is refused with a diagnostic");
    check(make_paint_commands(scene).empty(), "lowering refuses the id-less root too");
}

void slots_are_canonical() {
    check(accept_scene(slotted_scene()).accepted, "control: canonical slots are accepted");
    const auto refused = [](Scene scene, TokenSlot::Kind kind, const char* what) {
        const auto refusal = find_invalid_token_reference(scene);
        const auto verdict = accept_scene(scene);
        check(refusal && refusal->property == "color_tokens" && refusal->slot &&
                  refusal->slot->kind == kind && !verdict.accepted &&
                  verdict.diagnostics.size() == 1U &&
                  verdict.diagnostics[0].code == kInvalidTokenReferenceCode &&
                  make_paint_commands(scene).empty(),
              what);
    };
    Scene indexed = slotted_scene();
    indexed.root.children[0].color_tokens[0].index = 1U;
    refused(indexed, TokenSlot::Kind::path_stroke, "a path stroke slot with an index is refused");
    Scene stopped = slotted_scene();
    stopped.root.children[0].color_tokens[0].stop = 2U;
    refused(stopped, TokenSlot::Kind::path_stroke, "a path stroke slot with a stop is refused");
    Scene run = slotted_scene();
    run.root.children[2].color_tokens[0].stop = 1U;
    refused(run, TokenSlot::Kind::text_run, "a text run slot with a stop is refused");
    Scene shadow = slotted_scene();
    shadow.root.children[1].color_tokens[0].stop = 1U;
    refused(shadow, TokenSlot::Kind::box_shadow, "a box shadow slot with a stop is refused");
    Scene two_strokes = slotted_scene();
    two_strokes.root.children[0].color_tokens.push_back({TokenSlot::Kind::path_stroke, 7, 0, 0U});
    refused(two_strokes, TokenSlot::Kind::path_stroke,
            "a second stroke slot with a different index is refused");
    Scene same_stroke = slotted_scene();
    same_stroke.root.children[0].color_tokens.push_back({TokenSlot::Kind::path_stroke, 0, 0, 0U});
    refused(same_stroke, TokenSlot::Kind::path_stroke, "the same stroke slot twice is refused");

    Scene rewrite = slotted_scene();
    rewrite.root.children[0].color_tokens[0].index = 3U;
    const Color red = Color::hex(0xEF4444);
    (void)apply_token_values(rewrite, slot_receipts(red, kShadow, kStopA, kLabel));
    check(same_token_color(find(rewrite, "path")->path->stroke.color, kStroke),
          "a theme switch does not rewrite through a non-canonical slot");
}

void versioned_fields_are_gated() {
    auto line = std::make_shared<const PathGeometry>(PathGeometry{
        .verbs = {PathVerb::move, PathVerb::line}, .values = {0.0F, 0.0F, 4.0F, 4.0F}});
    Scene plain;
    plain.width = 20.0F;
    plain.height = 20.0F;
    plain.root = {.id = "root", .kind = SceneNode::Kind::group, .bounds = {0, 0, 20, 20}};
    plain.root.children.push_back({.id = "box",
                                   .kind = SceneNode::Kind::rectangle,
                                   .bounds = {0, 0, 4, 4},
                                   .fill = kAccent,
                                   .fill_gradients = {{.stops = {{0.0F, kStopA}, {1.0F, kStopB}}}},
                                   .box_shadows = {{.blur_radius = 2, .color = kShadow}},
                                   .text = "kept"});
    plain.root.children.push_back({.id = "label",
                                   .kind = SceneNode::Kind::text,
                                   .bounds = {0, 8, 20, 8},
                                   .fill = kLabel,
                                   .text_runs = {{.text = "a", .style = {.color = kLabel}}}});
    for (std::uint32_t version = kMinSupportedSceneVersion; version <= kSceneVersion; ++version) {
        plain.scene_version = version;
        check(scene_fields_match_version(plain) && accept_scene(plain).accepted &&
                  make_paint_commands(plain).size() == 2U,
              "control: version 1 fields stay legal at every supported version");
    }

    const auto refused_until = [](Scene scene, std::uint32_t introduced, const char* node_id,
                                  const char* property, const char* what) {
        for (std::uint32_t version = kMinSupportedSceneVersion; version < introduced;
             ++version) {
            scene.scene_version = version;
            const auto verdict = accept_scene(scene);
            const auto loss = scene_loss_diagnostics(scene);
            check(!scene_fields_match_version(scene) && make_paint_commands(scene).empty() &&
                      !verdict.accepted && verdict.diagnostics.size() == 1U &&
                      verdict.diagnostics[0].code == kSceneVersionMismatchCode &&
                      verdict.diagnostics[0].node_id == node_id &&
                      verdict.diagnostics[0].property == property && loss.size() == 1U &&
                      loss[0].code == kSceneVersionMismatchCode,
                  what);
        }
        scene.scene_version = introduced;
        check(scene_fields_match_version(scene),
              "control: the field is legal from the version that introduced it");
    };
    Scene path_data = plain;
    path_data.root.children[0].path = line;
    refused_until(path_data, 3U, "box", "path",
                  "a rectangle carrying path data before version 3 is refused");
    Scene image_reference = plain;
    image_reference.root.children[0].image = ImageReference{.content_hash = std::string(64, 'a')};
    refused_until(image_reference, 4U, "box", "image",
                  "a rectangle carrying an image reference before version 4 is refused");
    Scene image_table = plain;
    std::vector<std::uint8_t> texels(4U, 255U);
    image_table.images = {make_image_asset("sha256:" + std::string(64, 'a'), 1, 1, texels,
                                           ImageAlpha::premultiplied)};
    check(image_table.images[0] != nullptr, "the fixture asset is built");
    refused_until(image_table, 4U, "root", "images",
                  "an image table before version 4 is refused");
    Scene token_table = plain;
    token_table.tokens = receipts(kAccent, kCanvas, kLabel);
    refused_until(token_table, 7U, "root", "tokens",
                  "a token table before version 7 is refused");
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
    every_slot_follows_its_receipt();
    bad_slots_are_refused();
    lowering_refuses_invalid_references();
    id_less_root_refusal_has_a_diagnostic();
    slots_are_canonical();
    versioned_fields_are_gated();
    if (failures != 0) {
        std::fprintf(stderr, "%d token receipt checks failed\n", failures);
        return 1;
    }
    std::puts("token receipts passed");
    return 0;
}
