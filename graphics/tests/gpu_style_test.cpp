#include <vellum/graphics/skia_dawn_surface.hpp>
#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <array>
#include <algorithm>
#include <memory>
#include <cmath>
#include <cstdint>
#include <future>
#include <iostream>
#include <limits>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

bool capture(const Scene& scene, std::vector<std::uint8_t>& rgba,
             std::uint32_t& width, std::uint32_t& height) {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(scene.width),
         .height = static_cast<std::uint32_t>(scene.height)}, &error);
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(rgba, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    return true;
}

const std::uint8_t* pixel(
    const std::vector<std::uint8_t>& rgba, std::uint32_t width,
    std::uint32_t x, std::uint32_t y) {
    return rgba.data() + (static_cast<std::size_t>(y) * width + x) * 4U;
}

bool packaged_text_fallback_weights() {
    const std::vector<TextRun> runs{{
        .text = "Jost regular ",
        .style = {.font_family = "Jost", .font_weight = 400, .font_size = 24.0F},
    }, {
        .text = "Semibold 日本語",
        .style = {.font_family = "Jost", .font_weight = 600, .font_size = 24.0F},
    }};
    std::vector<std::future<TextMetrics>> futures;
    for (int index = 0; index < 8; ++index) {
        futures.push_back(std::async(std::launch::async, [runs] {
            TextMetrics metrics;
            std::string error;
            if (!SkiaDawnSurface::measure_text(runs, metrics, {}, &error)) {
                throw std::runtime_error(error);
            }
            return metrics;
        }));
    }
    const auto expected = futures.front().get();
    if (expected.width <= 0.0F || expected.ascent <= 0.0F ||
        expected.descent < 0.0F || expected.baseline != expected.ascent) {
        std::cerr << "invalid text metrics width=" << expected.width
                  << " ascent=" << expected.ascent
                  << " descent=" << expected.descent
                  << " baseline=" << expected.baseline << '\n';
        return false;
    }
    for (std::size_t index = 1; index < futures.size(); ++index) {
        const auto metrics = futures[index].get();
        if (std::abs(metrics.width - expected.width) > 0.01F ||
            std::abs(metrics.ascent - expected.ascent) > 0.01F ||
            std::abs(metrics.descent - expected.descent) > 0.01F) {
            std::cerr << "parallel text metrics diverged width=" << metrics.width
                      << " ascent=" << metrics.ascent
                      << " descent=" << metrics.descent << '\n';
            return false;
        }
    }
    const std::string combining = "e\xCC\x81" "e\xCC\x81";
    TextMetrics untracked;
    TextMetrics tracked;
    std::string error;
    const TextStyle cluster_style{
        .font_family = "Inter", .font_size = 24.0F,
    };
    auto tracked_style = cluster_style;
    tracked_style.letter_spacing = 7.0F;
    if (!SkiaDawnSurface::measure_text(
            {{.text = combining, .style = cluster_style}}, untracked, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = combining, .style = tracked_style}}, tracked, {}, &error) ||
        std::abs((tracked.width - untracked.width) - 14.0F) > 0.25F) {
        std::cerr << "tracking did not preserve combining clusters: " << error
                  << " delta=" << tracked.width - untracked.width << '\n';
        return false;
    }
    const TextStyle arabic_style{
        .font_family = "Inter", .font_size = 24.0F,
    };
    auto arabic_accent = arabic_style;
    arabic_accent.color = Color::hex(0x14B8A6);
    TextMetrics joined_arabic;
    TextMetrics styled_arabic;
    if (!SkiaDawnSurface::measure_text(
            {{.text = "\xD8\xB3\xD9\x84\xD8\xA7\xD9\x85", .style = arabic_style}},
            joined_arabic, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = "\xD8\xB3\xD9\x84", .style = arabic_style},
             {.text = "\xD8\xA7\xD9\x85", .style = arabic_accent}},
            styled_arabic, {}, &error) ||
        std::abs(joined_arabic.width - styled_arabic.width) > 0.25F) {
        std::cerr << "attributed runs changed Arabic joining width: " << error
                  << " joined=" << joined_arabic.width
                  << " styled=" << styled_arabic.width << '\n';
        return false;
    }
    TextMetrics unsupported;
    TextMetrics replacement;
    if (!SkiaDawnSurface::measure_text(
            {{.text = "launch \xF0\x9F\x9A\x80", .style = cluster_style}},
            unsupported, {}, &error) ||
        !SkiaDawnSurface::measure_text(
            {{.text = "launch ?", .style = cluster_style}},
            replacement, {}, &error) ||
        std::abs(unsupported.width - replacement.width) > 0.01F) {
        std::cerr << "unsupported glyph replacement was not deterministic: " << error
                  << " unsupported=" << unsupported.width
                  << " replacement=" << replacement.width << '\n';
        return false;
    }
    return true;
}

bool repeating_linear_gradient_non_square() {
    Scene scene{
        .width = 240.0F,
        .height = 140.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 240.0F, 140.0F},
            .children = {{
                .id = "gradient",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {20.0F, 30.0F, 200.0F, 80.0F},
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .repeating = true,
                    .repeat_length = 40.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0xFF0000)},
                        {.position = 1.0F, .color = Color::hex(0x0000FF)},
                    },
                }},
            }, {
                .id = "gradient-base",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {20.0F, 115.0F, 40.0F, 20.0F},
                .fill = Color::hex(0xFF00FF),
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0x00FF00)},
                        {.position = 1.0F, .color = Color::hex(0x00FF00)},
                    },
                }, {
                    .angle_degrees = 90.0F,
                    .repeating = true,
                    .repeat_length = 0.5F,
                    .stops = {
                        {.position = 0.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                        {.position = 1.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                    },
                }},
            }, {
                .id = "transparent-gradient-over-base",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {70.0F, 115.0F, 40.0F, 20.0F},
                .fill = Color::hex(0xFF00FF),
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                        {.position = 1.0F, .color = Color::rgba(1.0F, 1.0F, 1.0F, 0.0F)},
                    },
                }},
            }, {
                .id = "zero-width-gradient",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {130.0F, 115.0F, 0.0F, 20.0F},
                .fill_gradients = {{
                    .angle_degrees = 90.0F,
                    .stops = {
                        {.position = 0.0F, .color = Color::hex(0xFF0000)},
                        {.position = 1.0F, .color = Color::hex(0x0000FF)},
                    },
                }},
            }},
        },
    };
    const auto commands = make_paint_commands(scene);
    if (commands.size() != 4U || commands[0].fill_gradients.size() != 1U ||
        commands[1].fill_gradients.size() != 2U) {
        std::cerr << "gradient command was not materialized\n";
        return false;
    }
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    const auto* left = pixel(rgba, width, 30, 70);
    const auto* right = pixel(rgba, width, 210, 70);
    const auto* layered = pixel(rgba, width, 30, 125);
    const auto* base = pixel(rgba, width, 80, 125);
    const bool passed = left[0] > left[2] && right[2] > right[0] &&
        layered[0] < 15U && layered[1] > 240U && layered[2] < 15U &&
        base[0] > 240U && base[1] < 15U && base[2] > 240U;
    if (!passed) {
        std::cerr << "gradient pixels left=" << static_cast<int>(left[0]) << ','
                  << static_cast<int>(left[1]) << ',' << static_cast<int>(left[2])
                  << " right=" << static_cast<int>(right[0]) << ','
                  << static_cast<int>(right[1]) << ',' << static_cast<int>(right[2]) << '\n';
    }
    return passed;
}

bool outset_shadow_spread_diagonal() {
    Scene scene{
        .width = 240.0F,
        .height = 150.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 240.0F, 150.0F},
            .children = {{
                .id = "shadow",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {40.0F, 40.0F, 80.0F, 60.0F},
                .fill = Color::hex(0x14B8A6),
                .box_shadows = {{
                    .spread_radius = 10.0F,
                    .color = Color::hex(0x000000),
                }},
                .corner_radius = 20.0F,
            }, {
                .id = "shadow-only",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {160.0F, 40.0F, 50.0F, 60.0F},
                .box_shadows = {{
                    .spread_radius = 8.0F,
                    .color = Color::hex(0x000000),
                }},
                .corner_radius = 12.0F,
            }},
        },
    };
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    const auto* outside = pixel(rgba, width, 31, 31);
    const auto* diagonal = pixel(rgba, width, 36, 44);
    const auto* transparent_interior = pixel(rgba, width, 185, 70);
    const auto* shadow_only_outside = pixel(rgba, width, 155, 70);
    return outside[0] > 240U && outside[1] > 240U && outside[2] > 240U &&
           diagonal[0] < 40U && diagonal[1] < 40U && diagonal[2] < 40U &&
           transparent_interior[0] > 240U && transparent_interior[1] > 240U &&
           transparent_interior[2] > 240U && shadow_only_outside[0] < 40U &&
           shadow_only_outside[1] < 40U && shadow_only_outside[2] < 40U;
}

bool attributed_text_runs_materialization() {
    Scene scene{
        .width = 320.0F,
        .height = 130.0F,
        .background = Color::hex(0xFFFFFF),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 320.0F, 130.0F},
            .children = {{
                .id = "runs",
                .kind = SceneNode::Kind::text,
                .bounds = {12.0F, 20.0F, 296.0F, 60.0F},
                .text_runs = {{
                    .text = "Regular ",
                    .style = {.font_family = "Jost", .font_weight = 400,
                              .font_size = 26.0F, .color = Color::hex(0x111827)},
                }, {
                    .text = "Accent",
                    .style = {.font_family = "Jost", .font_weight = 600,
                              .font_size = 26.0F, .letter_spacing = 1.5F,
                              .color = Color::hex(0x14B8A6), .underline = true,
                              .strikethrough = true},
                }},
            }, {
                .id = "baseline",
                .kind = SceneNode::Kind::text,
                .bounds = {12.0F, 75.0F, 296.0F, 45.0F},
                .text_runs = {{
                    .text = "H",
                    .style = {.font_family = "Jost", .font_size = 30.0F,
                              .color = Color::hex(0xE00000)},
                }, {
                    .text = "H",
                    .style = {.font_family = "Jost", .font_size = 14.0F,
                              .color = Color::hex(0x0000E0)},
                }},
            }},
        },
    };
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0, height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    std::size_t dark = 0, accent = 0;
    int red_bottom = -1;
    int blue_bottom = -1;
    for (std::size_t index = 0; index < rgba.size(); index += 4U) {
        if (rgba[index] < 80U && rgba[index + 1U] < 100U && rgba[index + 2U] < 120U) ++dark;
        if (rgba[index] < 80U && rgba[index + 1U] > 120U && rgba[index + 2U] > 100U) ++accent;
        const int y = static_cast<int>((index / 4U) / width);
        if (rgba[index] > 150U && rgba[index + 1U] < 100U && rgba[index + 2U] < 100U) {
            red_bottom = std::max(red_bottom, y);
        }
        if (rgba[index] < 100U && rgba[index + 1U] < 100U && rgba[index + 2U] > 150U) {
            blue_bottom = std::max(blue_bottom, y);
        }
    }
    TextMetrics line_metrics;
    std::string error;
    if (!SkiaDawnSurface::measure_text(
            scene.root.children[0].text_runs, line_metrics, {}, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto decoration_start_y = static_cast<std::uint32_t>(std::floor(
        scene.root.children[0].bounds.y + line_metrics.baseline + 1.0F));
    std::size_t decoration_pixels = 0U;
    for (std::uint32_t y = decoration_start_y; y < 70U; ++y) {
        for (std::uint32_t x = 0U; x < width; ++x) {
            const auto* candidate = pixel(rgba, width, x, y);
            if (candidate[0] < 80U && candidate[1] > 120U && candidate[2] > 100U) {
                ++decoration_pixels;
            }
        }
    }
    const bool passed = dark > 20U && accent > 20U &&
        red_bottom >= 0 && blue_bottom >= 0 &&
        std::abs(red_bottom - blue_bottom) <= 2 && decoration_pixels > 10U;
    if (!passed) {
        std::cerr << "attributed pixels dark=" << dark << " accent=" << accent
                  << " red_bottom=" << red_bottom << " blue_bottom=" << blue_bottom
                  << " decoration_pixels=" << decoration_pixels
                  << " decoration_start_y=" << decoration_start_y << '\n';
    }
    return passed;
}


// Two textured quads, one untextured quad and a clipped quad, read back:
// texture coordinates choose the texel (the left half of the first quad
// samples the texture's right texel), a vertex colour multiplies the texel,
// an untextured mesh draws its vertex colours, and the clip rectangle cuts the
// last quad off.
bool textured_triangle_mesh() {
    auto texture = std::make_shared<MeshTexture>(MeshTexture{
        .width = 2,
        .height = 1,
        .premultiplied_rgba = {255, 0, 0, 255, 0, 0, 255, 255},
    });
    const auto quad = [](float x0, float y0, float x1, float y1, float u0, float u1,
                         std::uint32_t rgba) {
        auto mesh = std::make_shared<TriangleMesh>();
        mesh->vertices = {
            {.x = x0, .y = y0, .u = u0, .v = 0.0F, .premultiplied_rgba = rgba},
            {.x = x1, .y = y0, .u = u1, .v = 0.0F, .premultiplied_rgba = rgba},
            {.x = x1, .y = y1, .u = u1, .v = 1.0F, .premultiplied_rgba = rgba},
            {.x = x0, .y = y1, .u = u0, .v = 1.0F, .premultiplied_rgba = rgba},
        };
        mesh->indices = {0, 1, 2, 0, 2, 3};
        return mesh;
    };
    // u runs from 1 at the left edge to 0 at the right edge.
    auto mirrored = quad(0.0F, 0.0F, 40.0F, 20.0F, 1.0F, 0.0F, 0xFFFFFFFFU);
    mirrored->texture = texture;
    // A premultiplied half-alpha white modulating the same texture.
    auto modulated = quad(0.0F, 20.0F, 40.0F, 40.0F, 0.0F, 1.0F, 0x80808080U);
    modulated->texture = texture;
    auto untextured = quad(0.0F, 40.0F, 40.0F, 60.0F, 0.0F, 1.0F, 0x00FF00FFU);
    auto clipped = quad(0.0F, 60.0F, 40.0F, 80.0F, 0.0F, 1.0F, 0xFFFF00FFU);
    clipped->clip_enabled = true;
    clipped->clip = {0.0F, 60.0F, 20.0F, 20.0F};

    Scene scene{.width = 40.0F, .height = 80.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    int index = 0;
    for (const auto& mesh : {mirrored, modulated, untextured, clipped}) {
        scene.root.children.push_back({
            .id = "mesh-" + std::to_string(index++),
            .kind = SceneNode::Kind::mesh,
            .mesh = mesh,
        });
    }
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!capture(scene, rgba, width, height) || width != 40U || height != 80U) return false;

    struct Probe {
        const char* what;
        std::uint32_t x;
        std::uint32_t y;
        std::array<int, 4> expected;
    };
    const Probe probes[] = {
        {"mirrored left samples the right texel", 5, 10, {0, 0, 255, 255}},
        {"mirrored right samples the left texel", 35, 10, {255, 0, 0, 255}},
        // Half-alpha colours composited over the opaque black background.
        {"modulated left is half-alpha red", 5, 30, {128, 0, 0, 255}},
        {"modulated right is half-alpha blue", 35, 30, {0, 0, 128, 255}},
        {"untextured mesh draws its vertex colour", 20, 50, {0, 255, 0, 255}},
        {"clipped mesh inside the clip", 10, 70, {255, 255, 0, 255}},
        {"clipped mesh outside the clip", 30, 70, {0, 0, 0, 255}},
    };
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* actual = pixel(rgba, width, probe.x, probe.y);
        for (int channel = 0; channel < 4; ++channel) {
            if (std::abs(int(actual[channel]) - probe.expected[channel]) > 2) {
                std::cerr << "textured_triangle_mesh: " << probe.what << ": got "
                          << int(actual[0]) << ',' << int(actual[1]) << ','
                          << int(actual[2]) << ',' << int(actual[3]) << '\n';
                passed = false;
                break;
            }
        }
    }
    return passed;
}

bool scene_version_refusal() {
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    Scene scene{
        .width = 32.0F,
        .height = 32.0F,
        .background = Color::hex(0x204060),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 32.0F, 32.0F},
            .children = {{
                .id = "swatch",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {4.0F, 4.0F, 12.0F, 12.0F},
                .fill = Color::hex(0xF0A030),
            }},
        },
    };
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
            std::cerr << "unexpected render result: " << why << '\n';
            return false;
        }
        return surface->capture_rgba(after, after_width, after_height, &why) &&
               after == first;
    };
    Scene future = scene;
    future.scene_version = kSceneVersion + 1U;
    Scene unknown_kind = scene;
    unknown_kind.root.children[0].kind = static_cast<SceneNode::Kind>(42);
    if (!refused_keeps_frame(future, "scene version " + std::to_string(kSceneVersion + 1U) +
                                         " is not supported by this renderer") ||
        !refused_keeps_frame(
            unknown_kind, "scene contains unsupported node kind 42 at swatch")) {
        return false;
    }
    std::vector<std::uint8_t> again;
    if (!surface->render(scene, &error) ||
        !surface->capture_rgba(again, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    if (again != first) return false;
    Scene legacy = scene;
    legacy.scene_version = kMinSupportedSceneVersion;
    std::vector<std::uint8_t> old_version;
    if (!surface->render(legacy, &error) ||
        !surface->capture_rgba(old_version, width, height, &error) || old_version != first) {
        std::cerr << "version 1 scene did not render as before: " << error << '\n';
        return false;
    }
    const std::string carries =
        "scene version 1 cannot carry the transform, opacity, or corner radii at swatch";
    Scene turned = legacy;
    turned.root.children[0].transform = Affine2D{.a = 3.0F, .d = 3.0F};
    Scene faded = legacy;
    faded.root.children[0].opacity = 0.5F;
    Scene cornered = legacy;
    cornered.root.children[0].corner_radii = CornerRadii{6.0F, 6.0F, 6.0F, 6.0F};
    const auto with_path = [](Scene candidate, std::uint32_t version) {
        candidate.scene_version = version;
        candidate.root.children.push_back({
            .id = "outline",
            .kind = SceneNode::Kind::path,
            .path = std::make_shared<const PathGeometry>(PathGeometry{
                .verbs = {PathVerb::move, PathVerb::line},
                .values = {0.0F, 0.0F, 30.0F, 30.0F},
                .stroke = {.color = Color::hex(0xFFFFFF), .width = 2.0F},
            }),
        });
        return candidate;
    };
    // An image node needs version 4; at 3 it is refused, at 4 it draws.
    const auto with_image = [](Scene candidate, std::uint32_t version) {
        candidate.scene_version = version;
        candidate.root.children.push_back({
            .id = "logo",
            .kind = SceneNode::Kind::image,
            .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
            .image = ImageReference{.content_hash = "sha256:" + std::string(64, 'd')},
        });
        return candidate;
    };
    const auto accepted_at_current = [&](const Scene& candidate) {
        std::string why;
        if (!surface->render(candidate, &why)) {
            std::cerr << "a version 4 image scene was refused: " << why << '\n';
            return false;
        }
        const auto& lost = surface->diagnostics();
        return lost.size() == 1U && lost[0].code == kImageAssetMissingCode;
    };
    return refused_keeps_frame(turned, carries) && refused_keeps_frame(faded, carries) &&
           refused_keeps_frame(cornered, carries) &&
           refused_keeps_frame(with_path(scene, 1U),
                               "scene version 1 cannot carry the path node outline") &&
           refused_keeps_frame(with_path(scene, 2U),
                               "scene version 2 cannot carry the path node outline") &&
           refused_keeps_frame(with_image(scene, 3U),
                               "scene version 3 cannot carry the image node logo") &&
           accepted_at_current(with_image(scene, 4U));
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
                       const std::vector<Probe>& probes) {
    std::vector<std::uint8_t> rgba;
    std::vector<std::uint8_t> again;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t again_width = 0;
    std::uint32_t again_height = 0;
    if (!capture(scene, rgba, width, height) ||
        !capture(scene, again, again_width, again_height)) {
        return false;
    }
    if (width != static_cast<std::uint32_t>(scene.width) ||
        height != static_cast<std::uint32_t>(scene.height) || rgba != again) {
        std::cerr << fixture << ": capture is not deterministic or has the wrong size\n";
        return false;
    }
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* actual = pixel(rgba, width, probe.x, probe.y);
        for (int channel = 0; channel < 4; ++channel) {
            if (std::abs(int(actual[channel]) - probe.expected[channel]) > 2) {
                std::cerr << fixture << ": " << probe.what << ": got "
                          << int(actual[0]) << ',' << int(actual[1]) << ','
                          << int(actual[2]) << ',' << int(actual[3]) << '\n';
                passed = false;
                break;
            }
        }
    }
    return passed;
}

// A bar turned a quarter about its centre, a square scaled about its origin
// inside a translated parent, a flattened square, and a refused NaN transform.
bool node_transform_rotation_scale() {
    Scene scene{.width = 64.0F, .height = 64.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "bar",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {16.0F, 28.0F, 32.0F, 8.0F},
        .fill = Color::hex(0xFF0000),
        .transform = concat(translation(16.0F, 4.0F),
            concat(Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F},
                   translation(-16.0F, -4.0F))),
    });
    scene.root.children.push_back({
        .id = "frame",
        .kind = SceneNode::Kind::group,
        .bounds = {4.0F, 4.0F, 0.0F, 0.0F},
        .children = {{
            .id = "square",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
            .fill = Color::hex(0x00FF00),
            .transform = Affine2D{.a = 2.0F, .d = 2.0F},
        }},
    });
    scene.root.children.push_back({
        .id = "flattened",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {48.0F, 48.0F, 12.0F, 12.0F},
        .fill = Color::hex(0x0000FF),
        .transform = Affine2D{.a = 0.0F, .d = 0.0F},
    });
    if (!capture_and_probe("node_transform_rotation_scale", scene, {
            {"a zero-determinant transform paints nothing", 54, 54, {0, 0, 0, 255}},
            {"turned bar covers its new top", 32, 20, {255, 0, 0, 255}},
            {"turned bar covers its new bottom", 32, 44, {255, 0, 0, 255}},
            {"turned bar left its old left end", 20, 32, {0, 0, 0, 255}},
            {"turned bar left its old right end", 44, 32, {0, 0, 0, 255}},
            {"scaled square reaches past its own size", 17, 17, {0, 255, 0, 255}},
            {"scaled square stops at twice its size", 22, 22, {0, 0, 0, 255}},
        })) {
        return false;
    }
    std::string error;
    auto surface = SkiaDawnSurface::create({.width = 64, .height = 64}, &error);
    auto broken = scene;
    broken.root.children[1].children[0].transform->a = std::nanf("");
    if (!surface || surface->render(broken, &error) ||
        error != "scene node square has a non-finite transform, opacity, or corner radius") {
        std::cerr << "non-finite transform was not refused: " << error << '\n';
        return false;
    }
    return true;
}

// Overlapping members of a faded group composite before fading, a lone faded
// rectangle folds its opacity into its paint, and nested opacity multiplies.
bool group_opacity_layers() {
    Scene scene{.width = 96.0F, .height = 32.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    const auto white = [](std::string id, Rect bounds, float opacity = 1.0F) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = bounds,
            .fill = Color::hex(0xFFFFFF),
            .opacity = opacity,
        };
    };
    scene.root.children.push_back({
        .id = "layered",
        .kind = SceneNode::Kind::group,
        .children = {white("layered/a", {0.0F, 0.0F, 24.0F, 32.0F}),
                     white("layered/b", {8.0F, 0.0F, 24.0F, 32.0F})},
        .opacity = 0.5F,
    });
    scene.root.children.push_back(white("folded", {40.0F, 0.0F, 16.0F, 32.0F}, 0.5F));
    scene.root.children.push_back({
        .id = "nested",
        .kind = SceneNode::Kind::group,
        .children = {white("nested/half", {64.0F, 0.0F, 16.0F, 32.0F}, 0.5F),
                     white("nested/full", {80.0F, 0.0F, 16.0F, 32.0F})},
        .opacity = 0.5F,
    });
    return capture_and_probe("group_opacity_layers", scene, {
        {"faded group outside the overlap", 4, 16, {128, 128, 128, 255}},
        {"faded group inside the overlap", 16, 16, {128, 128, 128, 255}},
        {"faded group, second member only", 28, 16, {128, 128, 128, 255}},
        {"lone faded rectangle", 48, 16, {128, 128, 128, 255}},
        {"half inside a half group", 72, 16, {64, 64, 64, 255}},
        {"opaque inside a half group", 88, 16, {128, 128, 128, 255}},
    });
}

// Radii whose sum exceeds a side scale down together, as CSS radii do.
bool radii_overflow_scaling() {
    Scene scene{.width = 40.0F, .height = 40.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "overflow",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 40.0F, 40.0F},
        .fill = Color::hex(0xFFFFFF),
        .corner_radii = CornerRadii{40.0F, 40.0F, 0.0F, 0.0F},
    });
    return capture_and_probe("radii_overflow_scaling", scene, {
        {"outside the scaled top-left arc", 4, 4, {0, 0, 0, 255}},
        {"outside the scaled top-right arc", 35, 4, {0, 0, 0, 255}},
        {"inside the scaled arcs at the top centre", 20, 2, {255, 255, 255, 255}},
        {"inside the scaled arc below its centre", 2, 24, {255, 255, 255, 255}},
        {"square bottom corners", 1, 38, {255, 255, 255, 255}},
    });
}

// A faded command whose own paint overlaps itself draws through a layer, and
// a faded group's layer bounds reach a blurred shadow.
bool faded_command_layers() {
    Scene scene{.width = 112.0F, .height = 64.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    const auto red = Color::hex(0xFF0000);
    scene.root.children.push_back({
        .id = "gradient",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 16.0F, 16.0F},
        .fill = Color::hex(0xFFFFFF),
        .fill_gradients = {{.stops = {{.position = 0.0F, .color = red},
                                      {.position = 1.0F, .color = red}}}},
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "shadows",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {24.0F, 0.0F, 8.0F, 16.0F},
        .fill = Color::hex(0xFFFFFF),
        .box_shadows = {{.offset_x = 10.0F, .color = Color::hex(0xFFFFFF)},
                        {.offset_x = 10.0F, .color = Color::hex(0xFFFFFF)}},
        .opacity = 0.5F,
    });
    auto quad = std::make_shared<TriangleMesh>();
    quad->vertices = {
        {.x = 0.0F, .y = 0.0F}, {.x = 16.0F, .y = 0.0F},
        {.x = 16.0F, .y = 16.0F}, {.x = 0.0F, .y = 16.0F},
    };
    quad->indices = {0, 1, 2, 0, 2, 3};
    scene.root.children.push_back({
        .id = "mesh",
        .kind = SceneNode::Kind::mesh,
        .bounds = {48.0F, 0.0F, 16.0F, 16.0F},
        .mesh = quad,
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "decorated",
        .kind = SceneNode::Kind::text,
        .bounds = {0.0F, 24.0F, 64.0F, 36.0F},
        .fill = Color::hex(0xFFFFFF),
        .text = "gjpqy",
        .font_size = 28.0F,
        .underline = true,
        .opacity = 0.5F,
    });
    SceneNode blurred{
        .id = "blurred",
        .kind = SceneNode::Kind::group,
        .opacity = 0.5F,
    };
    blurred.children.push_back({
        .id = "blurred/box",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {76.0F, 24.0F, 12.0F, 12.0F},
        .fill = Color::hex(0xFFFFFF),
        .box_shadows = {{.blur_radius = 8.0F, .color = Color::hex(0xFFFFFF)}},
    });
    blurred.children.push_back({
        .id = "blurred/dot",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {100.0F, 56.0F, 4.0F, 4.0F},
        .fill = Color::hex(0xFFFFFF),
    });
    scene.root.children.push_back(std::move(blurred));

    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!capture_and_probe("faded_command_layers", scene, {
            {"faded gradient covers its fill before fading", 8, 8, {128, 0, 0, 255}},
            {"faded overlapping shadows", 38, 8, {128, 128, 128, 255}},
            {"faded box under its shadows", 28, 8, {128, 128, 128, 255}},
            {"faded mesh", 56, 8, {128, 128, 128, 255}},
            {"faded group box", 82, 30, {128, 128, 128, 255}},
        }) ||
        !capture(scene, rgba, width, height)) {
        return false;
    }
    int brightest = 0;
    int lit = 0;
    for (std::uint32_t y = 24; y < 64; ++y) {
        for (std::uint32_t x = 0; x < 64; ++x) {
            const int value = pixel(rgba, width, x, y)[0];
            brightest = std::max(brightest, value);
            if (value > 100) ++lit;
        }
    }
    const int halo = pixel(rgba, width, 92, 30)[0];
    if (brightest > 132 || lit < 50 || halo < 8) {
        std::cerr << "faded_command_layers: decorated text brightest=" << brightest
                  << " lit=" << lit << ", blurred shadow beyond the box=" << halo << '\n';
        return false;
    }
    return true;
}

// A blurred, spread shadow keeps its halo inside a faded, scaled group nested
// in another faded group, so each bounded layer reaches the shadow's extent.
bool nested_faded_shadow_layers() {
    Scene scene{.width = 96.0F, .height = 64.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    const auto white = Color::hex(0xFFFFFF);
    SceneNode inner{
        .id = "inner",
        .kind = SceneNode::Kind::group,
        .bounds = {20.0F, 20.0F, 0.0F, 0.0F},
        .transform = Affine2D{.a = 2.0F, .d = 2.0F},
        .opacity = 0.5F,
    };
    inner.children.push_back({
        .id = "inner/box",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 8.0F, 8.0F},
        .fill = white,
        .box_shadows = {{.blur_radius = 4.0F, .spread_radius = 2.0F, .color = white}},
    });
    inner.children.push_back({
        .id = "inner/dot",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 18.0F, 2.0F, 2.0F},
        .fill = white,
    });
    SceneNode outer{
        .id = "outer",
        .kind = SceneNode::Kind::group,
        .opacity = 0.5F,
    };
    outer.children.push_back(std::move(inner));
    outer.children.push_back({
        .id = "outer/dot",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {4.0F, 56.0F, 4.0F, 4.0F},
        .fill = white,
    });
    scene.root.children.push_back(std::move(outer));
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    // The box is drawn at (20, 20) to (36, 36); its spread reaches 40 and the
    // blur continues past it.
    if (!capture_and_probe("nested_faded_shadow_layers", scene, {
            {"box at a quarter", 28, 28, {64, 64, 64, 255}},
            {"outer member at a half", 6, 58, {128, 128, 128, 255}},
        }) ||
        !capture(scene, rgba, width, height)) {
        return false;
    }
    const int spread = pixel(rgba, width, 38, 28)[0];
    const int halo = pixel(rgba, width, 43, 28)[0];
    if (spread < 20 || halo < 4 || halo >= spread) {
        std::cerr << "nested_faded_shadow_layers: spread=" << spread << " halo=" << halo << '\n';
        return false;
    }
    return true;
}

// A transform carries a shadow's offset and turns text with its node.
bool transformed_shadow_and_text() {
    Scene scene{.width = 128.0F, .height = 128.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "shaded",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {4.0F, 4.0F, 8.0F, 8.0F},
        .fill = Color::hex(0xFF0000),
        .box_shadows = {{.offset_x = 8.0F, .color = Color::hex(0x00FF00)}},
        .transform = Affine2D{.a = 2.0F, .d = 2.0F},
    });
    scene.root.children.push_back({
        .id = "turned",
        .kind = SceneNode::Kind::text,
        .bounds = {88.0F, 8.0F, 96.0F, 24.0F},
        .fill = Color::hex(0xFFFFFF),
        .text = "WWWWW",
        .font_size = 18.0F,
        .transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F},
    });
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!capture_and_probe("transformed_shadow_and_text", scene, {
            {"scaled box", 16, 10, {255, 0, 0, 255}},
            {"shadow offset doubles with the scale", 28, 10, {0, 255, 0, 255}},
            {"shadow ends at the scaled offset", 40, 10, {0, 0, 0, 255}},
        }) ||
        !capture(scene, rgba, width, height)) {
        return false;
    }
    std::uint32_t left = width;
    std::uint32_t right = 0;
    std::uint32_t top = height;
    std::uint32_t bottom = 0;
    for (std::uint32_t y = 0; y < height; ++y) {
        for (std::uint32_t x = 48; x < width; ++x) {
            if (pixel(rgba, width, x, y)[1] < 128) continue;
            left = std::min(left, x);
            right = std::max(right, x);
            top = std::min(top, y);
            bottom = std::max(bottom, y);
        }
    }
    if (right < left || bottom - top < 2U * (right - left) || bottom - top < 40U ||
        right > 90U) {
        std::cerr << "transformed_shadow_and_text: text ink spans x " << left << ".." << right
                  << " y " << top << ".." << bottom << '\n';
        return false;
    }
    return true;
}

// Each corner keeps its own radius, and the
// uniform shorthand still rounds every corner.
bool per_corner_radii() {
    Scene scene{.width = 128.0F, .height = 64.0F, .background = Color::hex(0x000000)};
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "corners",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {0.0F, 0.0F, 64.0F, 64.0F},
        .fill = Color::hex(0xFFFFFF),
        .corner_radius = 4.0F,
        .corner_radii = CornerRadii{0.0F, 24.0F, 0.0F, 24.0F},
    });
    scene.root.children.push_back({
        .id = "shorthand",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {72.0F, 8.0F, 48.0F, 48.0F},
        .fill = Color::hex(0x00FF00),
        .corner_radius = 16.0F,
    });
    return capture_and_probe("per_corner_radii", scene, {
        {"square top-left corner", 1, 1, {255, 255, 255, 255}},
        {"rounded top-right corner", 62, 1, {0, 0, 0, 255}},
        {"square bottom-right corner", 62, 62, {255, 255, 255, 255}},
        {"rounded bottom-left corner", 1, 62, {0, 0, 0, 255}},
        {"rounded corner interior", 56, 8, {255, 255, 255, 255}},
        {"shorthand rounds its top-left", 73, 9, {0, 0, 0, 255}},
        {"shorthand rounds its bottom-right", 118, 54, {0, 0, 0, 255}},
        {"shorthand interior", 96, 32, {0, 255, 0, 255}},
    });
}

bool same_pixel(const std::uint8_t* value, std::array<std::uint8_t, 4> expected,
                int tolerance = 2) {
    for (std::size_t channel = 0; channel < 4U; ++channel) {
        if (std::abs(static_cast<int>(value[channel]) - static_cast<int>(expected[channel])) >
            tolerance) {
            return false;
        }
    }
    return true;
}

SceneNode star_path(std::string id, float x, FillRule rule) {
    PathGeometry star{.fill_rule = rule};
    for (int index = 0; index < 5; ++index) {
        const float angle = -1.5707963F + static_cast<float>(index * 2 % 5) * 1.2566371F;
        star.verbs.push_back(index == 0 ? PathVerb::move : PathVerb::line);
        star.values.insert(star.values.end(),
                           {30.0F + 28.0F * std::cos(angle), 30.0F + 28.0F * std::sin(angle)});
    }
    star.verbs.push_back(PathVerb::close);
    return {
        .id = std::move(id),
        .kind = SceneNode::Kind::path,
        .bounds = {x, 0.0F, 60.0F, 60.0F},
        .fill = Color::hex(0xFFFFFF),
        .path = std::make_shared<const PathGeometry>(star),
    };
}

bool path_fill_rule_and_stroke() {
    PathGeometry dashed{
        .verbs = {PathVerb::move, PathVerb::line},
        .values = {4.0F, 0.0F, 116.0F, 0.0F},
        .stroke = {.color = Color::hex(0xFF0000), .width = 4.0F, .cap = StrokeCap::round,
                   .dash_array = {12.0F, 12.0F}},
    };
    Scene scene{
        .width = 128.0F,
        .height = 80.0F,
        .background = Color::hex(0x000000),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 128.0F, 80.0F},
            .children = {
                star_path("nonzero", 0.0F, FillRule::nonzero),
                star_path("evenodd", 64.0F, FillRule::evenodd),
                {
                    .id = "dashes",
                    .kind = SceneNode::Kind::path,
                    .bounds = {0.0F, 70.0F, 128.0F, 0.0F},
                    .path = std::make_shared<const PathGeometry>(dashed),
                },
            },
        },
    };
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!capture(scene, rgba, width, height)) return false;
    const std::array<std::uint8_t, 4> white{255, 255, 255, 255};
    const std::array<std::uint8_t, 4> black{0, 0, 0, 255};
    const std::array<std::uint8_t, 4> red{255, 0, 0, 255};
    struct Probe {
        const char* what;
        std::uint32_t x;
        std::uint32_t y;
        std::array<std::uint8_t, 4> expected;
    };
    const std::array<Probe, 9> probes{{
        {"nonzero centre is filled", 30, 30, white},
        {"evenodd centre is a hole", 94, 30, black},
        {"nonzero point is filled", 30, 12, white},
        {"evenodd point is filled", 94, 12, white},
        {"outside the star is empty", 2, 58, black},
        {"the first dash is drawn", 10, 70, red},
        {"the first gap is empty", 22, 70, black},
        {"the second dash is drawn", 34, 70, red},
        {"the round cap reaches before the start", 3, 70, red},
    }};
    bool passed = true;
    for (const auto& probe : probes) {
        const auto* value = pixel(rgba, width, probe.x, probe.y);
        if (!same_pixel(value, probe.expected)) {
            std::cerr << probe.what << ": pixel (" << probe.x << ", " << probe.y << ") is "
                      << int(value[0]) << ',' << int(value[1]) << ',' << int(value[2]) << ','
                      << int(value[3]) << '\n';
            passed = false;
        }
    }
    return passed;
}

bool path_arc_gradient_and_refusal() {
    std::string reason;
    PathGeometry circle;
    if (!parse_svg_path_data("M10 30 A20 20 0 0 1 50 30 A20 20 0 0 1 10 30 Z", circle, &reason)) {
        std::cerr << reason << '\n';
        return false;
    }
    PathGeometry blob{
        .verbs = {PathVerb::move, PathVerb::cubic, PathVerb::line, PathVerb::line, PathVerb::close},
        .values = {0.0F, 0.0F, 20.0F, -10.0F, 40.0F, 10.0F, 60.0F, 0.0F, 60.0F, 40.0F,
                   0.0F, 40.0F},
    };
    const std::vector<LinearGradient> red_to_blue{{
        .angle_degrees = 90.0F,
        .stops = {
            {.position = 0.0F, .color = Color::hex(0xFF0000)},
            {.position = 1.0F, .color = Color::hex(0x0000FF)},
        },
    }};
    Scene scene{
        .width = 128.0F,
        .height = 112.0F,
        .background = Color::hex(0x000000),
        .root = {
            .id = "root",
            .kind = SceneNode::Kind::group,
            .bounds = {0.0F, 0.0F, 128.0F, 112.0F},
            .children = {
                {
                    .id = "circle",
                    .kind = SceneNode::Kind::path,
                    .bounds = {0.0F, 0.0F, 60.0F, 60.0F},
                    .fill = Color::hex(0x00FF00),
                    .path = std::make_shared<const PathGeometry>(circle),
                },
                {
                    .id = "blob",
                    .kind = SceneNode::Kind::path,
                    .bounds = {64.0F, 12.0F, 60.0F, 40.0F},
                    .fill_gradients = {{
                        .angle_degrees = 90.0F,
                        .stops = {
                            {.position = 0.0F, .color = Color::hex(0xFF0000)},
                            {.position = 1.0F, .color = Color::hex(0x0000FF)},
                        },
                    }},
                    .path = std::make_shared<const PathGeometry>(blob),
                },
                {
                    .id = "unboxed",
                    .kind = SceneNode::Kind::path,
                    .bounds = {0.0F, 64.0F, 0.0F, 0.0F},
                    .fill_gradients = red_to_blue,
                    .path = std::make_shared<const PathGeometry>(blob),
                },
            },
        },
    };
    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto surface = SkiaDawnSurface::create({.width = 128, .height = 112}, &error);
    std::vector<std::uint8_t> first;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    if (!surface || !surface->render(scene, &error) ||
        !surface->capture_rgba(first, width, height, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto* centre = pixel(first, width, 30, 30);
    const auto* corner = pixel(first, width, 12, 12);
    const auto* left = pixel(first, width, 68, 40);
    const auto* right = pixel(first, width, 120, 40);
    if (!same_pixel(centre, {0, 255, 0, 255}) || !same_pixel(corner, {0, 0, 0, 255})) {
        std::cerr << "the arc circle is not filled as a circle\n";
        return false;
    }
    if (!(left[0] > 200 && left[2] < 60 && right[2] > 200 && right[0] < 60)) {
        std::cerr << "the cubic path's gradient does not run from red to blue\n";
        return false;
    }
    const auto* unboxed_left = pixel(first, width, 4, 92);
    const auto* unboxed_right = pixel(first, width, 56, 92);
    if (!(unboxed_left[0] > 200 && unboxed_left[2] < 60 && unboxed_right[2] > 200 &&
          unboxed_right[0] < 60)) {
        std::cerr << "a gradient on a path without a layout box does not use its control box\n";
        return false;
    }

    Scene invalid = scene;
    auto not_finite = std::make_shared<PathGeometry>(*scene.root.children[1].path);
    not_finite->values[3] = std::numeric_limits<float>::quiet_NaN();
    invalid.root.children[1].path = not_finite;
    Scene unbalanced = scene;
    auto short_values = std::make_shared<PathGeometry>(*scene.root.children[1].path);
    short_values->values.pop_back();
    unbalanced.root.children[1].path = short_values;
    std::vector<std::uint8_t> after;
    const auto refused = [&](const Scene& candidate, const std::string& expected) {
        std::string why;
        if (surface->render(candidate, &why) || why != expected) {
            std::cerr << "unexpected render result: " << why << '\n';
            return false;
        }
        return surface->capture_rgba(after, width, height, &why) && after == first;
    };
    Scene infinite = scene;
    auto infinite_values = std::make_shared<PathGeometry>(*scene.root.children[1].path);
    infinite_values->values[6] = std::numeric_limits<float>::infinity();
    infinite.root.children[1].path = infinite_values;
    Scene ghost = scene;
    ghost.root.children[1].path = nullptr;
    return refused(invalid, "path blob cannot be drawn: a path coordinate is not finite") &&
           refused(infinite, "path blob cannot be drawn: a path coordinate is not finite") &&
           refused(unbalanced,
                   "path blob cannot be drawn: a path command has fewer values than its arity") &&
           refused(ghost, "path blob has no geometry");
}

// Every pixel a stroked path paints lies inside its damage bounds, for a
// diagonal square-capped segment and for a sharp miter that reaches far past
// half the stroke width.
bool path_damage_bounds_contain_paint() {
    const auto stroked = [](std::vector<float> values, StrokeJoin join, StrokeCap cap) {
        auto geometry = std::make_shared<PathGeometry>();
        geometry->values = std::move(values);
        geometry->verbs.assign(geometry->values.size() / 2U, PathVerb::line);
        geometry->verbs.front() = PathVerb::move;
        geometry->stroke = {.color = Color::hex(0xFFFFFF), .width = 4.0F, .join = join,
                            .cap = cap, .miter_limit = 10.0F};
        return geometry;
    };
    struct Case final {
        const char* what;
        Rect origin;
        std::shared_ptr<const PathGeometry> path;
        float painted_right_at_least;
    };
    const std::array<Case, 2> cases{{
        {"diagonal square cap", {10.0F, 10.0F, 0.0F, 0.0F},
         stroked({0.0F, 0.0F, 30.0F, 40.0F}, StrokeJoin::bevel, StrokeCap::square), 41.0F},
        {"sharp miter", {8.0F, 16.0F, 0.0F, 0.0F},
         stroked({0.0F, 0.0F, 40.0F, 0.0F, 0.0F, 10.0F}, StrokeJoin::miter, StrokeCap::butt),
         62.0F},
    }};
    bool passed = true;
    for (const auto& test : cases) {
        const SceneNode node{
            .id = "stroke",
            .kind = SceneNode::Kind::path,
            .bounds = test.origin,
            .path = test.path,
        };
        const Scene scene{
            .width = 96.0F,
            .height = 64.0F,
            .background = Color::hex(0x000000),
            .root = {.id = "root", .kind = SceneNode::Kind::group,
                     .bounds = {0.0F, 0.0F, 96.0F, 64.0F}, .children = {node}},
        };
        const DamagePropagation none;
        const auto bounds = node_paint_bounds(node, test.origin, {0.0F, 0.0F, 96.0F, 64.0F}, none);
        const auto pixels = bounds ? to_pixel_rect(*bounds, 1.0F) : std::nullopt;
        std::vector<std::uint8_t> rgba;
        std::uint32_t width = 0;
        std::uint32_t height = 0;
        if (!pixels || !capture(scene, rgba, width, height)) return false;
        std::uint32_t painted = 0;
        std::uint32_t outside = 0;
        std::int64_t rightmost = -1;
        for (std::uint32_t y = 0; y < height; ++y) {
            for (std::uint32_t x = 0; x < width; ++x) {
                const auto* value = pixel(rgba, width, x, y);
                if (value[0] == 0 && value[1] == 0 && value[2] == 0) continue;
                ++painted;
                rightmost = std::max<std::int64_t>(rightmost, x);
                const auto px = static_cast<std::int32_t>(x);
                const auto py = static_cast<std::int32_t>(y);
                if (px < pixels->left || px >= pixels->right || py < pixels->top ||
                    py >= pixels->bottom) {
                    ++outside;
                }
            }
        }
        if (painted == 0U || outside != 0U ||
            static_cast<float>(rightmost) < test.painted_right_at_least) {
            std::cerr << test.what << ": " << outside << " of " << painted
                      << " painted pixels lie outside the damage bounds; rightmost " << rightmost
                      << '\n';
            passed = false;
        }
    }
    return passed;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string fixture = argv[1];
    try {
        if (fixture == "packaged_text_fallback_weights") {
            return packaged_text_fallback_weights() ? 0 : 1;
        }
        if (fixture == "repeating_linear_gradient_non_square") {
            return repeating_linear_gradient_non_square() ? 0 : 1;
        }
        if (fixture == "outset_shadow_spread_diagonal") {
            return outset_shadow_spread_diagonal() ? 0 : 1;
        }
        if (fixture == "attributed_text_runs_materialization") {
            return attributed_text_runs_materialization() ? 0 : 1;
        }
        if (fixture == "textured_triangle_mesh") {
            return textured_triangle_mesh() ? 0 : 1;
        }
        if (fixture == "scene_version_refusal") {
            return scene_version_refusal() ? 0 : 1;
        }
        if (fixture == "path_fill_rule_and_stroke") {
            return path_fill_rule_and_stroke() ? 0 : 1;
        }
        if (fixture == "path_arc_gradient_and_refusal") {
            return path_arc_gradient_and_refusal() ? 0 : 1;
        }
        if (fixture == "path_damage_bounds_contain_paint") {
            return path_damage_bounds_contain_paint() ? 0 : 1;
        }
        if (fixture == "node_transform_rotation_scale") {
            return node_transform_rotation_scale() ? 0 : 1;
        }
        if (fixture == "group_opacity_layers") {
            return group_opacity_layers() ? 0 : 1;
        }
        if (fixture == "per_corner_radii") {
            return per_corner_radii() ? 0 : 1;
        }
        if (fixture == "radii_overflow_scaling") {
            return radii_overflow_scaling() ? 0 : 1;
        }
        if (fixture == "faded_command_layers") {
            return faded_command_layers() ? 0 : 1;
        }
        if (fixture == "nested_faded_shadow_layers") {
            return nested_faded_shadow_layers() ? 0 : 1;
        }
        if (fixture == "transformed_shadow_and_text") {
            return transformed_shadow_and_text() ? 0 : 1;
        }
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
    return 2;
}
