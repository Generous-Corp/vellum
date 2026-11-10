// Image node rendering on the Skia/Dawn surface: fit, sampling, source
// rectangles, corner clipping, opacity, the missing-asset placeholder, scene
// refusals, and cache identity.

#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

int failures = 0;

void expect(bool ok, const std::string& what, const std::string& detail = {}) {
    if (ok) return;
    ++failures;
    std::cerr << "FAIL: " << what;
    if (!detail.empty()) std::cerr << " (" << detail << ')';
    std::cerr << '\n';
}

struct Capture final {
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;

    [[nodiscard]] std::array<int, 4> at(std::uint32_t x, std::uint32_t y) const {
        const auto* p = rgba.data() + (static_cast<std::size_t>(y) * width + x) * 4U;
        return {p[0], p[1], p[2], p[3]};
    }
};

std::string describe(const std::array<int, 4>& value) {
    return std::to_string(value[0]) + "," + std::to_string(value[1]) + "," +
           std::to_string(value[2]) + "," + std::to_string(value[3]);
}

bool close_to(const std::array<int, 4>& actual, const std::array<int, 4>& expected,
              int tolerance = 1) {
    for (std::size_t channel = 0; channel < 4U; ++channel) {
        if (std::abs(actual[channel] - expected[channel]) > tolerance) return false;
    }
    return true;
}

void probe(const Capture& capture, std::uint32_t x, std::uint32_t y,
           const std::array<int, 4>& expected, const std::string& what, int tolerance = 1) {
    const auto actual = capture.at(x, y);
    expect(close_to(actual, expected, tolerance), what,
           "at " + std::to_string(x) + "," + std::to_string(y) + " got " + describe(actual) +
               " expected " + describe(expected));
}

Scene scene_of(float width, float height) {
    Scene scene;
    scene.width = width;
    scene.height = height;
    scene.background = Color::hex(0x000000);
    scene.root.id = "root";
    scene.images = {harness::fixtures::quadrant_image()};
    return scene;
}

SceneNode image_node(std::string id, Rect bounds, ImageFit fit = ImageFit::fill,
                     ImageSampling sampling = ImageSampling::nearest) {
    return {
        .id = std::move(id),
        .kind = SceneNode::Kind::image,
        .bounds = bounds,
        .image = ImageReference{.content_hash = std::string(harness::fixtures::kQuadrantImageHash),
                                .fit = fit,
                                .sampling = sampling},
    };
}

Capture render(const Scene& scene) {
    const auto result = harness::render_offscreen(
        scene, static_cast<std::uint32_t>(scene.width), static_cast<std::uint32_t>(scene.height));
    expect(result.ok, "the scene renders", result.error);
    return {result.rgba, result.pixel_width, result.pixel_height};
}

constexpr std::array<int, 4> kRed{255, 0, 0, 255};
constexpr std::array<int, 4> kGreen{0, 200, 0, 255};
constexpr std::array<int, 4> kBlue{0, 0, 255, 255};
// White at half alpha over the opaque black background.
constexpr std::array<int, 4> kHalfWhite{128, 128, 128, 255};
constexpr std::array<int, 4> kBlack{0, 0, 0, 255};

void decoding() {
    const auto asset = harness::fixtures::quadrant_image();
    expect(asset->width == 8U && asset->height == 8U &&
               asset->content_hash == harness::fixtures::kQuadrantImageHash,
           "the PNG decodes to an 8 by 8 asset named by its SHA-256");
    const auto texel = [&](std::size_t x, std::size_t y) {
        const auto* p = asset->premultiplied_rgba.data() + (y * 8U + x) * 4U;
        return std::array<int, 4>{p[0], p[1], p[2], p[3]};
    };
    expect(texel(0, 0) == kRed && texel(7, 0) == kGreen && texel(0, 7) == kBlue &&
               close_to(texel(7, 7), {128, 128, 128, 128}),
           "the decoded texels are premultiplied", describe(texel(7, 7)));

    std::vector<std::uint8_t> bytes(asset->premultiplied_rgba.begin(),
                                    asset->premultiplied_rgba.end());
    std::string error;
    expect(!SkiaDawnSurface::decode_image_asset(bytes, {}, &error) &&
               error.find("not a PNG") != std::string::npos,
           "bytes that are not a PNG are refused", error);
    const auto path = std::string(VELLUM_CAPTURE_IMAGE_DIR) + "/quadrants.png";
    std::FILE* file = std::fopen(path.c_str(), "rb");
    std::vector<std::uint8_t> png(4096);
    png.resize(file != nullptr ? std::fread(png.data(), 1, png.size(), file) : 0U);
    if (file != nullptr) std::fclose(file);
    expect(SkiaDawnSurface::decode_image_asset(
               png, std::string(harness::fixtures::kQuadrantImageHash).substr(7), &error) != nullptr,
           "a bare expected hash of the same digest is accepted", error);
    expect(!SkiaDawnSurface::decode_image_asset(png, "sha256:" + std::string(64, '0'), &error) &&
               error.find("hash to") != std::string::npos,
           "a mislabelled PNG is refused", error);
    png.resize(png.size() / 2U);
    expect(!SkiaDawnSurface::decode_image_asset(png, {}, &error),
           "a truncated PNG is refused", error);
}

void fit_and_sampling() {
    auto scene = scene_of(80.0F, 40.0F);
    scene.root.children.push_back(image_node("nearest", {0, 0, 32, 32}));
    scene.root.children.push_back(
        image_node("linear", {40, 0, 32, 32}, ImageFit::fill, ImageSampling::linear));
    const auto capture = render(scene);
    probe(capture, 8, 8, kRed, "fill draws the top-left quadrant");
    probe(capture, 24, 8, kGreen, "fill draws the top-right quadrant");
    probe(capture, 8, 24, kBlue, "fill draws the bottom-left quadrant");
    probe(capture, 24, 24, kHalfWhite, "premultiplied half white composites over black");
    probe(capture, 15, 4, kRed, "nearest sampling keeps a hard edge");
    probe(capture, 16, 4, kGreen, "nearest sampling switches at the texel boundary");
    const auto blended = capture.at(55, 4);
    expect(blended[0] > 32 && blended[1] > 32,
           "linear sampling blends neighbouring texels", describe(blended));
    probe(capture, 76, 36, kBlack, "nothing paints outside the boxes");

    auto contain = scene_of(64.0F, 32.0F);
    auto letterboxed = image_node("contain", {0, 0, 64, 32}, ImageFit::contain);
    letterboxed.fill = Color::hex(0xFFFF00);
    contain.root.children.push_back(letterboxed);
    const auto contained = render(contain);
    probe(contained, 8, 16, {255, 255, 0, 255}, "contain leaves the fill beside the image");
    probe(contained, 20, 4, kRed, "contain centres the image horizontally");
    probe(contained, 44, 4, kGreen, "contain keeps the aspect ratio");

    auto cover = scene_of(64.0F, 32.0F);
    cover.root.children.push_back(image_node("cover", {0, 0, 64, 32}, ImageFit::cover));
    const auto covered = render(cover);
    probe(covered, 4, 4, kRed, "cover crops the top of the image");
    probe(covered, 4, 28, kBlue, "cover crops the bottom of the image");
    probe(covered, 60, 4, kGreen, "cover fills the width");

    auto none = scene_of(32.0F, 32.0F);
    none.root.children.push_back(image_node("none", {0, 0, 32, 32}, ImageFit::none));
    const auto natural = render(none);
    probe(natural, 13, 13, kRed, "none draws one pixel per texel, centred");
    probe(natural, 18, 18, kHalfWhite, "none keeps the image's own size");
    probe(natural, 2, 2, kBlack, "none leaves the rest of the box empty");
    probe(natural, 21, 21, kBlack, "none stops at the image's edge");

    auto cropped = scene_of(32.0F, 32.0F);
    auto right = image_node("source", {0, 0, 32, 32});
    right.image->source_rect = Rect{4.0F, 0.0F, 4.0F, 8.0F};
    cropped.root.children.push_back(right);
    const auto sourced = render(cropped);
    probe(sourced, 4, 4, kGreen, "a source rectangle selects its texels");
    probe(sourced, 28, 28, kHalfWhite, "a source rectangle maps onto the whole box");

    auto rounded = scene_of(32.0F, 32.0F);
    auto corners = image_node("rounded", {0, 0, 32, 32});
    corners.corner_radius = 12.0F;
    rounded.root.children.push_back(corners);
    const auto clipped = render(rounded);
    probe(clipped, 0, 0, kBlack, "corner radii clip the image");
    probe(clipped, 8, 8, kRed, "inside the radius the image is drawn");
}

void opacity() {
    auto lone = scene_of(16.0F, 16.0F);
    auto faded = image_node("faded", {0, 0, 16, 16});
    faded.image->source_rect = Rect{0.0F, 0.0F, 4.0F, 4.0F};
    faded.opacity = 0.5F;
    lone.root.children.push_back(faded);
    probe(render(lone), 8, 8, {128, 0, 0, 255}, "a faded image folds its opacity");

    // Over a blue fill, folding would leave blue showing through the red.
    auto over_fill = lone;
    over_fill.root.children[0].fill = Color::hex(0x0000FF);
    probe(render(over_fill), 8, 8, {128, 0, 0, 255},
          "a faded image over a fill is composited as a layer");
}

void placeholder_and_refusals() {
    auto scene = scene_of(32.0F, 32.0F);
    auto missing = image_node("missing", {4, 4, 24, 24});
    missing.image->content_hash = "sha256:" + std::string(64, 'e');
    scene.root.children.push_back(missing);

    std::string error;
    auto surface = SkiaDawnSurface::create({.width = 32, .height = 32}, &error);
    expect(surface != nullptr, "the surface is created", error);
    if (!surface) return;
    expect(surface->render(scene, &error), "a missing asset still renders", error);
    Capture placeholder;
    expect(surface->capture_rgba(placeholder.rgba, placeholder.width, placeholder.height, &error),
           "the placeholder frame captures", error);
    const std::array<int, 4> grey{kMissingImagePlaceholder.red8(),
                                  kMissingImagePlaceholder.green8(),
                                  kMissingImagePlaceholder.blue8(), 255};
    probe(placeholder, 16, 16, grey, "a missing asset draws the placeholder over its box");
    probe(placeholder, 1, 1, kBlack, "the placeholder stays inside the box");
    const auto& diagnostics = surface->diagnostics();
    expect(diagnostics.size() == 1U && diagnostics[0].code == kImageAssetMissingCode &&
               diagnostics[0].node_id == "missing",
           "the missing asset is reported");

    const auto refused = [&](const Scene& candidate, const std::string& expected) {
        std::string why;
        Capture after;
        const bool rendered = surface->render(candidate, &why);
        expect(!rendered && why == expected, "the scene is refused: " + expected, why);
        expect(surface->capture_rgba(after.rgba, after.width, after.height, &why) &&
                   after.rgba == placeholder.rgba,
               "a refused scene keeps the previous frame: " + expected);
    };
    auto older = scene;
    older.scene_version = 3U;
    refused(older, "scene version 3 cannot carry the image node missing");
    expect(!surface->diagnostics().empty() &&
               surface->diagnostics()[0].code == kSceneVersionMismatchCode,
           "the version refusal is a loss diagnostic");

    auto malformed = scene;
    malformed.root.children[0].image->content_hash = "logo.png";
    refused(malformed, "image missing cannot be drawn: the content hash is not sha256: "
                       "followed by 64 lowercase hex digits");
    auto unreferenced = scene;
    unreferenced.root.children[0].image.reset();
    refused(unreferenced, "image missing cannot be drawn: it has no image reference");
    auto twice = scene;
    twice.images.push_back(harness::fixtures::quadrant_image());
    refused(twice, "the scene lists image asset " + std::string(harness::fixtures::kQuadrantImageHash) +
                       " twice");
    auto forged = scene;
    auto bad = std::make_shared<ImageAsset>(*forged.images[0]);
    bad->premultiplied_rgba.pop_back();
    forged.images = {bad};
    refused(forged, "image asset " + std::string(harness::fixtures::kQuadrantImageHash) +
                        " is invalid: the image's texel buffer does not match its dimensions");

    auto current = scene;
    current.scene_version = kSceneVersion;
    expect(surface->render(current, &error), "the same scene renders at the current version",
           error);
}

std::shared_ptr<const ImageAsset> solid(const std::string& hash, std::uint8_t red,
                                        std::uint8_t blue) {
    std::vector<std::uint8_t> texels;
    for (int index = 0; index < 4; ++index) texels.insert(texels.end(), {red, 0, blue, 255});
    return make_image_asset(hash, 2, 2, texels, ImageAlpha::premultiplied);
}

void cache_identity() {
    const std::string hash = "sha256:" + std::string(64, 'c');
    Scene scene;
    scene.width = 16.0F;
    scene.height = 16.0F;
    scene.background = Color::hex(0x000000);
    scene.root.children.push_back(image_node("swatch", {0, 0, 16, 16}));
    scene.root.children[0].image->content_hash = hash;
    scene.images = {solid(hash, 255, 0)};

    std::string error;
    auto surface = SkiaDawnSurface::create({.width = 16, .height = 16}, &error);
    expect(surface != nullptr, "the cached surface is created", error);
    if (!surface) return;
    const auto draw = [&](const Scene& candidate, bool incremental) {
        Capture capture;
        const bool ok = incremental ? surface->render_incremental(candidate, &error)
                                    : surface->render(candidate, &error);
        expect(ok && surface->capture_rgba(capture.rgba, capture.width, capture.height, &error),
               "the cached surface renders", error);
        return capture;
    };
    const auto first = draw(scene, false);
    probe(first, 8, 8, kRed, "the first asset is drawn");
    const auto again = draw(scene, false);
    expect(surface->telemetry().textures.frame_hits == 1U &&
               surface->telemetry().textures.frame_misses == 0U && again.rgba == first.rgba,
           "an unchanged asset is drawn from the texture cache",
           to_json(surface->telemetry()));

    // The same content hash arriving with other texels must not reach the
    // upload remembered for the first asset.
    auto relabelled = scene;
    relabelled.images = {solid(hash, 0, 255)};
    const auto swapped = draw(relabelled, true);
    probe(swapped, 8, 8, kBlue, "a new asset under the same hash is drawn, not the cached one");
    const auto fresh = render(relabelled);
    expect(swapped.rgba == fresh.rgba, "the incremental frame matches a fresh full render");
    const auto back = draw(scene, true);
    probe(back, 8, 8, kRed, "returning to the first asset draws it again");

    auto uncached = SkiaDawnSurface::create({.width = 16, .height = 16}, &error);
    if (!uncached) return;
    uncached->set_cache_budgets({.paragraphs = {}, .gradients = {}, .textures = {}});
    Capture plain;
    expect(uncached->render(relabelled, &error) &&
               uncached->capture_rgba(plain.rgba, plain.width, plain.height, &error) &&
               plain.rgba == swapped.rgba,
           "the uncached surface draws the same bytes", error);
    expect(uncached->telemetry().textures.entries == 0U,
           "with the texture cache off nothing is kept");
}

}  // namespace

int main() {
    std::string error;
    if (!harness::prepare_device(&error)) {
        std::cerr << "FAIL: no Dawn device: " << error << '\n';
        return 1;
    }
    try {
        decoding();
        fit_and_sampling();
        opacity();
        placeholder_and_refusals();
        cache_identity();
    } catch (const std::exception& exception) {
        std::cerr << "FAIL: " << exception.what() << '\n';
        return 1;
    }
    if (failures == 0) std::cout << "image GPU tests passed\n";
    return failures == 0 ? 0 : 1;
}
