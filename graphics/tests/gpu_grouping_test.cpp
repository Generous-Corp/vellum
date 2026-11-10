#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

struct Capture final {
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::size_t layers = 0;
};

bool capture(const Scene& scene, Capture& result) {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height)}, &error);
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(result.rgba, result.width, result.height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    result.layers = surface->telemetry().layer_count;
    return true;
}

struct Probe final {
    const char* what;
    std::uint32_t x;
    std::uint32_t y;
    std::array<int, 4> expected;
};

// Captures `scene` twice on fresh surfaces, requires identical bytes, and
// checks each probe within two levels per channel.
bool capture_and_probe(const char* fixture, const Scene& scene,
                       const std::vector<Probe>& probes, Capture* kept = nullptr) {
    Capture first;
    Capture second;
    if (!capture(scene, first) || !capture(scene, second)) return false;
    if (first.rgba != second.rgba) {
        std::cerr << fixture << ": capture is not deterministic\n";
        return false;
    }
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* actual =
            first.rgba.data() + (static_cast<std::size_t>(probe.y) * first.width + probe.x) * 4U;
        for (int channel = 0; channel < 4; ++channel) {
            if (std::abs(int(actual[channel]) - probe.expected[channel]) > 2) {
                std::cerr << fixture << ": " << probe.what << ": got " << int(actual[0]) << ','
                          << int(actual[1]) << ',' << int(actual[2]) << ',' << int(actual[3])
                          << '\n';
                passed = false;
                break;
            }
        }
    }
    if (kept != nullptr) *kept = std::move(first);
    return passed;
}

SceneNode rect(std::string id, Rect bounds, std::uint32_t color) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = Color::hex(color)};
}

Scene frame(float width, float height) {
    Scene scene{.width = width, .height = height, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, width, height};
    return scene;
}

constexpr std::array<int, 4> kBlack{0, 0, 0, 255};

// A rounded rectangle clip around a group clipped to a triangle; the red fill
// overhangs both, and the blue outer paint is clipped by the outer clip only.
bool nested_rounded_and_path_clips() {
    auto scene = frame(64.0F, 64.0F);
    auto outer = rect("outer", {8.0F, 8.0F, 48.0F, 48.0F}, 0x0000FF);
    outer.clip = NodeClip{.rect = {0.0F, 0.0F, 48.0F, 48.0F}, .radii = uniform_corner_radii(16.0F)};
    SceneNode inner{.id = "inner", .kind = SceneNode::Kind::group, .bounds = {0.0F, 0.0F, 48.0F, 48.0F}};
    inner.clip = NodeClip{.path = std::make_shared<const PathGeometry>(PathGeometry{
        .verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close},
        .values = {0.0F, 0.0F, 48.0F, 0.0F, 0.0F, 48.0F},
    })};
    inner.children.push_back(rect("inner/fill", {-8.0F, -8.0F, 64.0F, 64.0F}, 0xFF0000));
    outer.children.push_back(std::move(inner));
    outer.children.push_back(rect("overhang", {40.0F, 40.0F, 40.0F, 40.0F}, 0x00FF00));
    scene.root.children.push_back(std::move(outer));
    return capture_and_probe("nested_rounded_and_path_clips", scene, {
        {"inside both clips", 16, 16, {255, 0, 0, 255}},
        {"inside both clips near the top edge", 30, 12, {255, 0, 0, 255}},
        {"outside the triangle shows the outer paint", 20, 50, {0, 0, 255, 255}},
        {"only the rounded outer corner cuts the triangle's fill here", 10, 10, kBlack},
        {"the triangle cuts the overhang on the left", 4, 30, kBlack},
        {"the outer clip cuts the overhang sibling", 60, 60, kBlack},
        {"the overhang sibling shows inside the outer clip", 50, 50, {0, 255, 0, 255}},
    });
}

// A clip is in its node's coordinates, so it moves with the node's transform.
bool transformed_clip() {
    auto scene = frame(64.0F, 64.0F);
    auto node = rect("moved", {16.0F, 16.0F, 32.0F, 32.0F}, 0x00FF00);
    node.transform = translation(8.0F, 0.0F);
    node.clip = NodeClip{.rect = {0.0F, 0.0F, 16.0F, 32.0F}};
    node.children.push_back(rect("moved/child", {0.0F, 24.0F, 32.0F, 8.0F}, 0xFFFFFF));
    scene.root.children.push_back(std::move(node));
    // Scaled twice as wide about its origin, so its 4-wide clip covers 8.
    auto scaled = rect("scaled", {0.0F, 52.0F, 16.0F, 8.0F}, 0xFF00FF);
    scaled.transform = Affine2D{.a = 2.0F};
    scaled.clip = NodeClip{.rect = {0.0F, 0.0F, 4.0F, 8.0F}};
    scene.root.children.push_back(std::move(scaled));
    return capture_and_probe("transformed_clip", scene, {
        {"the clipped left half is drawn where the node moved", 30, 30, {0, 255, 0, 255}},
        {"the clip moved with the node past its untransformed edge", 36, 30, {0, 255, 0, 255}},
        {"the child is drawn past the untransformed clip edge", 38, 44, {255, 255, 255, 255}},
        {"the right half is clipped", 44, 30, kBlack},
        {"the untransformed position is empty", 20, 30, kBlack},
        {"the child is clipped by the moved clip", 30, 44, {255, 255, 255, 255}},
        {"the child is cut beyond the moved clip", 44, 44, kBlack},
        {"the clip scales with its node", 6, 56, {255, 0, 255, 255}},
        {"the scaled clip still cuts", 12, 56, kBlack},
    });
}

// Four panels: a half-transparent alpha mask; a white and black luminance mask
// over a parent whose own paint stays unmasked; the same mask in alpha mode,
// which keeps both opaque halves; and a mask that draws nothing.
bool alpha_and_luminance_masks() {
    auto scene = frame(160.0F, 32.0F);
    const auto halves = [](std::string id, MaskMode mode) {
        SceneNode mask{.id = id, .kind = SceneNode::Kind::group,
                       .bounds = {0.0F, 0.0F, 32.0F, 32.0F}, .mask = mode};
        mask.children.push_back(rect(id + "/white", {0.0F, 0.0F, 16.0F, 32.0F}, 0xFFFFFF));
        mask.children.push_back(rect(id + "/black", {16.0F, 0.0F, 16.0F, 32.0F}, 0x000000));
        return mask;
    };

    SceneNode alpha{.id = "alpha", .kind = SceneNode::Kind::group, .bounds = {0.0F, 0.0F, 32.0F, 32.0F}};
    alpha.children.push_back(rect("alpha/content", {0.0F, 0.0F, 32.0F, 32.0F}, 0xFF0000));
    auto half = rect("alpha/mask", {0.0F, 0.0F, 16.0F, 32.0F}, 0xFFFFFF);
    half.fill.alpha = 0.5F;
    half.mask = MaskMode::alpha;
    alpha.children.push_back(std::move(half));

    auto luminance = rect("luminance", {32.0F, 0.0F, 32.0F, 32.0F}, 0x404040);
    luminance.children.push_back(rect("luminance/content", {0.0F, 0.0F, 32.0F, 32.0F}, 0x00FF00));
    luminance.children.push_back(halves("luminance/mask", MaskMode::luminance));

    SceneNode opaque{.id = "opaque", .kind = SceneNode::Kind::group, .bounds = {64.0F, 0.0F, 32.0F, 32.0F}};
    opaque.children.push_back(rect("opaque/content", {0.0F, 0.0F, 32.0F, 32.0F}, 0x0000FF));
    opaque.children.push_back(halves("opaque/mask", MaskMode::alpha));

    SceneNode hidden{.id = "hidden", .kind = SceneNode::Kind::group, .bounds = {96.0F, 0.0F, 32.0F, 32.0F}};
    hidden.children.push_back(rect("hidden/content", {0.0F, 0.0F, 32.0F, 32.0F}, 0xFFFFFF));
    auto nothing = rect("hidden/mask", {0.0F, 0.0F, 32.0F, 32.0F}, 0xFFFFFF);
    nothing.opacity = 0.0F;
    nothing.mask = MaskMode::alpha;
    hidden.children.push_back(std::move(nothing));

    // Luminance times alpha: half-transparent white keeps half the content.
    SceneNode faint{.id = "faint", .kind = SceneNode::Kind::group, .bounds = {128.0F, 0.0F, 32.0F, 32.0F}};
    faint.children.push_back(rect("faint/content", {0.0F, 0.0F, 32.0F, 32.0F}, 0xFFFF00));
    auto faint_mask = rect("faint/mask", {0.0F, 0.0F, 32.0F, 32.0F}, 0xFFFFFF);
    faint_mask.fill.alpha = 0.5F;
    faint_mask.mask = MaskMode::luminance;
    faint.children.push_back(std::move(faint_mask));

    scene.root.children = {std::move(alpha), std::move(luminance), std::move(opaque),
                           std::move(hidden), std::move(faint)};
    Capture kept;
    const bool probed = capture_and_probe("alpha_and_luminance_masks", scene, {
        {"half alpha keeps half the content", 8, 16, {128, 0, 0, 255}},
        {"no alpha hides the content", 24, 16, kBlack},
        {"white luminance keeps the content", 40, 16, {0, 255, 0, 255}},
        {"black luminance hides the content, not the parent's paint", 56, 16, {64, 64, 64, 255}},
        {"alpha mode keeps the white half", 72, 16, {0, 0, 255, 255}},
        {"alpha mode keeps the opaque black half too", 88, 16, {0, 0, 255, 255}},
        {"an empty mask hides its siblings", 112, 16, kBlack},
        {"translucent white luminance keeps half the content", 144, 16, {128, 128, 0, 255}},
    }, &kept);
    // Five masked groups and five mask layers.
    if (kept.layers != 10U) {
        std::cerr << "alpha_and_luminance_masks: expected 10 layers, got " << kept.layers << '\n';
        return false;
    }
    return probed;
}

// A clip that spares its node's own paint, as `overflow: hidden` does, keeps
// the node's shadow outside the box while cutting its children.
bool subtree_clip_keeps_own_shadow() {
    auto scene = frame(64.0F, 64.0F);
    auto card = rect("card", {8.0F, 8.0F, 32.0F, 32.0F}, 0x0000FF);
    card.box_shadows = {{.offset_x = 16.0F, .offset_y = 16.0F, .color = Color::hex(0xFF0000)}};
    card.clip = NodeClip{.rect = {0.0F, 0.0F, 32.0F, 32.0F}, .clips_own_paint = false};
    card.children.push_back(rect("card/overhang", {16.0F, -6.0F, 32.0F, 4.0F}, 0x00FF00));
    scene.root.children.push_back(std::move(card));
    return capture_and_probe("subtree_clip_keeps_own_shadow", scene, {
        {"the shadow outside the box is drawn", 52, 52, {255, 0, 0, 255}},
        {"the card's own fill is drawn", 16, 16, {0, 0, 255, 255}},
        {"the child is cut above the box", 30, 4, kBlack},
    });
}

// Text is its node's own paint, so a clip that covers own paint cuts glyphs
// that overflow the box; the unclipped control proves they do overflow.
bool clipped_text_overflow() {
    auto scene = frame(96.0F, 32.0F);
    SceneNode label{.id = "label", .kind = SceneNode::Kind::text,
                    .bounds = {4.0F, 4.0F, 24.0F, 24.0F}, .fill = Color::hex(0xFFFFFF),
                    .text = "WWWWWWWWWW", .font_size = 16.0F};
    scene.root.children.push_back(label);
    const auto lit_past_box = [](const Capture& capture) {
        std::size_t lit = 0;
        for (std::uint32_t y = 0; y < capture.height; ++y) {
            for (std::uint32_t x = 30; x < capture.width; ++x) {
                const auto* value =
                    capture.rgba.data() + (static_cast<std::size_t>(y) * capture.width + x) * 4U;
                if (value[0] > 8 || value[1] > 8 || value[2] > 8) ++lit;
            }
        }
        return lit;
    };
    Capture unclipped;
    if (!capture(scene, unclipped)) return false;
    scene.root.children[0].clip = NodeClip{.rect = {0.0F, 0.0F, 24.0F, 24.0F}};
    Capture clipped;
    if (!capture(scene, clipped)) return false;
    const auto before = lit_past_box(unclipped);
    const auto after = lit_past_box(clipped);
    if (before < 20U || after != 0U) {
        std::cerr << "clipped_text_overflow: lit pixels past the box went from " << before
                  << " to " << after << '\n';
        return false;
    }
    return true;
}

// Source-over composition is associative, so isolating translucent overlapping
// children changes the layer count but not, beyond rounding, the pixels.
bool isolated_overlap_matches_flat() {
    auto scene = frame(64.0F, 64.0F);
    scene.background = Color::hex(0x101820);
    SceneNode group{.id = "group", .kind = SceneNode::Kind::group, .bounds = {8.0F, 8.0F, 48.0F, 48.0F}};
    const std::uint32_t colors[]{0xEF4444, 0x22C55E, 0x3B82F6};
    for (int index = 0; index < 3; ++index) {
        auto disc = rect("group/disc-" + std::to_string(index),
                         {10.0F * static_cast<float>(index), 8.0F * static_cast<float>(index),
                          28.0F, 28.0F},
                         colors[index]);
        disc.fill.alpha = 0.6F;
        disc.corner_radius = 14.0F;
        group.children.push_back(std::move(disc));
    }
    scene.root.children.push_back(group);
    Capture flat;
    if (!capture(scene, flat)) return false;
    scene.root.children[0].isolate = true;
    Capture isolated;
    if (!capture_and_probe("isolated_overlap_matches_flat", scene, {}, &isolated)) return false;
    if (isolated.layers != flat.layers + 1U) {
        std::cerr << "isolation did not open one layer: " << flat.layers << " then "
                  << isolated.layers << '\n';
        return false;
    }
    if (isolated.rgba.size() != flat.rgba.size()) return false;
    int worst = 0;
    std::size_t changed = 0;
    for (std::size_t index = 0; index < flat.rgba.size(); ++index) {
        const int delta = std::abs(int(flat.rgba[index]) - int(isolated.rgba[index]));
        worst = std::max(worst, delta);
        if (delta != 0) ++changed;
    }
    if (worst > 2) {
        std::cerr << "isolated and flat differ by " << worst << " levels\n";
        return false;
    }
    // The fixture must exercise an overlap; a blank capture would also match.
    const auto* centre = flat.rgba.data() + (28U * flat.width + 30U) * 4U;
    if (centre[0] < 40 || centre[1] < 40 || centre[2] < 60) {
        std::cerr << "the three discs do not overlap at the probe: " << int(centre[0]) << ','
                  << int(centre[1]) << ',' << int(centre[2]) << '\n';
        return false;
    }
    std::cout << "isolated_overlap_matches_flat: " << changed << " channel values moved, at most "
              << worst << " levels\n";
    return true;
}

// Clip, mask and isolation are refused before version 5 and drawn at 5; a
// refused render keeps the previous frame.
bool grouping_version_refusal() {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto scene = frame(32.0F, 32.0F);
    scene.root.children.push_back(rect("swatch", {4.0F, 4.0F, 12.0F, 12.0F}, 0xF0A030));
    auto surface = SkiaDawnSurface::create({.width = 32, .height = 32}, &error);
    std::vector<std::uint8_t> first;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(first, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto refused_keeps_frame = [&](const Scene& candidate, const std::string& expected) {
        std::string why;
        std::vector<std::uint8_t> after;
        std::uint32_t after_width = 0;
        std::uint32_t after_height = 0;
        if (surface->render(candidate, &why) || why != expected) {
            std::cerr << "expected refusal '" << expected << "', got '" << why << "'\n";
            return false;
        }
        return surface->capture_rgba(after, after_width, after_height, &why) && after == first;
    };
    const auto accepted = [&](const Scene& candidate) {
        std::string why;
        if (!surface->render(candidate, &why)) {
            std::cerr << "a version 5 scene was refused: " << why << '\n';
            return false;
        }
        return true;
    };
    bool ok = true;
    const std::vector<std::pair<const char*, void (*)(SceneNode&)>> fields{
        {"clip", [](SceneNode& node) { node.clip = NodeClip{.rect = {0.0F, 0.0F, 6.0F, 6.0F}}; }},
        {"mask", [](SceneNode& node) { node.mask = MaskMode::luminance; }},
        {"isolation", [](SceneNode& node) { node.isolate = true; }},
    };
    for (const auto& [name, apply] : fields) {
        auto candidate = scene;
        candidate.root.children.push_back(rect("grouped", {16.0F, 16.0F, 8.0F, 8.0F}, 0xFFFFFF));
        apply(candidate.root.children.back());
        candidate.scene_version = 4U;
        if (!refused_keeps_frame(
                candidate, "scene version 4 cannot carry the clip, mask, or isolation at grouped")) {
            std::cerr << "  for the " << name << '\n';
            ok = false;
        }
        candidate.scene_version = 5U;
        ok = accepted(candidate) && ok;
        if (!surface->render(scene, &error)) return false;
    }
    auto masked_root = scene;
    masked_root.root.mask = MaskMode::alpha;
    ok = refused_keeps_frame(masked_root, "scene root root cannot be a mask") && ok;
    auto broken = scene;
    broken.root.children[0].clip = NodeClip{.path = std::make_shared<const PathGeometry>(
        PathGeometry{.verbs = {PathVerb::close}})};
    std::string reason;
    (void)path_is_valid(*broken.root.children[0].clip->path, &reason);
    ok = refused_keeps_frame(broken, "clip of swatch cannot be applied: " + reason) && ok;
    return ok;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string fixture = argv[1];
    try {
        if (fixture == "nested_rounded_and_path_clips") return nested_rounded_and_path_clips() ? 0 : 1;
        if (fixture == "transformed_clip") return transformed_clip() ? 0 : 1;
        if (fixture == "clipped_text_overflow") return clipped_text_overflow() ? 0 : 1;
        if (fixture == "subtree_clip_keeps_own_shadow") return subtree_clip_keeps_own_shadow() ? 0 : 1;
        if (fixture == "alpha_and_luminance_masks") return alpha_and_luminance_masks() ? 0 : 1;
        if (fixture == "isolated_overlap_matches_flat") return isolated_overlap_matches_flat() ? 0 : 1;
        if (fixture == "grouping_version_refusal") return grouping_version_refusal() ? 0 : 1;
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
    return 2;
}
