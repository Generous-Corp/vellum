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

}  // namespace

int main() {
    if (!image_refusals()) return 1;
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
    return 0;
}
