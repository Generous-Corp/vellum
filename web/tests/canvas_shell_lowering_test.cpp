#include "canvas_shell_lowering.hpp"

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

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

// Every blend mode has a canvas operation except `clear` and `destination`; the
// shell flattens a blended layer, so each command inside it takes the layer's
// operation, the innermost blended layer wins, and a mode the canvas lacks is
// drawn as normal with one warning naming the node.
bool blend_lowering() {
    using vellum::graphics::BlendMode;
    using vellum::graphics::kAllBlendModes;
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
        const bool expected_lack = mode == BlendMode::clear || mode == BlendMode::destination;
        lacking += operation ? 0 : 1;
        if (operation.has_value() == expected_lack ||
            (operation && operation->empty())) {
            std::cerr << "blend mode " << vellum::graphics::to_string(mode)
                      << " has the wrong canvas operation\n";
            return false;
        }
    }
    if (lacking != 2 ||
        vellum::web::canvas_shell_blend_operation(BlendMode::normal) != "source-over" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::color_dodge) != "color-dodge" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::plus) != "lighter" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::source) != "copy" ||
        vellum::web::canvas_shell_blend_operation(BlendMode::destination_in) != "destination-in") {
        std::cerr << "a blend mode maps to the wrong canvas operation\n";
        return false;
    }

    vellum::graphics::Scene scene;
    scene.root.id = "root";
    auto group = box("group", BlendMode::multiply);
    group.children.push_back(box("group/plain", BlendMode::normal));
    group.children.push_back(box("group/inner", BlendMode::screen));
    scene.root.children.push_back(box("before", BlendMode::normal));
    scene.root.children.push_back(std::move(group));
    scene.root.children.push_back(box("after", BlendMode::normal));
    auto lowering = vellum::web::lower_for_canvas_shell(scene);
    std::vector<std::string> drawn;
    for (std::size_t index = 0; index < lowering.commands.size(); ++index) {
        if (!vellum::graphics::draws_content(lowering.commands[index].kind)) continue;
        drawn.push_back(lowering.commands[index].node_id + "=" +
                        std::string(vellum::web::canvas_shell_blend_operations(
                            lowering.commands)[index]));
    }
    const std::vector<std::string> expected{
        "before=", "group=multiply", "group/plain=multiply", "group/inner=screen", "after="};
    if (!lowering.accepted || !lowering.diagnostics.empty() || drawn != expected) {
        std::cerr << "a blended group was not flattened to per-command operations\n";
        for (const auto& entry : drawn) std::cerr << "  " << entry << '\n';
        return false;
    }

    scene.root.children[1].blend_mode = BlendMode::clear;
    scene.root.children[1].children[1].blend_mode = BlendMode::destination;
    lowering = vellum::web::lower_for_canvas_shell(scene);
    const auto operations = vellum::web::canvas_shell_blend_operations(lowering.commands);
    const bool plain = std::all_of(operations.begin(), operations.end(),
                                   [](std::string_view operation) { return operation.empty(); });
    const auto reported = [&](std::size_t index, std::string_view node, std::string_view mode) {
        const auto& diagnostic = lowering.diagnostics[index];
        return diagnostic.code == vellum::graphics::kBlendModeFallbackCode &&
               diagnostic.node_id == node && diagnostic.property == "blendMode" &&
               diagnostic.severity == LoweringDiagnostic::Severity::warning &&
               diagnostic.disposition == LoweringDiagnostic::Disposition::lowered &&
               diagnostic.message.find(std::string("blend mode ") + std::string(mode)) !=
                   std::string::npos &&
               diagnostic.message.find("drawn as normal") != std::string::npos;
    };
    if (!lowering.accepted || !plain || lowering.diagnostics.size() != 2U ||
        !reported(0U, "group", "clear") || !reported(1U, "group/inner", "destination")) {
        std::cerr << "a blend mode without a canvas operation was not drawn as normal with a "
                     "warning each, got "
                  << lowering.diagnostics.size() << " diagnostics\n";
        return false;
    }

    // Negative control: a scene that uses only modes the canvas has reports
    // nothing, so the warnings above come from the missing operations.
    scene.root.children[1].blend_mode = BlendMode::hue;
    scene.root.children[1].children[1].blend_mode = BlendMode::exclusive_or;
    if (!vellum::web::lower_for_canvas_shell(scene).diagnostics.empty()) {
        std::cerr << "a scene of supported blend modes reported a fallback\n";
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
}  // namespace

int main() {
    if (!image_refusals()) return 1;
    if (!mask_reports()) return 1;
    if (!blend_lowering()) return 1;
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
