#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
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

bool capture(const Scene& scene, Capture& result, bool cached = true, float scale = 1.0F) {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height),
         .scale = scale},
        &error);
    if (surface && !cached) surface->set_layer_cache_policy({.enabled = false});
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(result.rgba, result.width, result.height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    result.layers = surface->telemetry().layer_count;
    return true;
}

using Rgb = std::array<float, 3>;
using Rgba = std::array<float, 4>;

constexpr Rgb kBackground{0.20F, 0.40F, 0.60F};
constexpr Rgb kBackdrop{0.80F, 0.35F, 0.15F};
constexpr Rgb kSource{0.25F, 0.65F, 0.90F};

Color colour(const Rgb& value, float alpha = 1.0F) {
    return Color::rgba(value[0], value[1], value[2], alpha);
}

float luminance(const Rgb& c) { return 0.3F * c[0] + 0.59F * c[1] + 0.11F * c[2]; }

Rgb clip_colour(Rgb c) {
    const float l = luminance(c);
    const float n = std::min({c[0], c[1], c[2]});
    const float x = std::max({c[0], c[1], c[2]});
    if (n < 0.0F) {
        for (auto& v : c) v = l + (v - l) * l / (l - n);
    }
    if (x > 1.0F) {
        for (auto& v : c) v = l + (v - l) * (1.0F - l) / (x - l);
    }
    return c;
}

Rgb set_luminance(const Rgb& c, float l) {
    const float d = l - luminance(c);
    return clip_colour({c[0] + d, c[1] + d, c[2] + d});
}

float saturation(const Rgb& c) {
    return std::max({c[0], c[1], c[2]}) - std::min({c[0], c[1], c[2]});
}

Rgb set_saturation(const Rgb& c, float s) {
    std::array<int, 3> order{0, 1, 2};
    std::sort(order.begin(), order.end(), [&](int a, int b) { return c[a] < c[b]; });
    Rgb out{0.0F, 0.0F, 0.0F};
    const int low = order[0];
    const int mid = order[1];
    const int high = order[2];
    if (c[high] > c[low]) {
        out[mid] = (c[mid] - c[low]) * s / (c[high] - c[low]);
        out[high] = s;
    }
    return out;
}

float soft_light(float cb, float cs) {
    if (cs <= 0.5F) return cb - (1.0F - 2.0F * cs) * cb * (1.0F - cb);
    const float d = cb <= 0.25F ? ((16.0F * cb - 12.0F) * cb + 4.0F) * cb : std::sqrt(cb);
    return cb + (2.0F * cs - 1.0F) * (d - cb);
}

float separable(BlendMode mode, float cb, float cs) {
    const auto screen = [](float a, float b) { return a + b - a * b; };
    switch (mode) {
    case BlendMode::multiply: return cb * cs;
    case BlendMode::screen: return screen(cb, cs);
    case BlendMode::overlay:
        return cb <= 0.5F ? cb * 2.0F * cs : screen(cs, 2.0F * cb - 1.0F);
    case BlendMode::darken: return std::min(cb, cs);
    case BlendMode::lighten: return std::max(cb, cs);
    case BlendMode::difference: return std::fabs(cb - cs);
    case BlendMode::exclusion: return cb + cs - 2.0F * cb * cs;
    case BlendMode::color_dodge:
        return cb <= 0.0F ? 0.0F : cs >= 1.0F ? 1.0F : std::min(1.0F, cb / (1.0F - cs));
    case BlendMode::color_burn:
        return cb >= 1.0F ? 1.0F : cs <= 0.0F ? 0.0F : 1.0F - std::min(1.0F, (1.0F - cb) / cs);
    case BlendMode::hard_light:
        return cs <= 0.5F ? cb * 2.0F * cs : screen(cb, 2.0F * cs - 1.0F);
    case BlendMode::soft_light: return soft_light(cb, cs);
    default: return cs;
    }
}

// What an opaque source of `source` leaves over an opaque backdrop `backdrop`
// under `mode`, as premultiplied RGBA. Both are opaque, so the blend formulas
// of the Compositing and Blending specification and the Porter-Duff operators
// reduce to the few cases below.
Rgba expected(BlendMode mode, const Rgb& source, const Rgb& backdrop) {
    const auto opaque = [](const Rgb& c) { return Rgba{c[0], c[1], c[2], 1.0F}; };
    switch (mode) {
    case BlendMode::normal:
    case BlendMode::source_over:
    case BlendMode::source:
    case BlendMode::source_in:
    case BlendMode::source_atop: return opaque(source);
    case BlendMode::destination:
    case BlendMode::destination_in:
    case BlendMode::destination_over:
    case BlendMode::destination_atop: return opaque(backdrop);
    case BlendMode::destination_out:
    case BlendMode::clear:
    case BlendMode::source_out:
    case BlendMode::exclusive_or: return {0.0F, 0.0F, 0.0F, 0.0F};
    case BlendMode::plus:
        return {std::min(1.0F, source[0] + backdrop[0]), std::min(1.0F, source[1] + backdrop[1]),
                std::min(1.0F, source[2] + backdrop[2]), 1.0F};
    case BlendMode::hue:
        return opaque(set_luminance(set_saturation(source, saturation(backdrop)),
                                    luminance(backdrop)));
    case BlendMode::saturation:
        return opaque(set_luminance(set_saturation(backdrop, saturation(source)),
                                    luminance(backdrop)));
    case BlendMode::color: return opaque(set_luminance(source, luminance(backdrop)));
    case BlendMode::luminosity: return opaque(set_luminance(backdrop, luminance(source)));
    default:
        return {separable(mode, backdrop[0], source[0]), separable(mode, backdrop[1], source[1]),
                separable(mode, backdrop[2], source[2]), 1.0F};
    }
}

struct Probe final {
    const char* what;
    std::uint32_t x;
    std::uint32_t y;
    Rgba expected;
};

// Reports every probe further than `tolerance` levels from its expectation.
bool probes_hold(const std::string& fixture, const Capture& image,
                 const std::vector<Probe>& probes, int tolerance = 3) {
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* actual =
            image.rgba.data() + (static_cast<std::size_t>(probe.y) * image.width + probe.x) * 4U;
        for (int channel = 0; channel < 4; ++channel) {
            const int want = static_cast<int>(std::lround(probe.expected[channel] * 255.0F));
            if (std::abs(int(actual[channel]) - want) > tolerance) {
                std::cerr << fixture << ": " << probe.what << ": got " << int(actual[0]) << ','
                          << int(actual[1]) << ',' << int(actual[2]) << ',' << int(actual[3])
                          << ", expected " << int(std::lround(probe.expected[0] * 255.0F)) << ','
                          << int(std::lround(probe.expected[1] * 255.0F)) << ','
                          << int(std::lround(probe.expected[2] * 255.0F)) << ','
                          << int(std::lround(probe.expected[3] * 255.0F)) << '\n';
                passed = false;
                break;
            }
        }
    }
    return passed;
}

SceneNode rect(std::string id, Rect bounds, Color fill) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = fill};
}

Scene frame(float width, float height) {
    Scene scene{.width = width, .height = height, .background = colour(kBackground)};
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, width, height};
    return scene;
}

// A backdrop rectangle on the scene background, and a blended source that
// overlaps the backdrop on its left and the plain background on its right.
//
//   backdrop  x  8..48, y  8..56
//   blended   x 28..84, y 16..48
Scene blend_scene(BlendMode mode) {
    auto scene = frame(96.0F, 64.0F);
    scene.root.children.push_back(rect("backdrop", {8.0F, 8.0F, 40.0F, 48.0F}, colour(kBackdrop)));
    auto blended = rect("blended", {28.0F, 16.0F, 56.0F, 32.0F}, colour(kSource));
    blended.blend_mode = mode;
    scene.root.children.push_back(std::move(blended));
    return scene;
}

std::vector<Probe> blend_probes(BlendMode mode) {
    // A mode that empties the backdrop wherever the group draws nothing does
    // so across the whole frame, not only under the source.
    const bool cleared = blend_mode_clears_backdrop(mode);
    const Rgba backdrop = cleared ? Rgba{0.0F, 0.0F, 0.0F, 0.0F}
                                  : Rgba{kBackdrop[0], kBackdrop[1], kBackdrop[2], 1.0F};
    const Rgba background = cleared ? Rgba{0.0F, 0.0F, 0.0F, 0.0F}
                                    : Rgba{kBackground[0], kBackground[1], kBackground[2], 1.0F};
    return {
        {"over the backdrop rectangle", 38, 32, expected(mode, kSource, kBackdrop)},
        {"over the scene background", 66, 32, expected(mode, kSource, kBackground)},
        {"the backdrop beyond the source", 16, 32, backdrop},
        {"the background beyond the source", 90, 60, background},
    };
}

bool every_mode_matches(const std::string& fixture, const std::vector<BlendMode>& modes) {
    bool passed = true;
    for (const auto mode : modes) {
        Capture image;
        const auto scene = blend_scene(mode);
        if (!capture(scene, image)) return false;
        const std::string name = fixture + " " + std::string(to_string(mode));
        if (!probes_hold(name, image, blend_probes(mode))) passed = false;
        Capture again;
        if (!capture(scene, again) || again.rgba != image.rgba) {
            std::cerr << name << ": the capture is not deterministic\n";
            passed = false;
        }
    }
    return passed;
}

std::vector<BlendMode> tier(int wanted) {
    std::vector<BlendMode> modes;
    for (const auto mode : kAllBlendModes) {
        if (mode != BlendMode::normal && blend_mode_tier(mode) == wanted) modes.push_back(mode);
    }
    return modes;
}

// Every Tier 1 mode, normal aside, leaves the pixels its formula gives over a
// backdrop shape and over the scene background, and leaves the rest alone.
bool tier1_mode_captures() {
    const auto modes = tier(1);
    if (modes.size() != 10U) {
        std::cerr << "expected ten Tier 1 modes besides normal, found " << modes.size() << '\n';
        return false;
    }
    if (!every_mode_matches("tier 1", modes)) return false;
    // Negative control: the probes tell the modes apart. Judged against the
    // formula for a different mode, a capture fails.
    Capture image;
    if (!capture(blend_scene(BlendMode::multiply), image)) return false;
    if (probes_hold("control", image, blend_probes(BlendMode::screen))) {
        std::cerr << "a multiply capture satisfied the screen probes\n";
        return false;
    }
    Capture plain;
    if (!capture(blend_scene(BlendMode::normal), plain)) return false;
    if (!probes_hold("normal", plain, blend_probes(BlendMode::normal)) || plain.rgba == image.rgba) {
        std::cerr << "a normal capture is not plain source-over, or equals a multiply capture\n";
        return false;
    }
    return true;
}

// Tier 2 is required on Skia Graphite: the remaining separable and
// non-separable modes and Porter-Duff operators all draw their formulas.
bool tier2_mode_captures() {
    const auto modes = tier(2);
    if (modes.size() != 18U) {
        std::cerr << "expected eighteen Tier 2 modes, found " << modes.size() << '\n';
        return false;
    }
    if (!every_mode_matches("tier 2", modes)) return false;
    Capture hue;
    Capture normal;
    if (!capture(blend_scene(BlendMode::hue), hue) ||
        !capture(blend_scene(BlendMode::normal), normal) || hue.rgba == normal.rgba) {
        std::cerr << "a hue capture equals a normal one\n";
        return false;
    }
    return true;
}

// Opacity fades the blended group: a half-faded multiply is the average of
// the backdrop and the fully multiplied pixel.
bool blend_with_opacity() {
    auto scene = blend_scene(BlendMode::multiply);
    scene.root.children[1].opacity = 0.5F;
    Capture image;
    if (!capture(scene, image)) return false;
    const auto full = expected(BlendMode::multiply, kSource, kBackdrop);
    const Rgba half{(full[0] + kBackdrop[0]) * 0.5F, (full[1] + kBackdrop[1]) * 0.5F,
                    (full[2] + kBackdrop[2]) * 0.5F, 1.0F};
    return probes_hold("blend_with_opacity", image, {{"half-faded multiply", 38, 32, half}});
}

// An ancestor that isolates is its own backdrop: a blend inside it meets the
// group's transparent pixels, not the scene beneath, so a multiply over
// nothing is the source itself. Without the isolation the same child
// multiplies with the scene background.
bool blend_stays_inside_an_isolated_parent() {
    const auto build = [](bool isolate) {
        auto scene = frame(96.0F, 64.0F);
        SceneNode parent{.id = "parent", .kind = SceneNode::Kind::group,
                         .bounds = {0.0F, 0.0F, 96.0F, 64.0F}, .isolate = isolate};
        auto blended = rect("blended", {28.0F, 16.0F, 56.0F, 32.0F}, colour(kSource));
        blended.blend_mode = BlendMode::multiply;
        parent.children.push_back(std::move(blended));
        scene.root.children.push_back(std::move(parent));
        return scene;
    };
    Capture contained;
    Capture open;
    if (!capture(build(true), contained) || !capture(build(false), open)) return false;
    const Rgba source{kSource[0], kSource[1], kSource[2], 1.0F};
    const auto multiplied = expected(BlendMode::multiply, kSource, kBackground);
    return probes_hold("isolated parent", contained, {{"the source itself", 66, 32, source}}) &&
           probes_hold("open parent", open, {{"multiplied with the background", 66, 32, multiplied}});
}

// The layer cache composites a cached opacity group's image; a blend nested
// inside such a group must come out as it does when the group is drawn
// directly, and a blended group is never served from a stale image.
bool blend_layer_cache_matches_direct() {
    for (const float scale : {1.0F, 2.0F}) {
        auto scene = frame(96.0F, 64.0F);
        scene.root.children.push_back(
            rect("backdrop", {8.0F, 8.0F, 40.0F, 48.0F}, colour(kBackdrop)));
        SceneNode fade{.id = "fade", .kind = SceneNode::Kind::group,
                       .bounds = {20.0F, 4.0F, 70.0F, 56.0F}, .opacity = 0.7F};
        fade.children.push_back(rect("fade/base", {0.0F, 0.0F, 40.0F, 40.0F}, colour(kSource)));
        auto blended = rect("fade/blended", {10.0F, 8.0F, 50.0F, 30.0F}, colour(kBackdrop, 0.8F));
        blended.blend_mode = BlendMode::difference;
        fade.children.push_back(std::move(blended));
        scene.root.children.push_back(std::move(fade));
        auto outer = rect("outer", {30.0F, 30.0F, 50.0F, 26.0F}, colour(kSource));
        outer.blend_mode = BlendMode::screen;
        scene.root.children.push_back(std::move(outer));
        Capture direct;
        Capture cached;
        if (!capture(scene, direct, false, scale)) return false;
        for (int frame_index = 0; frame_index < 4; ++frame_index) {
            if (!capture(scene, cached, true, scale)) return false;
        }
        if (direct.rgba != cached.rgba || direct.layers == 0U) {
            std::cerr << "a cached scene with blends differs from a direct draw at scale " << scale
                      << '\n';
            return false;
        }
    }
    return true;
}

// A blend mode is a version 6 field: an older scene is refused with its node
// named and the previous frame kept, an unknown mode is refused, and a version
// 6 scene draws.
bool blend_version_refusal() {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = SkiaDawnSurface::create({.width = 96, .height = 64}, &error);
    if (!surface) {
        std::cerr << error << '\n';
        return false;
    }
    const auto plain = blend_scene(BlendMode::normal);
    std::vector<std::uint8_t> first;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!surface->render(plain, &error) || !surface->capture_rgba(first, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto refused = [&](const Scene& candidate, const std::string& message) {
        std::string why;
        std::vector<std::uint8_t> after;
        if (surface->render(candidate, &why) || why != message) {
            std::cerr << "expected the refusal '" << message << "', got '" << why << "'\n";
            return false;
        }
        const auto& reported = surface->diagnostics();
        return surface->capture_rgba(after, width, height, &why) && after == first &&
               reported.size() == 1U && reported[0].code == kSceneVersionMismatchCode &&
               reported[0].node_id == "blended" && reported[0].property == "blendMode";
    };
    bool ok = true;
    auto older = blend_scene(BlendMode::multiply);
    older.scene_version = 5U;
    ok = refused(older, "scene version 5 cannot carry the blend mode at blended") && ok;
    older.scene_version = 4U;
    ok = refused(older, "scene version 4 cannot carry the blend mode at blended") && ok;
    auto unknown = blend_scene(BlendMode::multiply);
    unknown.root.children[1].blend_mode = static_cast<BlendMode>(200);
    ok = refused(unknown, "node blended has a blend mode this build does not know") && ok;
    older.scene_version = 5U;
    older.root.children[1].blend_mode = BlendMode::normal;
    ok = surface->render(older, &error) && ok;
    ok = surface->render(blend_scene(BlendMode::multiply), &error) && ok;
    if (!ok) std::cerr << "blend_version_refusal: " << error << '\n';
    return ok;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string fixture = argv[1];
    try {
        if (fixture == "tier1_mode_captures") return tier1_mode_captures() ? 0 : 1;
        if (fixture == "tier2_mode_captures") return tier2_mode_captures() ? 0 : 1;
        if (fixture == "blend_with_opacity") return blend_with_opacity() ? 0 : 1;
        if (fixture == "blend_stays_inside_an_isolated_parent") {
            return blend_stays_inside_an_isolated_parent() ? 0 : 1;
        }
        if (fixture == "blend_layer_cache_matches_direct") {
            return blend_layer_cache_matches_direct() ? 0 : 1;
        }
        if (fixture == "blend_version_refusal") return blend_version_refusal() ? 0 : 1;
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
    return 2;
}
