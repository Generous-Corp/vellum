#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace {

using vellum::graphics::Color;
using vellum::graphics::ImageAlpha;
using vellum::graphics::ImageAsset;
using vellum::graphics::ImageFit;
using vellum::graphics::ImageReference;
using vellum::graphics::ImageSampling;
using vellum::graphics::PaintCommand;
using vellum::graphics::Rect;
using vellum::graphics::Scene;
using vellum::graphics::SceneNode;

const std::string kHashA = "sha256:" + std::string(64, 'a');
const std::string kHashB = "sha256:" + std::string(63, '0') + "b";

bool expect(bool ok, const std::string& what) {
    if (!ok) std::cerr << "FAIL: " << what << '\n';
    return ok;
}

bool near(float left, float right) { return std::abs(left - right) < 0.0001F; }

bool near_rect(const Rect& rect, float x, float y, float width, float height) {
    return near(rect.x, x) && near(rect.y, y) && near(rect.width, width) &&
           near(rect.height, height);
}

std::shared_ptr<const ImageAsset> solid_asset(const std::string& hash, std::uint32_t width,
                                              std::uint32_t height) {
    std::vector<std::uint8_t> texels(static_cast<std::size_t>(width) * height * 4U, 255U);
    return vellum::graphics::make_image_asset(hash, width, height, texels,
                                              ImageAlpha::premultiplied);
}

SceneNode image_node(const std::string& id, const std::string& hash, Rect bounds) {
    return {.id = id,
            .kind = SceneNode::Kind::image,
            .bounds = bounds,
            .image = ImageReference{.content_hash = hash}};
}

bool content_hashes() {
    using vellum::graphics::is_content_hash;
    using vellum::graphics::normalize_content_hash;
    static_assert(is_content_hash("sha256:0000000000000000000000000000000000000000000000000000000000000000"));
    static_assert(!is_content_hash("sha256:000000000000000000000000000000000000000000000000000000000000000"));
    static_assert(!is_content_hash("sha256:000000000000000000000000000000000000000000000000000000000000000A"));
    static_assert(!is_content_hash("SHA256:0000000000000000000000000000000000000000000000000000000000000000"));
    static_assert(!is_content_hash("0000000000000000000000000000000000000000000000000000000000000000"));
    bool ok = true;
    const std::string bare(64, 'F');
    ok &= expect(normalize_content_hash(bare) == "sha256:" + std::string(64, 'f'),
                 "a bare upper-case hash normalizes to the prefixed lower-case form");
    ok &= expect(normalize_content_hash("  SHA256:" + bare + "\n") ==
                     "sha256:" + std::string(64, 'f'),
                 "a prefixed hash with whitespace and upper case normalizes");
    ok &= expect(!normalize_content_hash("assets/logo.png") &&
                     !normalize_content_hash(std::string(63, 'a')) &&
                     !normalize_content_hash("sha256:" + std::string(64, 'g')) &&
                     !normalize_content_hash("md5:" + std::string(64, 'a')),
                 "anything but a SHA-256 hash is not an integrity claim");
    return ok;
}

bool asset_construction() {
    bool ok = true;
    // Straight alpha is premultiplied once, rounding to nearest.
    const std::vector<std::uint8_t> straight{200, 100, 1, 128, 255, 255, 255, 0};
    std::string error;
    const auto asset = vellum::graphics::make_image_asset(kHashA, 2, 1, straight,
                                                          ImageAlpha::straight, &error);
    ok &= expect(asset != nullptr && asset->width == 2U && asset->height == 1U &&
                     asset->content_hash == kHashA,
                 "a straight-alpha asset is built: " + error);
    if (asset) {
        const std::vector<std::uint8_t> expected{100, 50, 1, 128, 0, 0, 0, 0};
        ok &= expect(asset->premultiplied_rgba == expected,
                     "straight alpha is multiplied into the colour channels");
        ok &= expect(vellum::graphics::image_asset_is_valid(*asset), "the built asset is valid");
    }
    const std::vector<std::uint8_t> opaque(16, 255);
    ok &= expect(!vellum::graphics::make_image_asset("logo.png", 2, 2, opaque,
                                                     ImageAlpha::premultiplied, &error) &&
                     error.find("content hash") != std::string::npos,
                 "a non-canonical hash is refused");
    ok &= expect(!vellum::graphics::make_image_asset(kHashA, 3, 2, opaque,
                                                     ImageAlpha::premultiplied, &error) &&
                     error.find("dimensions") != std::string::npos,
                 "a buffer that does not match the size is refused");
    ok &= expect(!vellum::graphics::make_image_asset(kHashA, 0, 4, {},
                                                     ImageAlpha::premultiplied, &error),
                 "an empty image is refused");
    ok &= expect(!vellum::graphics::make_image_asset(
                     kHashA, vellum::graphics::kMaxImageDimension + 1U, 1, opaque,
                     ImageAlpha::premultiplied, &error) &&
                     error.find("pixels on a side") != std::string::npos,
                 "an image wider than the dimension budget is refused");
    static_assert(vellum::graphics::kMaxImageDimension == 8192U);
    static_assert(static_cast<std::size_t>(vellum::graphics::kMaxImageDimension) *
                      vellum::graphics::kMaxImageDimension * 4U ==
                  vellum::graphics::kMaxImageBytes);
    ok &= expect(!vellum::graphics::make_image_asset(kHashA, 8192, 8193, opaque,
                                                     ImageAlpha::premultiplied, &error) &&
                     error.find("8192 pixels on a side") != std::string::npos,
                 "an image taller than the texture limit is refused before its buffer is read");
    ok &= expect(!vellum::graphics::make_image_asset(kHashA, 8192, 8192, opaque,
                                                     ImageAlpha::premultiplied, &error) &&
                     error.find("dimensions") != std::string::npos,
                 "an image at the limit is checked against its buffer");
    const std::vector<std::uint8_t> unpremultiplied{10, 0, 0, 5};
    ok &= expect(!vellum::graphics::make_image_asset(kHashA, 1, 1, unpremultiplied,
                                                     ImageAlpha::premultiplied, &error) &&
                     error.find("above its alpha") != std::string::npos,
                 "premultiplied texels with a channel above alpha are refused");

    ImageAsset forged{.content_hash = kHashA, .width = 1, .height = 1,
                      .premultiplied_rgba = {9, 0, 0, 4}};
    ok &= expect(!vellum::graphics::image_asset_is_valid(forged, &error),
                 "a hand-built asset that is not premultiplied is invalid");

    Scene scene;
    scene.images = {solid_asset(kHashA, 1, 1), solid_asset(kHashB, 1, 1)};
    ok &= expect(vellum::graphics::scene_images_are_valid(scene, &error),
                 "distinct valid assets are accepted");
    ok &= expect(vellum::graphics::find_image_asset(scene, kHashB) == scene.images[1] &&
                     !vellum::graphics::find_image_asset(scene, "sha256:" + std::string(64, 'c')),
                 "assets are found by content hash");
    scene.images.push_back(solid_asset(kHashA, 2, 2));
    const vellum::graphics::ImageAssetIndex index(scene);
    ok &= expect(index.find(kHashA) == scene.images[0] &&
                     vellum::graphics::find_image_asset(scene, kHashA) == scene.images[0] &&
                     index.find(kHashB) == scene.images[1] && !index.find("sha256:"),
                 "the index and the linear lookup agree, first entry first");
    ok &= expect(!vellum::graphics::scene_images_are_valid(scene, &error) &&
                     error.find("twice") != std::string::npos,
                 "a hash listed twice is refused");
    scene.images = {nullptr};
    ok &= expect(!vellum::graphics::scene_images_are_valid(scene, &error),
                 "a null asset is refused");
    return ok;
}

bool reference_validity() {
    bool ok = true;
    std::string reason;
    ImageReference reference{.content_hash = kHashA};
    ok &= expect(vellum::graphics::image_reference_is_valid(reference), "a plain reference");
    reference.source_rect = Rect{0.0F, 0.0F, -1.0F, 2.0F};
    ok &= expect(!vellum::graphics::image_reference_is_valid(reference, &reason),
                 "a negative source size is refused");
    reference.source_rect = Rect{NAN, 0.0F, 1.0F, 2.0F};
    ok &= expect(!vellum::graphics::image_reference_is_valid(reference, &reason),
                 "a non-finite source rectangle is refused");
    reference.source_rect.reset();
    reference.fit = static_cast<ImageFit>(9);
    ok &= expect(!vellum::graphics::image_reference_is_valid(reference, &reason),
                 "an unknown fit is refused");
    reference.fit = ImageFit::cover;
    reference.sampling = static_cast<ImageSampling>(9);
    ok &= expect(!vellum::graphics::image_reference_is_valid(reference, &reason),
                 "an unknown sampling is refused");
    reference.sampling = ImageSampling::nearest;
    reference.content_hash = std::string(64, 'a');
    ok &= expect(!vellum::graphics::image_reference_is_valid(reference, &reason),
                 "a bare hash is not canonical in a scene");
    return ok;
}

bool placement() {
    using vellum::graphics::image_placement;
    bool ok = true;
    const Rect box{10.0F, 20.0F, 100.0F, 50.0F};
    ImageReference reference{.content_hash = kHashA};

    auto placed = image_placement(reference, 40, 40, box);
    ok &= expect(placed && near_rect(placed->source, 0, 0, 40, 40) &&
                     near_rect(placed->destination, 10, 20, 100, 50),
                 "fill stretches the image to the box");

    reference.fit = ImageFit::contain;
    placed = image_placement(reference, 40, 40, box);
    ok &= expect(placed && near_rect(placed->source, 0, 0, 40, 40) &&
                     near_rect(placed->destination, 35, 20, 50, 50),
                 "contain scales to the shorter side and centres");

    reference.fit = ImageFit::cover;
    placed = image_placement(reference, 40, 40, box);
    ok &= expect(placed && near_rect(placed->source, 0, 10, 40, 20) &&
                     near_rect(placed->destination, 10, 20, 100, 50),
                 "cover scales to the longer side and crops the overflow from the source");

    reference.fit = ImageFit::none;
    placed = image_placement(reference, 40, 80, box);
    ok &= expect(placed && near_rect(placed->source, 0, 15, 40, 50) &&
                     near_rect(placed->destination, 40, 20, 40, 50),
                 "none draws one pixel per pixel, centred and cropped");

    reference.fit = ImageFit::fill;
    reference.source_rect = Rect{30.0F, -10.0F, 20.0F, 30.0F};
    placed = image_placement(reference, 40, 40, box);
    ok &= expect(placed && near_rect(placed->source, 30, 0, 10, 20) &&
                     near_rect(placed->destination, 10, 20, 100, 50),
                 "a source rectangle past the image is clipped to it");

    reference.source_rect = Rect{50.0F, 0.0F, 10.0F, 10.0F};
    ok &= expect(!image_placement(reference, 40, 40, box),
                 "a source rectangle outside the image draws nothing");
    reference.source_rect.reset();
    ok &= expect(!image_placement(reference, 40, 40, {0, 0, 0, 10}),
                 "an empty box draws nothing");
    return ok;
}

bool versions() {
    bool ok = true;
    static_assert(vellum::graphics::kSceneVersion == 4U);
    static_assert(vellum::graphics::is_known_kind(SceneNode::Kind::image));
    Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back(image_node("logo", kHashA, {0, 0, 4, 4}));
    scene.images = {solid_asset(kHashA, 2, 2)};
    for (const std::uint32_t version : {1U, 2U, 3U}) {
        scene.scene_version = version;
        const auto loss = vellum::graphics::scene_loss_diagnostics(scene);
        const auto* found = vellum::graphics::find_version_mismatch(scene);
        ok &= expect(!vellum::graphics::scene_fields_match_version(scene) &&
                         vellum::graphics::make_paint_commands(scene).empty() &&
                         found != nullptr && found->id == "logo" &&
                         vellum::graphics::find_version_4_kinds(scene.root) == found,
                     "a version " + std::to_string(version) + " scene with an image is refused");
        ok &= expect(loss.size() == 1U &&
                         loss[0].code == vellum::graphics::kSceneVersionMismatchCode &&
                         loss[0].severity ==
                             vellum::graphics::LoweringDiagnostic::Severity::error &&
                         loss[0].node_id == "logo" &&
                         loss[0].message == "scene version " + std::to_string(version) +
                                                " cannot carry the image node logo",
                     "the refusal is reported as a version-mismatch diagnostic");
    }
    scene.scene_version = 4U;
    ok &= expect(vellum::graphics::scene_fields_match_version(scene) &&
                     vellum::graphics::find_version_mismatch(scene) == nullptr &&
                     vellum::graphics::scene_loss_diagnostics(scene).empty() &&
                     vellum::graphics::make_paint_commands(scene).size() == 1U,
                 "a version 4 scene draws its image");

    scene.root.children.push_back({.id = "future", .kind = static_cast<SceneNode::Kind>(42)});
    const auto unknown = vellum::graphics::scene_loss_diagnostics(scene);
    ok &= expect(unknown.size() == 1U && unknown[0].node_id == "future" &&
                     unknown[0].code == vellum::graphics::kSceneVersionMismatchCode,
                 "an unknown kind is reported as a loss even at the current version");
    scene.root.children.pop_back();
    scene.scene_version = vellum::graphics::kSceneVersion + 1U;
    const auto newer = vellum::graphics::scene_loss_diagnostics(scene);
    ok &= expect(newer.size() == 1U && newer[0].message == "scene version 5 is not supported",
                 "a newer scene version is reported");
    return ok;
}

bool commands() {
    bool ok = true;
    Scene scene;
    scene.root.id = "root";
    scene.root.bounds = {5.0F, 6.0F, 100.0F, 100.0F};
    auto present = image_node("present", kHashA, {1.0F, 2.0F, 30.0F, 20.0F});
    present.fill = Color::hex(0x112233);
    present.corner_radius = 4.0F;
    present.image->fit = ImageFit::cover;
    present.image->sampling = ImageSampling::nearest;
    scene.root.children.push_back(present);
    scene.root.children.push_back(image_node("missing", kHashB, {40.0F, 2.0F, 10.0F, 10.0F}));
    scene.images = {solid_asset(kHashA, 8, 8)};

    const auto output = vellum::graphics::make_paint_commands(scene);
    ok &= expect(output.size() == 2U, "each image node emits one command");
    if (output.size() == 2U) {
        const auto& first = output[0];
        ok &= expect(first.kind == PaintCommand::Kind::image && first.node_id == "present" &&
                         near_rect(first.bounds, 6, 8, 30, 20) &&
                         first.image_asset == scene.images[0] && first.image &&
                         first.image->fit == ImageFit::cover &&
                         first.image->sampling == ImageSampling::nearest &&
                         near(first.corner_radii.top_left, 4.0F) &&
                         first.fill.blue == present.fill.blue,
                     "an image command carries its reference, asset, fill and radii");
        ok &= expect(output[1].node_id == "missing" && !output[1].image_asset,
                     "a missing asset leaves the command without one");
        const auto extent = vellum::graphics::paint_extent(first);
        ok &= expect(extent.bounded && near_rect(extent.rect, 6, 8, 30, 20),
                     "an image paints within its box");
        ok &= expect(vellum::graphics::draws_content(PaintCommand::Kind::image) &&
                         vellum::graphics::to_string(PaintCommand::Kind::image) == "image",
                     "an image command draws content");
    }
    auto loss = vellum::graphics::scene_loss_diagnostics(scene);
    ok &= expect(loss.size() == 1U && loss[0].code == vellum::graphics::kImageAssetMissingCode &&
                     loss[0].node_id == "missing" &&
                     loss[0].severity == vellum::graphics::LoweringDiagnostic::Severity::warning,
                 "a missing asset is a warning against its node");
    scene.root.children[0].image->source_rect = Rect{9.0F, 0.0F, 4.0F, 4.0F};
    loss = vellum::graphics::scene_loss_diagnostics(scene);
    ok &= expect(loss.size() == 2U && loss[0].node_id == "present" &&
                     loss[0].message.find("outside its asset") != std::string::npos,
                 "a source rectangle that selects nothing is a warning");
    scene.root.children[0].image->source_rect = Rect{7.0F, 7.0F, 4.0F, 4.0F};
    ok &= expect(vellum::graphics::scene_loss_diagnostics(scene).size() == 1U,
                 "a source rectangle that overlaps the asset is not");

    // A lone faded image folds its opacity; two members get a layer.
    Scene faded;
    faded.root.children.push_back({.id = "group",
                                   .kind = SceneNode::Kind::group,
                                   .children = {image_node("a", kHashA, {0, 0, 4, 4})},
                                   .opacity = 0.5F});
    auto lone = vellum::graphics::make_paint_commands(faded);
    ok &= expect(lone.size() == 1U && near(lone[0].opacity, 0.5F),
                 "a lone faded image folds the opacity into its command");
    faded.root.children[0].children.push_back(image_node("b", kHashA, {2, 2, 4, 4}));
    auto layered = vellum::graphics::make_paint_commands(faded);
    ok &= expect(layered.size() == 4U && layered[0].kind == PaintCommand::Kind::begin_layer &&
                     near_rect(layered[0].bounds, 0, 0, 6, 6),
                 "two faded images share a layer bounded by their boxes");
    return ok;
}

bool damage() {
    using vellum::graphics::compute_scene_damage;
    using vellum::graphics::DamagePropagation;
    bool ok = true;
    const DamagePropagation propagation;
    const Rect frame{0, 0, 200, 100};
    auto node = image_node("logo", kHashA, {10, 10, 20, 20});
    const auto painted =
        vellum::graphics::node_paint_bounds(node, {10, 10, 20, 20}, frame, propagation);
    ok &= expect(painted && near_rect(*painted, 10, 10, 20, 20),
                 "an image's paint bounds are its box");
    auto changed = node;
    ok &= expect(!vellum::graphics::paint_differs(node, changed), "an unchanged image");
    changed.image->fit = ImageFit::contain;
    ok &= expect(vellum::graphics::paint_differs(node, changed), "a fit change differs");
    changed = node;
    changed.image->sampling = ImageSampling::nearest;
    ok &= expect(vellum::graphics::paint_differs(node, changed), "a sampling change differs");
    changed = node;
    changed.image->source_rect = Rect{0, 0, 1, 1};
    ok &= expect(vellum::graphics::paint_differs(node, changed), "a source change differs");
    changed = node;
    changed.image->content_hash = kHashB;
    ok &= expect(vellum::graphics::paint_differs(node, changed), "a hash change differs");
    changed = node;
    changed.fill = Color::hex(0xFFFFFF);
    ok &= expect(vellum::graphics::paint_differs(node, changed), "a fill change differs");

    // The same scene with a replaced asset damages the image's box only.
    Scene before;
    before.width = 200.0F;
    before.height = 100.0F;
    before.root.children.push_back(node);
    before.root.children.push_back({.id = "box", .kind = SceneNode::Kind::rectangle,
                                    .bounds = {100, 10, 20, 20},
                                    .fill = Color::hex(0x334455)});
    before.images = {solid_asset(kHashA, 2, 2)};
    Scene after = before;
    ok &= expect(compute_scene_damage(before, after, 1.0F).empty(),
                 "the same asset object causes no damage");
    after.images = {solid_asset(kHashA, 2, 2)};
    const auto region = compute_scene_damage(before, after, 1.0F);
    const auto bounds = region.bounds();
    ok &= expect(!region.empty() && !region.is_full_frame() && bounds.left >= 8 &&
                     bounds.right <= 32 && bounds.top >= 8 && bounds.bottom <= 32,
                 "a replaced asset damages only the image that draws it");
    after.images.clear();
    ok &= expect(!compute_scene_damage(before, after, 1.0F).empty(),
                 "an asset going missing damages the image");
    return ok;
}

}  // namespace

int main() {
    bool ok = content_hashes();
    ok &= asset_construction();
    ok &= reference_validity();
    ok &= placement();
    ok &= versions();
    ok &= commands();
    ok &= damage();
    if (ok) std::cout << "image node tests passed\n";
    return ok ? 0 : 1;
}
