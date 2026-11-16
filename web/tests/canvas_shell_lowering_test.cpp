#include "canvas_shell_lowering.hpp"

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <algorithm>
#include <cmath>
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
}  // namespace

// Draws `commands` as the shell does, each rectangle with its colour times
// its opacity source-over, and checks every pixel against the plot drawn as
// one group: its rectangles composited at full opacity, then faded once by
// `opacity`. It also checks that the shell's rectangles never overlap, so no
// pixel is blended twice.
bool composites_once(const std::vector<vellum::graphics::PaintCommand>& commands,
                     const Scene& scene, const std::string& id, float opacity) {
    using vellum::graphics::Color;
    using vellum::graphics::PlotLayer;
    const auto& node = *vellum::graphics::find_node(scene, id);
    const auto& plot = *node.plot;
    const auto geometry = vellum::graphics::plot_geometry(plot, node.fill, node.bounds, 1.0F);
    const auto width = static_cast<int>(scene.width);
    const auto height = static_cast<int>(scene.height);
    const auto covers = [](const vellum::graphics::Rect& rect, int x, int y) {
        const float cx = static_cast<float>(x) + 0.5F;
        const float cy = static_cast<float>(y) + 0.5F;
        return cx >= rect.x && cx < rect.x + rect.width && cy >= rect.y &&
               cy < rect.y + rect.height;
    };
    const Color clear = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    const auto opacities = vellum::graphics::effective_opacities(commands);
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            Color group = clear;
            for (const auto& piece : geometry.rects) {
                if (!covers(piece.rect, x, y)) continue;
                Color color = node.fill;
                if (piece.layer == PlotLayer::fill) color = plot.fill_color;
                if (piece.layer == PlotLayer::peak) color = plot.peak_color;
                if (piece.layer == PlotLayer::line) color = plot.line_color;
                group = vellum::web::canvas_shell_over(color, group);
            }
            group.alpha *= opacity;
            Color shell = clear;
            int hits = 0;
            for (std::size_t index = 0; index < commands.size(); ++index) {
                const auto& command = commands[index];
                if ((command.node_id != id && command.node_id != id + "/background") ||
                    !vellum::graphics::draws_content(command.kind) ||
                    !covers(command.bounds, x, y)) {
                    continue;
                }
                Color color = command.fill;
                color.alpha *= opacities[index];
                if (!(color.alpha > 0.0F)) continue;
                ++hits;
                shell = vellum::web::canvas_shell_over(color, shell);
            }
            const auto near = [](float a, float b) { return std::abs(a - b) < 1e-4F; };
            if (hits > 1 || !near(shell.alpha, group.alpha) ||
                (group.alpha > 0.0F &&
                 (!near(shell.red, group.red) || !near(shell.green, group.green) ||
                  !near(shell.blue, group.blue)))) {
                std::cerr << "pixel " << x << "," << y << " has " << hits
                          << " shell rectangles and alpha " << shell.alpha << " against "
                          << group.alpha << '\n';
                return false;
            }
        }
    }
    return true;
}

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

// An untransformed plot is drawn as disjoint rectangles that composite it as
// one group with no loss; a turned one is not drawn and is reported; a
// version 11 scene with a plot is refused as the native renderer refuses it.
bool plot_lowering() {
    using vellum::graphics::Color;
    using vellum::graphics::PaintCommand;
    using vellum::graphics::PlotChannelShape;
    using vellum::graphics::PlotSpec;
    using vellum::graphics::PlotStyle;
    Scene scene;
    scene.width = 64.0F;
    scene.height = 32.0F;
    scene.root.id = "root";
    auto spec = std::make_shared<PlotSpec>();
    spec->style = PlotStyle::area;
    spec->shape = PlotChannelShape::vector;
    spec->slot_count = 4;
    spec->samples = {0.25F, 0.5F, 0.75F, 1.0F};
    spec->fill_color = Color::hex(0x22C55E);
    SceneNode plot{.id = "spectrum", .kind = SceneNode::Kind::plot,
                   .bounds = {8.0F, 4.0F, 8.0F, 20.0F}, .fill = Color::hex(0x111827),
                   .opacity = 0.5F};
    plot.plot = spec;
    scene.root.children.push_back(plot);
    const auto lowering = vellum::web::lower_for_canvas_shell(scene);
    if (!lowering.accepted || lowering.losses != 0 || !lowering.diagnostics.empty() ||
        !composites_once(lowering.commands, scene, "spectrum", 0.5F)) {
        std::cerr << "a faded area plot over its background was not composited once\n";
        return false;
    }
    // A translucent peak tick over a meter's level: the overlap is one piece
    // of the composited colour.
    auto meter = std::make_shared<PlotSpec>();
    meter->style = PlotStyle::level_meter;
    meter->shape = PlotChannelShape::meter;
    meter->samples = {0.5F, 0.45F};
    meter->peak_width = 4.0F;
    meter->fill_color = Color::hex(0x22C55E);
    meter->peak_color = Color::hex(0xF8FAFC, 0.5F);
    Scene metered = scene;
    metered.root.children[0].plot = meter;
    const auto level = vellum::web::lower_for_canvas_shell(metered);
    if (!level.accepted || level.losses != 0 ||
        !composites_once(level.commands, metered, "spectrum", 0.5F)) {
        std::cerr << "a faded meter's peak over its level was not composited once\n";
        return false;
    }
    // A gradient background lowers to its own rectangle inside the plot's
    // opacity layer; its colour is flattened with the plot, so the faded
    // overlap is still blended once and the layer costs no loss. A gradient
    // with the version 8 vocabulary is still reported, as one loss.
    Scene shaded = scene;
    shaded.root.children[0].fill_gradients = {
        {.stops = {{.position = 0.0F, .color = Color::hex(0x111827)},
                   {.position = 1.0F, .color = Color::hex(0x334155)}}}};
    const auto linear = vellum::web::lower_for_canvas_shell(shaded);
    const bool layered = std::any_of(linear.commands.begin(), linear.commands.end(),
                                     [](const PaintCommand& command) {
                                         return command.kind == PaintCommand::Kind::begin_layer;
                                     });
    if (!linear.accepted || linear.losses != 0 || layered ||
        !composites_once(linear.commands, shaded, "spectrum", 0.5F)) {
        std::cerr << "a faded plot over a gradient background was not composited once\n";
        return false;
    }
    shaded.root.children[0].fill_gradients[0].kind = vellum::graphics::GradientKind::radial;
    const auto radial = vellum::web::lower_for_canvas_shell(shaded);
    if (!radial.accepted || radial.losses != 1 ||
        !composites_once(radial.commands, shaded, "spectrum", 0.5F)) {
        std::cerr << "a faded plot over a radial background was not composited once with one loss\n";
        return false;
    }
    // A blended plot lowers through the compositing group main wraps every
    // blended node in: its rectangles sit inside the `begin_layer` that
    // carries the blend, which the shell composites with the canvas operation.
    Scene blended = scene;
    blended.root.children[0].blend_mode = vellum::graphics::BlendMode::multiply;
    const auto grouped = vellum::web::lower_for_canvas_shell(blended);
    const auto grouped_plan = vellum::web::canvas_shell_paint_plan(grouped.commands);
    std::size_t begin = grouped.commands.size();
    std::size_t end = grouped.commands.size();
    std::size_t inside = 0;
    bool plot_left = false;
    for (std::size_t index = 0; index < grouped.commands.size(); ++index) {
        const auto& command = grouped.commands[index];
        if (command.kind == PaintCommand::Kind::plot) plot_left = true;
        if (command.kind == PaintCommand::Kind::begin_layer && command.node_id == "spectrum" &&
            command.blend_mode == vellum::graphics::BlendMode::multiply) {
            begin = index;
        }
        if (command.kind == PaintCommand::Kind::end_layer && begin < index && end == grouped.commands.size()) {
            end = index;
        }
        if (command.kind == PaintCommand::Kind::rectangle && command.node_id == "spectrum") {
            if (begin < index && end == grouped.commands.size()) ++inside;
            else plot_left = true;
        }
    }
    if (!grouped.accepted || grouped.losses != 0 || !grouped.diagnostics.empty() || plot_left ||
        begin >= grouped.commands.size() || end >= grouped.commands.size() || inside == 0U ||
        grouped_plan.group_operations[begin].empty()) {
        std::cerr << "a blended plot was not drawn inside its composited blend group\n";
        return false;
    }
    // A Tier 2 blend falls back to normal with main's one warning and loss;
    // the plot itself is still drawn, so no plot diagnostic is added.
    blended.root.children[0].blend_mode = vellum::graphics::BlendMode::hue;
    const auto fallback = vellum::web::lower_for_canvas_shell(blended);
    if (!fallback.accepted || fallback.losses != 1 || fallback.diagnostics.size() != 1U ||
        fallback.diagnostics[0].code != vellum::graphics::kBlendModeFallbackCode ||
        std::any_of(fallback.commands.begin(), fallback.commands.end(),
                    [](const PaintCommand& command) {
                        return command.kind == PaintCommand::Kind::plot;
                    })) {
        std::cerr << "a Tier 2 blended plot did not fall back to normal with one warning\n";
        return false;
    }
    // An ordinary rectangle whose id happens to read like a plot background,
    // drawn just before a faded plot without one, keeps its own paint.
    Scene sibling = scene;
    sibling.root.children.insert(sibling.root.children.begin(),
                                 SceneNode{.id = "spectrum/background",
                                           .kind = SceneNode::Kind::rectangle,
                                           .bounds = {4.0F, 2.0F, 20.0F, 24.0F},
                                           .fill = Color::hex(0xEF4444),
                                           .corner_radius = 3.0F});
    const auto beside = vellum::web::lower_for_canvas_shell(sibling);
    const auto kept = std::find_if(beside.commands.begin(), beside.commands.end(),
                                   [](const PaintCommand& command) {
                                       return command.node_id == "spectrum/background";
                                   });
    if (!beside.accepted || kept == beside.commands.end() ||
        kept->kind != PaintCommand::Kind::rectangle || kept->fill.alpha != 1.0F ||
        kept->opacity != 1.0F || kept->corner_radius != 3.0F || kept->plot_background) {
        std::cerr << "an ordinary rectangle named like a plot background was swallowed\n";
        return false;
    }
    scene.root.children[0].transform =
        vellum::graphics::Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    const auto turned = vellum::web::lower_for_canvas_shell(scene);
    if (!turned.accepted || turned.losses != 1 || turned.diagnostics.size() != 1U ||
        turned.diagnostics[0].code != vellum::web::kPlotNotDrawnCode ||
        turned.diagnostics[0].node_id != "spectrum" || turned.commands.size() != 1U ||
        turned.commands[0].kind != PaintCommand::Kind::plot) {
        std::cerr << "a turned plot was not reported as not drawn\n";
        return false;
    }
    scene.root.children[0].transform.reset();
    scene.scene_version = 11U;
    return refused(scene, "scene version 11 cannot carry the plot node spectrum",
                   vellum::graphics::kSceneVersionMismatchCode, "spectrum");
}

int main() {
    if (!image_refusals()) return 1;
    if (!track_evaluation()) return 1;
    if (!mask_reports()) return 1;
    if (!blend_lowering()) return 1;
    if (!gradient_reports_and_version_gate()) return 1;
    if (!effect_lowering()) return 1;
    if (!plot_lowering()) return 1;
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
