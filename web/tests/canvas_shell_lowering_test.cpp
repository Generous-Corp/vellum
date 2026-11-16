#include "canvas_shell_lowering.hpp"
#include "command_digest.hpp"

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/vector_asset.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using vellum::graphics::ImageAlpha;
using vellum::graphics::ImageAsset;
using vellum::graphics::ImageReference;
using vellum::graphics::LoweringDiagnostic;
using vellum::graphics::Scene;
using vellum::graphics::SceneNode;

const std::string kHash = "sha256:" + std::string(64, 'a');

Scene image_scene() {
    Scene scene;
    scene.width = 16.0F;
    scene.height = 16.0F;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "logo",
        .kind = SceneNode::Kind::image,
        .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
        .image = ImageReference{.content_hash = kHash},
    });
    const std::vector<std::uint8_t> texels(16, 255);
    scene.images = {vellum::graphics::make_image_asset(kHash, 2, 2, texels,
                                                       ImageAlpha::premultiplied)};
    return scene;
}

// The shell refuses `scene` with `reason` and one `code` error, draws and
// counts nothing, and agrees with `accept_scene`, the native decision.
bool refused(const Scene& scene, const std::string& reason, std::string_view code,
             const std::string& node_id) {
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    const auto native = vellum::graphics::accept_scene(scene);
    const bool ok = !lowering.accepted && lowering.reason == reason &&
                    lowering.commands.empty() && lowering.losses == 0 &&
                    lowering.diagnostics.size() == 1U &&
                    lowering.diagnostics[0].code == code &&
                    lowering.diagnostics[0].node_id == node_id &&
                    lowering.diagnostics[0].severity == LoweringDiagnostic::Severity::error &&
                    !native.accepted && native.reason == lowering.reason &&
                    native.diagnostics.size() == 1U &&
                    native.diagnostics[0].message == lowering.diagnostics[0].message;
    if (!ok) {
        std::cerr << "expected the refusal \"" << reason << "\" (" << code << "), got accepted="
                  << lowering.accepted << " reason=\"" << lowering.reason << "\" diagnostics="
                  << lowering.diagnostics.size() << " commands=" << lowering.commands.size()
                  << '\n';
    }
    return ok;
}

bool image_refusals() {
    using vellum::graphics::kInvalidImageCode;
    using vellum::graphics::kSceneVersionMismatchCode;
    const auto accepted = vellum::web::lower_for_canvas_shell(image_scene());
    if (!accepted.accepted || accepted.commands.size() != 1U || accepted.losses != 1 ||
        !accepted.diagnostics.empty() || !accepted.reason.empty()) {
        std::cerr << "a version 4 image scene was not drawn with one loss\n";
        return false;
    }
    auto missing = image_scene();
    missing.images.clear();
    const auto placeholder = vellum::web::lower_for_canvas_shell(missing);
    if (!placeholder.accepted || placeholder.losses != 1 || placeholder.diagnostics.size() != 1U ||
        placeholder.diagnostics[0].code != vellum::graphics::kImageAssetMissingCode) {
        std::cerr << "a missing asset was not drawn as a reported placeholder\n";
        return false;
    }

    bool ok = true;
    auto older = image_scene();
    older.scene_version = 3U;
    ok &= refused(older, "scene version 3 cannot carry the image node logo",
                  kSceneVersionMismatchCode, "logo");
    auto malformed = image_scene();
    malformed.root.children[0].image->content_hash = "logo.png";
    ok &= refused(malformed,
                  "image logo cannot be drawn: the content hash is not sha256: followed by "
                  "64 lowercase hex digits",
                  kInvalidImageCode, "logo");
    auto unreferenced = image_scene();
    unreferenced.root.children[0].image.reset();
    ok &= refused(unreferenced, "image logo cannot be drawn: it has no image reference",
                  kInvalidImageCode, "logo");
    auto parent = image_scene();
    parent.root.children[0].children.push_back(
        {.id = "inner", .kind = SceneNode::Kind::rectangle, .bounds = {0, 0, 2, 2}});
    ok &= refused(parent, "image logo cannot be drawn: an image node has no children",
                  kInvalidImageCode, "logo");
    auto twice = image_scene();
    twice.images.push_back(twice.images[0]);
    ok &= refused(twice, "the scene lists image asset " + kHash + " twice", kInvalidImageCode, "");
    auto short_buffer = image_scene();
    auto truncated = std::make_shared<ImageAsset>(*short_buffer.images[0]);
    truncated->premultiplied_rgba.pop_back();
    short_buffer.images = {truncated};
    ok &= refused(short_buffer,
                  "image asset " + kHash +
                      " is invalid: the image's texel buffer does not match its dimensions",
                  kInvalidImageCode, "");
    auto straight = image_scene();
    auto unpremultiplied = std::make_shared<ImageAsset>(*straight.images[0]);
    unpremultiplied->premultiplied_rgba[3] = 16U;
    straight.images = {unpremultiplied};
    ok &= refused(straight,
                  "image asset " + kHash +
                      " is invalid: a texel has a colour channel above its alpha",
                  kInvalidImageCode, "");
    return ok;
}


// The shell reports each skipped mask once, as a warning naming the mask node,
// and the diagnostics hold nothing else for a scene that is otherwise clean.
bool reports_masks(const char* what, const Scene& scene, int losses,
                   const std::vector<std::string>& mask_ids,
                   const std::vector<std::string>& drawn) {
    using Kind = vellum::graphics::PaintCommand::Kind;
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    bool ok = lowering.accepted && lowering.losses == losses &&
              lowering.diagnostics.size() == mask_ids.size();
    for (std::size_t index = 0; ok && index < mask_ids.size(); ++index) {
        const auto& diagnostic = lowering.diagnostics[index];
        ok = diagnostic.code == vellum::web::kMaskNotCompositedCode &&
             diagnostic.node_id == mask_ids[index] && diagnostic.property == "mask" &&
             diagnostic.severity == LoweringDiagnostic::Severity::warning &&
             diagnostic.message.find("mask " + mask_ids[index]) != std::string::npos &&
             diagnostic.message.find("not composited") != std::string::npos;
    }
    const auto skipped = vellum::web::canvas_shell_masked(lowering.commands);
    std::vector<std::string> kept;
    for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
        const auto& command = lowering.commands[index];
        if (!skipped[index] && vellum::graphics::draws_content(command.kind)) {
            kept.push_back(command.node_id);
        }
    }
    ok = ok && kept == drawn;
    if (!ok) {
        std::cerr << what << ": expected " << losses << " losses and " << mask_ids.size()
                  << " mask reports, got accepted=" << lowering.accepted
                  << " losses=" << lowering.losses
                  << " diagnostics=" << lowering.diagnostics.size() << '\n';
    }
    return ok;
}

bool mask_reports() {
    const auto box = [](std::string id) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
        };
    };
    const auto scene_of = [](SceneNode node) {
        Scene scene;
        scene.root.id = "root";
        scene.root.children.push_back(std::move(node));
        return scene;
    };
    const auto masked_group = [&](std::string id, vellum::graphics::MaskMode mode) {
        SceneNode group{.id = id, .kind = SceneNode::Kind::group};
        group.children.push_back(box(id + "/content"));
        auto mask = box(id + "/mask");
        mask.mask = mode;
        group.children.push_back(std::move(mask));
        return group;
    };
    using vellum::graphics::MaskMode;
    bool ok = true;

    ok &= reports_masks("no mask", scene_of(box("plain")), 0, {}, {"plain"});

    ok &= reports_masks("single mask", scene_of(masked_group("g", MaskMode::alpha)), 2,
                        {"g/mask"}, {"g/content"});
    const auto single = vellum::web::lower_for_canvas_shell(
        scene_of(masked_group("g", MaskMode::luminance)));
    ok &= single.diagnostics.size() == 1U &&
          single.diagnostics[0].message.find("luminance") != std::string::npos;

    auto clip_in_mask = masked_group("g", MaskMode::alpha);
    auto& mask_node = clip_in_mask.children.back();
    auto inner = box("g/mask/inner");
    inner.clip = vellum::graphics::NodeClip{.rect = {0.0F, 0.0F, 2.0F, 2.0F}};
    mask_node.children.push_back(std::move(inner));
    ok &= reports_masks("clip inside a mask", scene_of(clip_in_mask), 2, {"g/mask"},
                        {"g/content"});

    auto nested_mask = masked_group("g", MaskMode::alpha);
    auto inner_mask = box("g/mask/nested");
    inner_mask.mask = MaskMode::luminance;
    nested_mask.children.back().children.push_back(std::move(inner_mask));
    ok &= reports_masks("mask inside a mask", scene_of(nested_mask), 2, {"g/mask"},
                        {"g/content"});

    auto mask_in_clip = masked_group("g", MaskMode::alpha);
    mask_in_clip.clip = vellum::graphics::NodeClip{.rect = {0.0F, 0.0F, 8.0F, 8.0F}};
    ok &= reports_masks("mask inside a clip", scene_of(mask_in_clip), 3, {"g/mask"},
                        {"g/content"});

    auto isolated = masked_group("g", MaskMode::alpha);
    isolated.isolate = true;
    ok &= reports_masks("isolated group with a mask", scene_of(isolated), 3, {"g/mask"},
                        {"g/content"});

    SceneNode two{.id = "pair", .kind = SceneNode::Kind::group};
    two.children.push_back(masked_group("first", MaskMode::alpha));
    two.children.push_back(masked_group("second", MaskMode::luminance));
    ok &= reports_masks("two masks", scene_of(two), 4, {"first/mask", "second/mask"},
                        {"first/content", "second/content"});

    auto missing = scene_of(masked_group("g", MaskMode::alpha));
    missing.root.children.push_back({
        .id = "logo",
        .kind = SceneNode::Kind::image,
        .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
        .image = ImageReference{.content_hash = kHash},
    });
    const auto mixed = vellum::web::lower_for_canvas_shell(missing);
    ok &= mixed.accepted && mixed.diagnostics.size() == 2U &&
          mixed.diagnostics[0].code == vellum::graphics::kImageAssetMissingCode &&
          mixed.diagnostics[1].code == vellum::web::kMaskNotCompositedCode;
    if (!ok) std::cerr << "a mask report did not follow the scene's own warnings\n";
    return ok;
}

// The shell implements Tier 1 only. A Tier 1 blended group is drawn offscreen
// and composited once with its operation and its own opacity, so its members
// are not each blended and the group alpha is not multiplied into every child.
// A Tier 2 mode, or any mode on a backend without blendBegin and blendEnd, is
// drawn as normal with one warning and one counted loss.
bool blend_lowering() {
    using vellum::graphics::BlendMode;
    using vellum::graphics::kAllBlendModes;
    using Kind = vellum::graphics::PaintCommand::Kind;
    const auto box = [](std::string id, BlendMode mode) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .blend_mode = mode,
        };
    };
    int lacking = 0;
    for (const auto mode : kAllBlendModes) {
        const auto operation = vellum::web::canvas_shell_blend_operation(mode);
        lacking += operation ? 0 : 1;
        if (operation.has_value() != (vellum::graphics::blend_mode_tier(mode) == 1) ||
            (operation && operation->empty())) {
            std::cerr << "blend mode " << vellum::graphics::to_string(mode)
                      << " has the wrong canvas operation for its tier\n";
            return false;
        }
    }
    if (lacking != 18 ||
        vellum::web::canvas_shell_blend_operation(BlendMode::normal) != "source-over" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::multiply) != "multiply" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::destination_in) != "destination-in" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::color_dodge).has_value() ||
        vellum::web::canvas_shell_blend_operation(BlendMode::plus).has_value()) {
        std::cerr << "the canvas shell does not implement exactly Tier 1\n";
        return false;
    }

    vellum::graphics::Scene scene;
    scene.root.id = "root";
    auto group = box("group", BlendMode::multiply);
    group.opacity = 0.5F;
    group.children.push_back(box("group/plain", BlendMode::normal));
    group.children.push_back(box("group/inner", BlendMode::screen));
    auto fade = box("group/fade", BlendMode::normal);
    fade.opacity = 0.4F;
    fade.children.push_back(box("group/fade/child", BlendMode::normal));
    group.children.push_back(std::move(fade));
    scene.root.children.push_back(box("before", BlendMode::normal));
    scene.root.children.push_back(std::move(group));
    scene.root.children.push_back(box("after", BlendMode::normal));
    auto lowering = vellum::web::lower_for_canvas_shell(scene);
    auto plan = vellum::web::canvas_shell_paint_plan(lowering.commands);
    const auto flattened = vellum::graphics::effective_opacities(lowering.commands);
    std::vector<std::string> drawn;
    std::vector<std::string> groups;
    bool draws_take_no_operation = true;
    for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
        const auto& command = lowering.commands[index];
        const std::string alpha = std::to_string(plan.opacities[index]).substr(0, 4);
        if (command.kind == Kind::begin_layer) {
            groups.push_back(command.node_id + "=" + std::string(plan.group_operations[index]) +
                             "@" + alpha);
        } else if (vellum::graphics::draws_content(command.kind)) {
            drawn.push_back(command.node_id + "@" + alpha);
            draws_take_no_operation = draws_take_no_operation &&
                                      plan.group_operations[index].empty();
        }
    }
    const std::vector<std::string> expected_drawn{
        "before@1.00", "group@1.00", "group/plain@1.00", "group/inner@1.00",
        "group/fade@0.40", "group/fade/child@0.40", "after@1.00"};
    const std::vector<std::string> expected_groups{
        "group=multiply@0.50", "group/inner=screen@1.00", "group/fade=@0.40"};
    if (!lowering.accepted || !lowering.diagnostics.empty() || lowering.losses != 1 ||
        drawn != expected_drawn || groups != expected_groups || !draws_take_no_operation) {
        std::cerr << "a Tier 1 group was not composited once with its own alpha, losses "
                  << lowering.losses << '\n';
        for (const auto& entry : groups) std::cerr << "  group " << entry << '\n';
        for (const auto& entry : drawn) std::cerr << "  drew " << entry << '\n';
        return false;
    }
    // Negative control: flattening multiplies the group alpha into every
    // child, which is what the composited plan avoids.
    const auto plain_index = static_cast<std::size_t>(
        std::find_if(lowering.commands.begin(), lowering.commands.end(),
                     [](const auto& command) { return command.node_id == "group/plain"; }) -
        lowering.commands.begin());
    if (plain_index >= flattened.size() || flattened[plain_index] != 0.5F) {
        std::cerr << "negative control: flattening did not fade the child by the group alpha\n";
        return false;
    }

    const auto reported = [](const LoweringDiagnostic& diagnostic, std::string_view node,
                             std::string_view mode, std::string_view why) {
        return diagnostic.code == vellum::graphics::kBlendModeFallbackCode &&
               diagnostic.node_id == node && diagnostic.property == "blendMode" &&
               diagnostic.severity == LoweringDiagnostic::Severity::warning &&
               diagnostic.disposition == LoweringDiagnostic::Disposition::lowered &&
               diagnostic.message.find(std::string("blend mode ") + std::string(mode)) !=
                   std::string::npos &&
               diagnostic.message.find(why) != std::string::npos &&
               diagnostic.message.find("drawn as normal") != std::string::npos;
    };
    scene.root.children[1].blend_mode = BlendMode::hue;
    scene.root.children[1].children[1].blend_mode = BlendMode::exclusive_or;
    lowering = vellum::web::lower_for_canvas_shell(scene);
    plan = vellum::web::canvas_shell_paint_plan(lowering.commands);
    const bool none = std::all_of(plan.group_operations.begin(), plan.group_operations.end(),
                                  [](std::string_view operation) { return operation.empty(); });
    if (!lowering.accepted || !none ||
        plan.opacities != vellum::graphics::effective_opacities(lowering.commands) ||
        lowering.diagnostics.size() != 2U || lowering.losses != 3 ||
        !reported(lowering.diagnostics[0], "group", "hue", "Tier 2") ||
        !reported(lowering.diagnostics[1], "group/inner", "xor", "Tier 2")) {
        std::cerr << "a Tier 2 group was not drawn as normal with a warning and a loss each, got "
                  << lowering.diagnostics.size() << " diagnostics and " << lowering.losses
                  << " losses\n";
        return false;
    }

    scene.root.children[1].blend_mode = BlendMode::multiply;
    scene.root.children[1].children[1].blend_mode = BlendMode::screen;
    const vellum::web::CanvasShellCapabilities older{.group_compositing = false};
    lowering = vellum::web::lower_for_canvas_shell(scene, older);
    plan = vellum::web::canvas_shell_paint_plan(lowering.commands, older);
    const bool flat = std::all_of(plan.group_operations.begin(), plan.group_operations.end(),
                                  [](std::string_view operation) { return operation.empty(); });
    if (!lowering.accepted || !flat || lowering.diagnostics.size() != 2U ||
        lowering.losses != 3 ||
        plan.opacities != vellum::graphics::effective_opacities(lowering.commands) ||
        !reported(lowering.diagnostics[0], "group", "multiply", "no blendBegin and blendEnd") ||
        !reported(lowering.diagnostics[1], "group/inner", "screen", "no blendBegin and blendEnd")) {
        std::cerr << "a backend without group hooks did not draw blends as normal with a "
                     "warning and a loss each\n";
        return false;
    }

    scene.scene_version = 5U;
    const auto refused = vellum::web::lower_for_canvas_shell(scene);
    const auto native = vellum::graphics::accept_scene(scene);
    if (refused.accepted || !refused.commands.empty() || refused.diagnostics.size() != 1U ||
        refused.diagnostics[0].code != vellum::graphics::kSceneVersionMismatchCode ||
        refused.reason != native.reason ||
        refused.reason != "scene version 5 cannot carry the blend mode at group") {
        std::cerr << "a version 5 scene with a blend mode was not refused like the native "
                     "renderer\n";
        return false;
    }
    return true;
}

bool gradient_reports_and_version_gate() {
    using vellum::graphics::Color;
    using vellum::graphics::GradientKind;
    using vellum::graphics::LinearGradient;
    using vellum::graphics::PathGeometry;
    using vellum::graphics::PathVerb;
    auto gradient = [](GradientKind kind) {
        LinearGradient value;
        value.kind = kind;
        value.stops = {{.position = 0.0F, .color = Color::hex(0xFF0000)},
                       {.position = 1.0F, .color = Color::hex(0x0000FF)}};
        return value;
    };
    auto radial = [&](float radius_x, float radius_y) {
        auto value = gradient(GradientKind::radial);
        value.radius_x = radius_x;
        value.radius_y = radius_y;
        return value;
    };
    Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "ellipse", .kind = SceneNode::Kind::rectangle,
        .bounds = {0, 0, 16, 16}, .fill_gradients = {radial(0.5F, 0.25F)}});
    scene.root.children.push_back({
        .id = "circle", .kind = SceneNode::Kind::rectangle,
        .bounds = {48, 0, 16, 32}, .fill_gradients = {radial(0.5F, 0.25F)}});
    scene.root.children.push_back({
        .id = "two", .kind = SceneNode::Kind::rectangle,
        .bounds = {16, 0, 16, 16}, .fill_gradients = {gradient(GradientKind::two_circle_radial)}});
    scene.root.children.push_back({
        .id = "conic", .kind = SceneNode::Kind::rectangle,
        .bounds = {32, 0, 16, 16}, .fill_gradients = {gradient(GradientKind::conic)}});
    auto path = std::make_shared<PathGeometry>();
    path->verbs = {PathVerb::move, PathVerb::line};
    path->values = {0, 0, 10, 0};
    path->stroke.width = 2.0F;
    path->stroke.gradient = gradient(GradientKind::linear);
    scene.root.children.push_back({
        .id = "stroke", .kind = SceneNode::Kind::path,
        .bounds = {0, 16, 16, 16}, .path = path});
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    std::string ellipse_message;
    std::string circle_message;
    bool saw_two = false;
    bool saw_conic = false;
    bool saw_stroke = false;
    for (const auto& diagnostic : lowering.diagnostics) {
        if (diagnostic.code != vellum::web::kGradientNotExpressibleCode) continue;
        if (diagnostic.node_id == "ellipse") ellipse_message = diagnostic.message;
        if (diagnostic.node_id == "circle") circle_message = diagnostic.message;
        saw_two |= diagnostic.node_id == "two" &&
                   diagnostic.message.find("two-circle") != std::string::npos;
        saw_conic |= diagnostic.node_id == "conic" &&
                     diagnostic.message.find("conic") != std::string::npos;
        saw_stroke |= diagnostic.node_id == "stroke" && diagnostic.property == "strokeGradient";
    }
    if (!lowering.accepted || lowering.diagnostics.size() != 5U ||
        ellipse_message.find("elliptical radial") == std::string::npos ||
        circle_message.find("radial gradient") == std::string::npos ||
        circle_message.find("elliptical") != std::string::npos || !saw_two || !saw_conic ||
        !saw_stroke || lowering.losses != 5) {
        std::cerr << "canvas shell did not report each gradient fidelity loss: "
                  << lowering.diagnostics.size() << " diagnostics, " << lowering.losses
                  << " losses\n";
        return false;
    }
    {
        // A mask's own commands are skipped, never drawn as a base colour, so
        // a gradient on them is not reported; only the mask itself is.
        Scene masked;
        masked.root.id = "root";
        SceneNode group{.id = "g", .kind = SceneNode::Kind::group};
        group.children.push_back({
            .id = "g/content", .kind = SceneNode::Kind::rectangle,
            .bounds = {0, 0, 16, 16}, .fill = Color::hex(0xFFFFFF)});
        SceneNode mask{
            .id = "g/mask", .kind = SceneNode::Kind::rectangle,
            .bounds = {0, 0, 16, 16}, .fill_gradients = {gradient(GradientKind::conic)}};
        mask.mask = vellum::graphics::MaskMode::alpha;
        group.children.push_back(std::move(mask));
        masked.root.children.push_back(std::move(group));
        const auto masked_lowering = vellum::web::lower_for_canvas_shell(masked);
        const bool any_gradient = std::any_of(
            masked_lowering.diagnostics.begin(), masked_lowering.diagnostics.end(),
            [](const LoweringDiagnostic& diagnostic) {
                return diagnostic.code == vellum::web::kGradientNotExpressibleCode;
            });
        if (!masked_lowering.accepted || any_gradient ||
            masked_lowering.diagnostics.size() != 1U ||
            masked_lowering.diagnostics[0].code != vellum::web::kMaskNotCompositedCode) {
            std::cerr << "a gradient inside a skipped mask was reported as drawn: "
                      << masked_lowering.diagnostics.size() << " diagnostics\n";
            return false;
        }
    }
    scene.scene_version = 5U;
    const auto refused = vellum::web::lower_for_canvas_shell(scene);
    if (refused.accepted || refused.diagnostics.size() != 1U ||
        refused.diagnostics[0].code != vellum::graphics::kSceneVersionMismatchCode) {
        std::cerr << "version 5 gradient scene was accepted\n";
        return false;
    }

    Scene legacy;
    legacy.scene_version = 5U;
    legacy.root.id = "root";
    auto plain = gradient(GradientKind::linear);
    plain.repeating = true;
    plain.repeat_length = 4.0F;
    legacy.root.children.push_back({
        .id = "plain", .kind = SceneNode::Kind::rectangle,
        .bounds = {0, 0, 16, 16}, .fill_gradients = {plain}});
    const auto plain_lowering = vellum::web::lower_for_canvas_shell(legacy);
    if (!plain_lowering.accepted || !plain_lowering.diagnostics.empty() ||
        plain_lowering.losses != 0) {
        std::cerr << "a plain linear fill changed the legacy loss count or diagnostics\n";
        return false;
    }
    return true;
}

std::shared_ptr<const vellum::graphics::PathGeometry> rectangle_path(float width = 10.0F) {
    vellum::graphics::PathGeometry geometry;
    if (!vellum::graphics::parse_svg_path_data("M0 0H10V10H0Z", geometry)) return nullptr;
    geometry.stroke.width = width;
    return std::make_shared<const vellum::graphics::PathGeometry>(std::move(geometry));
}

vellum::graphics::LinearGradient ramp(vellum::graphics::GradientKind kind) {
    vellum::graphics::LinearGradient gradient;
    gradient.kind = kind;
    if (kind == vellum::graphics::GradientKind::linear) gradient.angle_degrees = 90.0F;
    gradient.stops = {{.position = 0.0F, .color = vellum::graphics::Color::hex(0xFF0000)},
                      {.position = 1.0F, .color = vellum::graphics::Color::hex(0x0000FF)}};
    return gradient;
}

Scene vector_scene(std::vector<vellum::graphics::VectorOp> ops) {
    Scene scene;
    scene.width = 300.0F;
    scene.height = 200.0F;
    scene.root.id = "root";
    auto asset = std::make_shared<vellum::graphics::VectorAsset>();
    asset->content_hash = kHash;
    asset->view_box = {0.0F, 0.0F, 100.0F, 50.0F};
    asset->ops = std::move(ops);
    SceneNode node{.id = "icon", .kind = SceneNode::Kind::vector,
                   .bounds = {10.0F, 20.0F, 200.0F, 100.0F}};
    node.vector = std::move(asset);
    scene.root.children.push_back(std::move(node));
    return scene;
}

bool contains(const std::string& text, std::string_view part) {
    if (text.find(part) != std::string::npos) return true;
    std::cerr << "the canvas plan lacks " << part << ": " << text << "\n";
    return false;
}

// The canvas shell draws a compiled vector asset from a plan the shared core
// writes, maps gradients exactly where canvas 2D can, and names each
// approximation it makes.
bool vector_plans() {
    using vellum::graphics::Affine2D;
    using vellum::graphics::Color;
    using vellum::graphics::GradientKind;
    using vellum::graphics::VectorOp;
    const auto solid = [](std::uint32_t value) {
        return vellum::graphics::VectorPaint{.color = Color::hex(value)};
    };
    auto repeating = ramp(GradientKind::linear);
    repeating.spread = vellum::graphics::GradientSpread::repeat;
    auto concentric = ramp(GradientKind::two_circle_radial);
    const std::vector<VectorOp> lossy{
        {.kind = VectorOp::Kind::push_group, .transform = Affine2D{.tx = 5.0F, .ty = 5.0F},
         .opacity = 0.5F, .clip = rectangle_path(0.0F)},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F), .fill = solid(0xFF0000)},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(2.0F), .fill = solid(0x0000FF),
         .stroke = solid(0x000000)},
        {.kind = VectorOp::Kind::pop_group},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F),
         .fill = {.gradient = ramp(GradientKind::linear),
                  .gradient_transform = {.a = 30.0F, .d = 20.0F, .tx = 60.0F, .ty = 20.0F}}},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F),
         .fill = {.gradient = concentric, .gradient_transform = {.a = 10.0F, .d = 10.0F}}},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F),
         .fill = {.gradient = concentric, .gradient_transform = {.a = 30.0F, .d = 10.0F}}},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(1.0F),
         .stroke = {.gradient = concentric, .gradient_transform = {.a = 30.0F, .d = 10.0F}}},
        {.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F),
         .fill = {.gradient = repeating, .gradient_transform = {.a = 10.0F, .d = 10.0F}}},
    };
    const auto scene = vector_scene(lossy);
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    if (!lowering.accepted || lowering.commands.size() != 1U ||
        lowering.commands[0].kind != vellum::graphics::PaintCommand::Kind::vector) {
        std::cerr << "the vector scene did not lower to one vector command: " << lowering.reason
                  << "\n";
        return false;
    }
    const auto plan = vellum::web::lower_vector_for_canvas(lowering.commands[0], 0.75F);
    if (!contains(plan.json, "\"id\":\"icon\"") ||
        !contains(plan.json, "\"matrix\":[2,0,0,2,10,20]") ||
        !contains(plan.json, "\"alpha\":0.75") ||
        !contains(plan.json, "{\"op\":\"save\",\"alpha\":0.5,\"transform\":[1,0,0,1,5,5]}") ||
        !contains(plan.json, "{\"op\":\"clip\",\"d\":\"M0 0L10 0L10 10L0 10Z\",\"rule\":\"nonzero\"}") ||
        !contains(plan.json, "\"paint\":{\"color\":[1,0,0,1]}") ||
        !contains(plan.json, "\"linear\":[60,30,90,30]") ||
        !contains(plan.json, "\"radial\":[5,5,0,5,5,5]") ||
        !contains(plan.json, "\"space\":[30,0,0,10,0,0]") ||
        !contains(plan.json, "{\"op\":\"stroke\",\"d\":\"M0 0L10 0L10 10L0 10Z\",\"width\":2,"
                             "\"join\":\"miter\",\"cap\":\"butt\",\"miter\":4,")) {
        return false;
    }
    if (plan.losses.size() != 3U || lowering.losses != 1) {
        std::cerr << "expected three vector approximations counted as one loss, got "
                  << plan.losses.size() << " and " << lowering.losses << "\n";
        return false;
    }
    const auto vector_diagnostics = std::count_if(
        lowering.diagnostics.begin(), lowering.diagnostics.end(), [](const LoweringDiagnostic& d) {
            return d.code == vellum::web::kVectorApproximatedCode && d.node_id == "icon";
        });
    if (vector_diagnostics != 3) {
        std::cerr << "expected three vector approximation diagnostics, got " << vector_diagnostics
                  << "\n";
        return false;
    }
    const auto exact = vellum::web::lower_for_canvas_shell(vector_scene({lossy[4], lossy[5],
                                                                         lossy[6]}));
    if (!exact.accepted || exact.losses != 0 || !exact.diagnostics.empty()) {
        std::cerr << "an exactly drawn vector asset reported a loss\n";
        return false;
    }
    auto controlled = vector_scene({lossy[4]});
    controlled.root.children[0].id = std::string("a\nb\tc\x01" "d\"e\\f");
    const auto escaped = vellum::web::lower_for_canvas_shell(controlled);
    const auto escaped_plan = vellum::web::lower_vector_for_canvas(escaped.commands.at(0), 1.0F);
    if (!contains(escaped_plan.json, "\"id\":\"a\\u000ab\\u0009c\\u0001d\\\"e\\\\f\"")) return false;
    for (const char character : escaped_plan.json) {
        if (static_cast<unsigned char>(character) < 0x20U) {
            std::cerr << "the canvas plan holds a raw control character\n";
            return false;
        }
    }
    auto older = vector_scene({lossy[4]});
    older.scene_version = 8U;
    const auto refused = vellum::web::lower_for_canvas_shell(older);
    if (refused.accepted || !refused.commands.empty() || refused.diagnostics.size() != 1U ||
        refused.diagnostics[0].code != vellum::graphics::kSceneVersionMismatchCode) {
        std::cerr << "the shell drew a version 8 scene with a vector node\n";
        return false;
    }
    return true;
}

// The builder's entry points keep a rendered Vector node's opacity and tell
// an empty clip, which hides its group, from no clip at all.
bool vector_builder_helpers() {
    using vellum::graphics::VectorOp;
    const auto empty = vellum::web::vector_group_op({}, 1.0F, true, "", false);
    const auto none = vellum::web::vector_group_op({}, 1.0F, false, "", false);
    if (!empty || !empty->clip || !empty->clip->verbs.empty() || !none || none->clip) {
        std::cerr << "an empty clip and no clip were not told apart\n";
        return false;
    }
    if (vellum::web::vector_group_op({}, 1.0F, true, "Q", false)) {
        std::cerr << "malformed clip path data was accepted\n";
        return false;
    }
    const auto red = VectorOp{.kind = VectorOp::Kind::path, .path = rectangle_path(0.0F),
                              .fill = {.color = vellum::graphics::Color::hex(0xFF0000)}};
    auto scene = vector_scene({*empty, red, {.kind = VectorOp::Kind::pop_group}});
    auto node = vellum::web::vector_node("icon", {10.0F, 20.0F, 200.0F, 100.0F}, 0.5F);
    node.vector = scene.root.children[0].vector;
    scene.root.children[0] = std::move(node);
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    if (!lowering.accepted || lowering.commands.size() != 1U) {
        std::cerr << "the builder's vector node did not lower: " << lowering.reason << "\n";
        return false;
    }
    const auto opacities = vellum::graphics::effective_opacities(lowering.commands);
    const auto plan = vellum::web::lower_vector_for_canvas(lowering.commands[0], opacities[0]);
    if (!contains(plan.json, "\"alpha\":0.5,") ||
        !contains(plan.json, "{\"op\":\"clip\",\"d\":\"\",\"rule\":\"nonzero\"}")) {
        return false;
    }
    // The proof digest tells the node's opacity apart, and an opaque frame
    // hashes the same each time.
    const auto digest = [&](float opacity) {
        auto faded = vector_scene({red});
        faded.root.children[0].opacity = opacity;
        return vellum::web::command_digest(vellum::web::lower_for_canvas_shell(faded).commands);
    };
    if (digest(1.0F) != digest(1.0F) || digest(1.0F) == digest(0.5F) ||
        digest(0.5F) == digest(0.25F)) {
        std::cerr << "the command digest does not carry a vector node's opacity\n";
        return false;
    }
    return true;
}
}  // namespace

// The shell draws a scene with tracks at its clock, as the native renderer
// does, with no loss of its own.
bool track_evaluation() {
    using vellum::graphics::AnimatedProperty;
    using vellum::graphics::AnimationTrack;
    using vellum::graphics::kSceneVersionMismatchCode;
    Scene scene;
    scene.width = 16.0F;
    scene.height = 16.0F;
    scene.root.id = "root";
    SceneNode fader{.id = "fader", .kind = SceneNode::Kind::rectangle,
                    .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
                    .fill = vellum::graphics::Color::hex(0xFFFFFF)};
    fader.tracks.push_back(AnimationTrack{
        .property = AnimatedProperty::opacity,
        .keyframes = {{.time = 0.0, .value = 0.0F}, {.time = 2.0, .value = 1.0F}}});
    scene.root.children.push_back(fader);
    scene.clock = 0.5;
    const auto quarter = vellum::web::lower_for_canvas_shell(scene);
    if (!quarter.accepted || quarter.commands.size() != 1U || quarter.commands[0].opacity != 0.25F ||
        quarter.losses != 0 || !quarter.diagnostics.empty()) {
        std::cerr << "the shell did not draw an opacity track at its clock without loss\n";
        return false;
    }
    scene.clock = 1.5;
    const auto later = vellum::web::lower_for_canvas_shell(scene);
    if (!later.accepted || later.commands.size() != 1U || later.commands[0].opacity != 0.75F) {
        std::cerr << "control: the shell drew the same frame at a later clock\n";
        return false;
    }
    auto older = scene;
    older.scene_version = 10U;
    return refused(older, "scene version 10 cannot carry the animation tracks at fader",
                   kSceneVersionMismatchCode, "fader");
}

bool effect_lowering() {
    using vellum::graphics::EffectKind;
    using vellum::graphics::NodeEffect;
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.width = 200.0F;
    scene.height = 100.0F;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, 200.0F, 100.0F}};
    NodeEffect swap{.kind = EffectKind::color_matrix};
    swap.matrix[0] = 0.0F;
    swap.matrix[2] = 1.0F;
    SceneNode outer{.id = "outer", .kind = SceneNode::Kind::group,
                    .bounds = {0.0F, 0.0F, 100.0F, 100.0F}};
    outer.effects = {{.kind = EffectKind::blur, .sigma = 2.0F}, swap,
                     {.kind = EffectKind::drop_shadow, .sigma = 1.0F, .offset_x = 2.0F}};
    SceneNode inner{.id = "inner", .kind = SceneNode::Kind::group,
                    .bounds = {10.0F, 10.0F, 40.0F, 40.0F}};
    inner.effects = {{.kind = EffectKind::blur, .sigma = 3.0F}};
    inner.children.push_back({.id = "inner/box", .kind = SceneNode::Kind::rectangle,
                              .bounds = {0.0F, 0.0F, 10.0F, 10.0F},
                              .fill = vellum::graphics::Color::hex(0xFF0000)});
    outer.children.push_back(std::move(inner));
    outer.children.push_back({.id = "outer/box", .kind = SceneNode::Kind::rectangle,
                              .bounds = {60.0F, 60.0F, 10.0F, 10.0F},
                              .fill = vellum::graphics::Color::hex(0x00FF00)});
    SceneNode pane{.id = "pane", .kind = SceneNode::Kind::group,
                   .bounds = {120.0F, 10.0F, 60.0F, 60.0F}};
    pane.effects = {{.kind = EffectKind::material,
                     .material = std::string(vellum::graphics::kFrostedGlassMaterial)}};
    scene.root.children.push_back(std::move(outer));
    scene.root.children.push_back(std::move(pane));
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    if (!lowering.accepted || lowering.filters.size() != lowering.commands.size()) {
        std::cerr << "an effect scene was not lowered: " << lowering.reason << '\n';
        return false;
    }
    std::string inner_filter;
    std::string outer_filter;
    for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
        if (lowering.commands[index].node_id == "inner/box") inner_filter = lowering.filters[index];
        if (lowering.commands[index].node_id == "outer/box") outer_filter = lowering.filters[index];
    }
    const std::string expected_outer = "blur(2.000000px) url(#vellum-color-matrix-0)";
    if (outer_filter != expected_outer ||
        inner_filter != "blur(3.000000px) " + expected_outer) {
        std::cerr << "blur and colour matrix filters were not composed innermost first: '"
                  << inner_filter << "' and '" << outer_filter << "'\n";
        return false;
    }
    if (lowering.color_matrices.size() != 1U ||
        lowering.color_matrices[0].id != "vellum-color-matrix-0" ||
        lowering.color_matrices[0].values.rfind("0.000000 0.000000 1.000000", 0) != 0) {
        std::cerr << "the colour matrix was not handed to the shell in SVG order\n";
        return false;
    }
    int not_drawn = 0;
    bool shadow = false;
    bool material = false;
    for (const auto& diagnostic : lowering.diagnostics) {
        if (diagnostic.code != vellum::web::kEffectNotDrawnCode) continue;
        ++not_drawn;
        shadow = shadow || (diagnostic.node_id == "outer" &&
                            diagnostic.message.find("drop shadow") != std::string::npos);
        material = material ||
                   (diagnostic.node_id == "pane" &&
                    diagnostic.message.find("vellum.frosted-glass") != std::string::npos);
    }
    if (not_drawn != 2 || !shadow || !material) {
        std::cerr << "the shell did not report the drop shadow and the material it skips\n";
        return false;
    }
    return true;
}

int main() {
    if (!image_refusals()) return 1;
    if (!vector_plans()) return 1;
    if (!vector_builder_helpers()) return 1;
    if (!track_evaluation()) return 1;
    if (!mask_reports()) return 1;
    if (!blend_lowering()) return 1;
    if (!gradient_reports_and_version_gate()) return 1;
    if (!effect_lowering()) return 1;
    using vellum::graphics::Affine2D;
    using vellum::graphics::CornerRadii;
    using vellum::graphics::SceneNode;
    const auto box = [](std::string id) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .corner_radius = 2.0F,
        };
    };
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back(box("plain"));
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .text = "Hi",
    });
    if (vellum::web::canvas_shell_loss_count(
            vellum::graphics::make_paint_commands(scene)) != 0) {
        std::cerr << "a plain scene reported a loss\n";
        return 1;
    }

    auto turned = box("turned");
    turned.transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    auto corners = box("corners");
    corners.corner_radii = CornerRadii{1.0F, 0.0F, 0.0F, 0.0F};
    auto faded_single = box("faded-single");
    faded_single.opacity = 0.5F;
    scene.root.children.push_back(turned);
    scene.root.children.push_back(corners);
    scene.root.children.push_back(faded_single);
    scene.root.children.push_back({
        .id = "group",
        .kind = SceneNode::Kind::group,
        .children = {box("group/a"), box("group/b")},
        .opacity = 0.5F,
    });
    auto mesh = std::make_shared<vellum::graphics::TriangleMesh>();
    mesh->vertices = {{}, {.x = 1.0F}, {.y = 1.0F}};
    mesh->indices = {0, 1, 2};
    scene.root.children.push_back({
        .id = "mesh", .kind = SceneNode::Kind::mesh, .mesh = mesh,
    });
    scene.root.children.push_back({
        .id = "shaded",
        .kind = SceneNode::Kind::rectangle,
        .box_shadows = {{.blur_radius = 4.0F}},
    });
    auto outline = std::make_shared<vellum::graphics::PathGeometry>();
    outline->verbs = {vellum::graphics::PathVerb::move, vellum::graphics::PathVerb::line};
    outline->values = {0.0F, 0.0F, 4.0F, 4.0F};
    scene.root.children.push_back({
        .id = "outline", .kind = SceneNode::Kind::path, .path = outline,
    });
    const auto losses = vellum::web::canvas_shell_loss_count(
        vellum::graphics::make_paint_commands(scene));
    if (losses != 5) {
        std::cerr << "expected 5 losses, got " << losses << '\n';
        return 1;
    }

    scene.root.children.push_back({
        .id = "faded-path-group",
        .kind = SceneNode::Kind::group,
        .children = {box("faded-path-group/box"),
                     {.id = "faded-path-group/outline", .kind = SceneNode::Kind::path,
                      .path = outline}},
        .opacity = 0.5F,
    });
    auto layered = vellum::graphics::make_paint_commands(scene);
    using Kind = vellum::graphics::PaintCommand::Kind;
    const auto wrapped_path = std::find_if(layered.begin(), layered.end(), [](const auto& command) {
        return command.node_id == "faded-path-group/outline";
    });
    if (layered.empty() || wrapped_path == layered.end() || wrapped_path->kind != Kind::path ||
        (wrapped_path - 2)->kind != Kind::begin_layer ||
        !vellum::web::canvas_shell_loses(*wrapped_path) ||
        vellum::web::canvas_shell_loss_count(layered) != 7) {
        std::cerr << "a path inside a faded layer was not counted with its layer: "
                  << vellum::web::canvas_shell_loss_count(layered) << '\n';
        return 1;
    }

    scene.root.children.push_back({
        .id = "logo",
        .kind = SceneNode::Kind::image,
        .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
        .image = vellum::graphics::ImageReference{.content_hash = "sha256:" + std::string(64, 'a')},
    });
    layered = vellum::graphics::make_paint_commands(scene);
    if (layered.empty() || layered.back().kind != Kind::image ||
        !vellum::web::canvas_shell_loses(layered.back()) ||
        vellum::web::canvas_shell_loss_count(layered) != 8) {
        std::cerr << "an image was not counted as a loss: "
                  << vellum::web::canvas_shell_loss_count(layered) << '\n';
        return 1;
    }

    // A clip counts once; a mask counts once and its commands are skipped,
    // so the shell never draws mask coverage as visible paint. The masked
    // group's layer is flattened and counts once, as any layer does.
    vellum::graphics::Scene grouped;
    grouped.root.id = "root";
    auto clipped = box("clipped");
    clipped.clip = vellum::graphics::NodeClip{.rect = {0.0F, 0.0F, 2.0F, 2.0F}};
    grouped.root.children.push_back(clipped);
    SceneNode masked{.id = "masked", .kind = SceneNode::Kind::group};
    masked.children.push_back(box("masked/content"));
    auto mask = box("masked/mask");
    mask.mask = vellum::graphics::MaskMode::luminance;
    mask.children.push_back(box("masked/mask/inner"));
    auto nested = box("masked/mask/nested");
    nested.mask = vellum::graphics::MaskMode::alpha;
    mask.children.push_back(nested);
    masked.children.push_back(mask);
    grouped.root.children.push_back(masked);
    const auto grouped_commands = vellum::graphics::make_paint_commands(grouped);
    const auto skipped = vellum::web::canvas_shell_masked(grouped_commands);
    std::vector<std::string> drawn;
    for (std::size_t index = 0; index < grouped_commands.size(); ++index) {
        if (!skipped[index] && vellum::graphics::draws_content(grouped_commands[index].kind)) {
            drawn.push_back(grouped_commands[index].node_id);
        }
    }
    if (drawn != std::vector<std::string>{"clipped", "masked/content"} ||
        vellum::web::canvas_shell_loss_count(grouped_commands) != 3) {
        std::cerr << "clips and masks were not lowered as declared: "
                  << vellum::web::canvas_shell_loss_count(grouped_commands) << '\n';
        return 1;
    }
    return 0;
}
