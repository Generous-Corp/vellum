#include "support/scene_capture_harness.hpp"

#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/render_telemetry.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace {

using namespace vellum::graphics;
namespace harness = vellum::graphics::capture_harness;

constexpr std::uint32_t kWidth = 320;
constexpr std::uint32_t kHeight = 200;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

constexpr RenderCacheBudgets kNoCaches{{0, 0}, {0, 0}, {0, 0}};

std::unique_ptr<SkiaDawnSurface> make_surface(bool cached) {
    std::string error;
    auto surface = SkiaDawnSurface::create(
        {.width = kWidth, .height = kHeight, .scale = 1.0F,
         .native_surface_handle = nullptr, .vsync = false},
        &error);
    if (!surface) throw std::runtime_error("surface creation failed: " + error);
    if (!cached) surface->set_cache_budgets(kNoCaches);
    return surface;
}

std::vector<std::uint8_t> capture(SkiaDawnSurface& surface) {
    std::vector<std::uint8_t> rgba;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::string error;
    if (!surface.capture_rgba(rgba, width, height, &error)) {
        throw std::runtime_error("capture failed: " + error);
    }
    return rgba;
}

void render(SkiaDawnSurface& surface, const Scene& scene) {
    std::string error;
    if (!surface.render(scene, &error)) throw std::runtime_error("render failed: " + error);
}

// The uncached reference: `scene` drawn once on a fresh surface with every
// cache off.
std::vector<std::uint8_t> reference(const Scene& scene) {
    auto surface = make_surface(false);
    render(*surface, scene);
    return capture(*surface);
}

std::string first_difference(const std::vector<std::uint8_t>& expected,
                             const std::vector<std::uint8_t>& actual) {
    if (expected.size() != actual.size()) return "sizes differ";
    for (std::size_t index = 0; index < expected.size(); ++index) {
        if (expected[index] != actual[index]) {
            const auto pixel = index / 4U;
            std::ostringstream out;
            out << "first at (" << pixel % kWidth << ", " << pixel / kWidth << ") channel "
                << index % 4U << ": " << int(expected[index]) << " vs " << int(actual[index]);
            return out.str();
        }
    }
    return {};
}

void expect_identical(const std::vector<std::uint8_t>& expected,
                      const std::vector<std::uint8_t>& actual, const std::string& what) {
    const auto difference = first_difference(expected, actual);
    expect(difference.empty(), what, difference);
}

std::string describe(const CacheStats& stats) {
    std::ostringstream out;
    out << "hits=" << stats.hits << " misses=" << stats.misses
        << " frame_hits=" << stats.frame_hits << " frame_misses=" << stats.frame_misses
        << " evictions=" << stats.evictions << " trimmed=" << stats.trimmed
        << " refused=" << stats.refused << " entries=" << stats.entries
        << " bytes=" << stats.bytes;
    return out.str();
}

SceneNode text_node(const std::string& id, const std::string& text, float x, float y,
                    Color color = Color::hex(0xE5E7EB)) {
    return {
        .id = id,
        .kind = SceneNode::Kind::text,
        .bounds = {x, y, 140.0F, 18.0F},
        .text_runs = {{.text = text,
                       .style = {.font_family = "Inter", .font_size = 14.0F, .color = color}}},
    };
}

SceneNode gradient_node(const std::string& id, Rect bounds) {
    return {
        .id = id,
        .kind = SceneNode::Kind::rectangle,
        .bounds = bounds,
        .fill_gradients = {{.angle_degrees = 90.0F,
                            .stops = {{0.0F, Color::hex(0xF97316)},
                                      {1.0F, Color::hex(0x6366F1)}}}},
        .corner_radius = 6.0F,
    };
}

std::shared_ptr<MeshTexture> checker_texture(std::uint8_t tint) {
    auto texture = std::make_shared<MeshTexture>();
    texture->width = 8;
    texture->height = 8;
    texture->premultiplied_rgba.resize(8U * 8U * 4U);
    for (std::uint32_t y = 0; y < 8; ++y) {
        for (std::uint32_t x = 0; x < 8; ++x) {
            const bool on = ((x + y) % 2U) == 0U;
            auto* texel = &texture->premultiplied_rgba[(y * 8U + x) * 4U];
            texel[0] = on ? tint : 20;
            texel[1] = on ? 40 : 200;
            texel[2] = on ? 220 : static_cast<std::uint8_t>(255 - tint);
            texel[3] = 255;
        }
    }
    return texture;
}

SceneNode textured_quad(const std::string& id, std::shared_ptr<const MeshTexture> texture,
                        float x, float y) {
    auto quad = std::make_shared<TriangleMesh>();
    quad->vertices = {
        {.x = 0.0F, .y = 0.0F, .u = 0.0F, .v = 0.0F},
        {.x = 48.0F, .y = 0.0F, .u = 1.0F, .v = 0.0F},
        {.x = 48.0F, .y = 48.0F, .u = 1.0F, .v = 1.0F},
        {.x = 0.0F, .y = 48.0F, .u = 0.0F, .v = 1.0F},
    };
    quad->indices = {0, 1, 2, 0, 2, 3};
    quad->texture = std::move(texture);
    return {
        .id = id,
        .kind = SceneNode::Kind::mesh,
        .bounds = {x, y, 48.0F, 48.0F},
        .mesh = quad,
    };
}

Scene empty_scene(std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = static_cast<float>(kWidth);
    scene.height = static_cast<float>(kHeight);
    scene.background = Color::hex(0x111827);
    scene.root.id = "root";
    scene.scene_version = version;
    return scene;
}

struct ExpectedPixel final {
    std::uint32_t x = 0;
    std::uint32_t y = 0;
    std::array<float, 3> rgb{};
    std::string where;
};

float encode_srgb(float linear) {
    linear = std::clamp(linear, 0.0F, 1.0F);
    return linear <= 0.0031308F ? linear * 12.92F
                                : 1.055F * std::pow(linear, 1.0F / 2.4F) - 0.055F;
}

float decode_srgb(float encoded) {
    return encoded <= 0.04045F ? encoded / 12.92F
                               : std::pow((encoded + 0.055F) / 1.055F, 2.4F);
}

std::array<float, 3> to_oklab(const std::array<float, 3>& srgb) {
    const float r = decode_srgb(srgb[0]);
    const float g = decode_srgb(srgb[1]);
    const float b = decode_srgb(srgb[2]);
    const float l = std::cbrt(0.4122214708F * r + 0.5363325363F * g + 0.0514459929F * b);
    const float m = std::cbrt(0.2119034982F * r + 0.6806995451F * g + 0.1073969566F * b);
    const float s = std::cbrt(0.0883024619F * r + 0.2817188376F * g + 0.6299787005F * b);
    return {0.2104542553F * l + 0.7936177850F * m - 0.0040720468F * s,
            1.9779984951F * l - 2.4285922050F * m + 0.4505937099F * s,
            0.0259040371F * l + 0.7827717662F * m - 0.8086757660F * s};
}

std::array<float, 3> from_oklab(const std::array<float, 3>& lab) {
    const float l = std::pow(lab[0] + 0.3963377774F * lab[1] + 0.2158037573F * lab[2], 3.0F);
    const float m = std::pow(lab[0] - 0.1055613458F * lab[1] - 0.0638541728F * lab[2], 3.0F);
    const float s = std::pow(lab[0] - 0.0894841775F * lab[1] - 1.2914855480F * lab[2], 3.0F);
    return {encode_srgb(4.0767416621F * l - 3.3077115913F * m + 0.2309699292F * s),
            encode_srgb(-1.2684380046F * l + 2.6097574011F * m - 0.3413193965F * s),
            encode_srgb(-0.0041960863F * l - 0.7034186147F * m + 1.7076147010F * s)};
}

std::array<float, 3> mix(const std::array<float, 3>& from, const std::array<float, 3>& to,
                         float t) {
    return {from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t,
            from[2] + (to[2] - from[2]) * t};
}

constexpr std::array<float, 3> kRed{1.0F, 0.0F, 0.0F};
constexpr std::array<float, 3> kBlue{0.0F, 0.0F, 1.0F};

LinearGradient red_to_blue(GradientKind kind) {
    LinearGradient gradient;
    gradient.kind = kind;
    gradient.stops = {{.position = 0.0F, .color = Color::hex(0xFF0000)},
                      {.position = 1.0F, .color = Color::hex(0x0000FF)}};
    return gradient;
}

void expect_pixels(const std::string& name, Scene scene, const std::vector<ExpectedPixel>& pixels,
                   float tolerance = 6.0F) {
    scene.background = Color::hex(0x000000);
    auto surface = make_surface(false);
    std::string error;
    if (!surface->render(scene, &error)) {
        expect(false, "gradient oracle " + name + " renders", error);
        return;
    }
    const auto rgba = capture(*surface);
    for (const auto& pixel : pixels) {
        const auto offset = (static_cast<std::size_t>(pixel.y) * kWidth + pixel.x) * 4U;
        float worst = 0.0F;
        std::ostringstream detail;
        detail << "at (" << pixel.x << ", " << pixel.y << ") rgb=";
        for (std::size_t channel = 0; channel < 3U; ++channel) {
            const float actual = rgba[offset + channel];
            worst = std::max(worst, std::abs(actual - pixel.rgb[channel] * 255.0F));
            detail << (channel ? "," : "") << actual;
        }
        detail << " expected=" << pixel.rgb[0] * 255.0F << "," << pixel.rgb[1] * 255.0F << ","
               << pixel.rgb[2] * 255.0F;
        expect(worst <= tolerance, "gradient oracle " + name + " " + pixel.where, detail.str());
    }
}

Scene one_gradient_box(Rect bounds, LinearGradient gradient,
                       std::uint32_t version = kSceneVersion) {
    Scene scene = empty_scene(version);
    scene.root.children.push_back({.id = "oracle",
                                   .kind = SceneNode::Kind::rectangle,
                                   .bounds = bounds,
                                   .fill_gradients = {std::move(gradient)}});
    return scene;
}

// Pixels along a left-to-right ramp, each expected at `colour(t)` for the
// ramp position t of the pixel centre.
std::vector<ExpectedPixel> along_ramp(
    Rect bounds, const std::function<std::array<float, 3>(float)>& colour) {
    std::vector<ExpectedPixel> pixels;
    const auto y = static_cast<std::uint32_t>(bounds.y + bounds.height * 0.5F);
    for (const auto& [t, where] : {std::pair{0.05F, "near the start"},
                                   std::pair{0.5F, "at the middle"},
                                   std::pair{0.95F, "near the end"}}) {
        const auto x = static_cast<std::uint32_t>(bounds.x + bounds.width * t);
        const float centre = (static_cast<float>(x) + 0.5F - bounds.x) / bounds.width;
        pixels.push_back({x, y, colour(centre), where});
    }
    return pixels;
}

void gradient_pixel_oracle() {
    const Rect ramp{20.0F, 20.0F, 200.0F, 40.0F};
    const auto srgb_mix = [](float t) { return mix(kRed, kBlue, t); };
    {
        auto gradient = red_to_blue(GradientKind::linear);
        gradient.angle_degrees = 90.0F;
        expect_pixels("linear", one_gradient_box(ramp, gradient), along_ramp(ramp, srgb_mix));
        gradient.repeat_length = 40.0F;
        expect_pixels("linear that does not repeat ignores its repeat length",
                      one_gradient_box(ramp, gradient, 5U), along_ramp(ramp, srgb_mix));
        gradient.repeating = true;
        expect_pixels("linear repeating every 40 pixels", one_gradient_box(ramp, gradient, 5U),
                      along_ramp(ramp, [&](float t) {
                          const float cycle = t * ramp.width / 40.0F;
                          return mix(kRed, kBlue, cycle - std::floor(cycle));
                      }));
    }
    {
        // Before version 8 a gradient blends unpremultiplied colours, so the
        // middle of red to transparent blue is half-transparent purple, drawn
        // over black as a quarter of red and of blue.
        auto gradient = red_to_blue(GradientKind::linear);
        gradient.angle_degrees = 90.0F;
        gradient.stops[1].color = Color::rgba(0.0F, 0.0F, 1.0F, 0.0F);
        expect_pixels("legacy red to transparent blends unpremultiplied",
                      one_gradient_box(ramp, gradient, 5U), along_ramp(ramp, [](float t) {
                          return std::array<float, 3>{(1.0F - t) * (1.0F - t), 0.0F,
                                                      t * (1.0F - t)};
                      }));
    }
    {
        auto gradient = red_to_blue(GradientKind::linear);
        gradient.angle_degrees = 90.0F;
        gradient.interpolation = GradientInterpolation::linear_srgb;
        expect_pixels("linear-srgb interpolation", one_gradient_box(ramp, gradient),
                      along_ramp(ramp, [](float t) {
                          return std::array<float, 3>{encode_srgb(1.0F - t), 0.0F,
                                                      encode_srgb(t)};
                      }));
        gradient.interpolation = GradientInterpolation::oklab;
        expect_pixels("oklab interpolation", one_gradient_box(ramp, gradient),
                      along_ramp(ramp, [](float t) {
                          return from_oklab(mix(to_oklab(kRed), to_oklab(kBlue), t));
                      }));
        // Stops written in linear sRGB keep their meaning whatever the
        // interpolation: linear grey 0.5 is sRGB 0.735.
        gradient.color_space = "linear-srgb";
        gradient.stops = {{.position = 0.0F, .color = Color::rgba(0.5F, 0.5F, 0.5F, 1.0F)},
                          {.position = 1.0F, .color = Color::rgba(0.0F, 0.0F, 0.0F, 1.0F)}};
        for (const auto interpolation :
             {GradientInterpolation::srgb, GradientInterpolation::linear_srgb}) {
            gradient.interpolation = interpolation;
            const bool linear = interpolation == GradientInterpolation::linear_srgb;
            expect_pixels(std::string("linear-srgb stops with ") +
                              (linear ? "linear-srgb" : "srgb") + " interpolation",
                          one_gradient_box(ramp, gradient), along_ramp(ramp, [&](float t) {
                              const float grey = linear ? encode_srgb(0.5F * (1.0F - t))
                                                        : encode_srgb(0.5F) * (1.0F - t);
                              return std::array<float, 3>{grey, grey, grey};
                          }));
        }
    }
    {
        const Rect box{20.0F, 20.0F, 160.0F, 160.0F};
        const float cx = box.x + box.width * 0.5F;
        const float cy = box.y + box.height * 0.5F;
        constexpr float kPi = 3.14159265358979323846F;
        for (const float start : {0.0F, 90.0F, 47.0F}) {
            for (const auto spread :
                 {GradientSpread::pad, GradientSpread::repeat, GradientSpread::reflect}) {
                auto gradient = red_to_blue(GradientKind::conic);
                gradient.angle_degrees = start;
                gradient.spread = spread;
                std::vector<ExpectedPixel> pixels;
                for (const auto& [turn, where] : {std::pair{10.0F, "near the start"},
                                                  std::pair{180.0F, "at the middle"},
                                                  std::pair{350.0F, "near the end"}}) {
                    const float radians = (start + turn) * kPi / 180.0F;
                    const auto x = static_cast<std::uint32_t>(cx + 50.0F * std::sin(radians));
                    const auto y = static_cast<std::uint32_t>(cy - 50.0F * std::cos(radians));
                    const float dx = static_cast<float>(x) + 0.5F - cx;
                    const float dy = static_cast<float>(y) + 0.5F - cy;
                    float degrees = std::atan2(dx, -dy) * 180.0F / kPi - start;
                    degrees = std::fmod(std::fmod(degrees, 360.0F) + 360.0F, 360.0F);
                    pixels.push_back({x, y, mix(kRed, kBlue, degrees / 360.0F), where});
                }
                expect_pixels("conic from " + std::to_string(static_cast<int>(start)) +
                                  " degrees, spread " +
                                  std::to_string(static_cast<int>(spread)),
                              one_gradient_box(box, gradient), pixels);
            }
        }
    }
    {
        // A non-square box: the radii scale by its shorter side, 120.
        const Rect box{20.0F, 40.0F, 240.0F, 120.0F};
        for (const float start_radius : {0.0F, 0.1F}) {
            auto gradient = red_to_blue(GradientKind::two_circle_radial);
            gradient.focal_x = 0.4F;
            gradient.focal_y = 0.5F;
            gradient.start_radius = start_radius;
            gradient.end_radius = 0.5F;
            const float fx = box.x + box.width * gradient.focal_x;
            const float fy = box.y + box.height * gradient.focal_y;
            const float ex = box.x + box.width * 0.5F;
            const float ey = box.y + box.height * 0.5F;
            const float r0 = start_radius * 120.0F;
            const float r1 = 60.0F;
            const auto t_at = [&](float px, float py) {
                const float dx = ex - fx;
                const float dy = ey - fy;
                const float dr = r1 - r0;
                const float qx = px - fx;
                const float qy = py - fy;
                const float a = dx * dx + dy * dy - dr * dr;
                const float b = -2.0F * (qx * dx + qy * dy + r0 * dr);
                const float c = qx * qx + qy * qy - r0 * r0;
                const float root = std::sqrt(std::max(0.0F, b * b - 4.0F * a * c));
                const float t = std::max((-b + root) / (2.0F * a), (-b - root) / (2.0F * a));
                return std::clamp(t, 0.0F, 1.0F);
            };
            std::vector<ExpectedPixel> pixels;
            for (const auto& [x, where] :
                 {std::pair{static_cast<std::uint32_t>(fx), "at the focal point"},
                  std::pair{static_cast<std::uint32_t>(fx - 30.0F), "midway to the left edge"},
                  std::pair{static_cast<std::uint32_t>(ex + 40.0F), "near the right edge"},
                  std::pair{static_cast<std::uint32_t>(box.x + 6.0F), "near the left edge"}}) {
                const auto y = static_cast<std::uint32_t>(fy);
                pixels.push_back({x, y,
                                  mix(kRed, kBlue,
                                      t_at(static_cast<float>(x) + 0.5F,
                                           static_cast<float>(y) + 0.5F)),
                                  where});
            }
            expect_pixels("two-circle radial with start radius " +
                              std::to_string(start_radius),
                          one_gradient_box(box, gradient), pixels);
        }
    }
    {
        const Rect box{20.0F, 40.0F, 240.0F, 120.0F};
        auto gradient = red_to_blue(GradientKind::radial);
        gradient.center_x = 0.5F;
        gradient.center_y = 0.5F;
        gradient.radius_x = 0.5F;
        gradient.radius_y = 0.5F;
        const float cx = box.x + box.width * 0.5F;
        const float cy = box.y + box.height * 0.5F;
        std::vector<ExpectedPixel> pixels;
        for (const auto& [dx, dy, where] :
             {std::tuple{0.0F, 0.0F, "at the centre"}, std::tuple{60.0F, 0.0F, "half way along x"},
              std::tuple{0.0F, 30.0F, "half way along y"},
              std::tuple{110.0F, 0.0F, "near the x edge"}}) {
            const auto x = static_cast<std::uint32_t>(cx + dx);
            const auto y = static_cast<std::uint32_t>(cy + dy);
            const float ux = (static_cast<float>(x) + 0.5F - cx) / 120.0F;
            const float uy = (static_cast<float>(y) + 0.5F - cy) / 60.0F;
            pixels.push_back({x, y,
                              mix(kRed, kBlue, std::min(1.0F, std::hypot(ux, uy))), where});
        }
        expect_pixels("elliptical radial", one_gradient_box(box, gradient), pixels);
    }
    {
        // A straight horizontal line has no box with area; its stroke
        // gradient still runs along it.
        auto line = std::make_shared<PathGeometry>();
        line->verbs = {PathVerb::move, PathVerb::line};
        line->values = {0.0F, 0.0F, 200.0F, 0.0F};
        line->stroke.width = 6.0F;
        line->stroke.color = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
        auto gradient = red_to_blue(GradientKind::linear);
        gradient.angle_degrees = 90.0F;
        line->stroke.gradient = gradient;
        Scene scene = empty_scene();
        scene.root.children.push_back({.id = "line",
                                       .kind = SceneNode::Kind::path,
                                       .bounds = {20.0F, 150.0F, 0.0F, 0.0F},
                                       .path = line});
        expect_pixels("gradient stroke on a horizontal line", scene,
                      along_ramp({20.0F, 147.0F, 200.0F, 6.0F}, srgb_mix));
    }
}

// Three texts, one gradient, one textured mesh, and a faded group of two
// rectangles that opens one layer.
Scene telemetry_scene(const std::shared_ptr<const MeshTexture>& texture, bool all = true) {
    auto scene = empty_scene();
    scene.root.children.push_back(text_node("alpha", "Alpha", 12.0F, 12.0F));
    scene.root.children.push_back(text_node("beta", "Beta", 12.0F, 40.0F));
    if (all) {
        scene.root.children.push_back(text_node("gamma", "Gamma", 12.0F, 68.0F));
        scene.root.children.push_back(gradient_node("ramp", {170.0F, 12.0F, 120.0F, 40.0F}));
    }
    scene.root.children.push_back(textured_quad("quad", texture, 170.0F, 70.0F));
    SceneNode faded{
        .id = "faded",
        .kind = SceneNode::Kind::group,
        .bounds = {20.0F, 120.0F, 120.0F, 60.0F},
        .opacity = 0.5F,
    };
    faded.children.push_back({.id = "faded-a", .kind = SceneNode::Kind::rectangle,
                              .bounds = {0.0F, 0.0F, 80.0F, 40.0F},
                              .fill = Color::hex(0x22C55E)});
    faded.children.push_back({.id = "faded-b", .kind = SceneNode::Kind::rectangle,
                              .bounds = {40.0F, 20.0F, 80.0F, 40.0F},
                              .fill = Color::hex(0xEF4444)});
    scene.root.children.push_back(std::move(faded));
    return scene;
}

void fixtures_with_and_without_caches() {
    for (const auto& [name, scene] : harness::fixtures::all(kWidth, kHeight)) {
        auto cached = make_surface(true);
        render(*cached, scene);
        const auto cold = capture(*cached);
        render(*cached, scene);
        const auto warm = capture(*cached);
        const auto telemetry = cached->telemetry();
        const auto expected = reference(scene);
        expect_identical(expected, cold, name + ": a cold cached frame matches the uncached one");
        expect_identical(expected, warm, name + ": a warm cached frame matches the uncached one");
        const auto commands = make_paint_commands(scene);
        const bool has_text = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return c.kind == PaintCommand::Kind::text;
        });
        const bool has_gradient = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return !c.fill_gradients.empty();
        });
        const bool has_texture = std::any_of(commands.begin(), commands.end(), [](const auto& c) {
            return (c.mesh && c.mesh->texture) ||
                   (c.kind == PaintCommand::Kind::image && c.image_asset);
        });
        expect((!has_text || (telemetry.paragraphs.frame_hits > 0 &&
                              telemetry.paragraphs.frame_misses == 0)) &&
                   (!has_gradient || (telemetry.gradients.frame_hits > 0 &&
                                      telemetry.gradients.frame_misses == 0)) &&
                   (!has_texture || (telemetry.textures.frame_hits > 0 &&
                                     telemetry.textures.frame_misses == 0)),
               name + ": the warm frame is drawn entirely from the caches",
               to_json(telemetry));
    }
}

// A second frame whose inputs differ from the first in one way a key must
// see. The cached surface draws the first frame and then the second; its
// second frame must match the second scene drawn without caches.
void expect_second_frame_exact(const std::string& what, const Scene& first,
                               const Scene& second) {
    auto cached = make_surface(true);
    render(*cached, first);
    render(*cached, second);
    expect_identical(reference(second), capture(*cached), what);
}

void keys_see_every_input() {
    {
        auto first = empty_scene();
        first.root.children.push_back(text_node("label", "Level", 30.0F, 30.0F));
        auto second = first;
        second.root.children[0].text_runs[0].style.color = Color::hex(0x38BDF8);
        expect_second_frame_exact("a text colour change redraws the paragraph", first, second);
        auto faded = first;
        faded.root.children[0].opacity = 0.4F;
        expect_second_frame_exact("a folded text opacity redraws the paragraph", first, faded);
        auto moved = first;
        moved.root.children[0].bounds.x = 140.0F;
        moved.root.children[0].bounds.y = 120.0F;
        auto cached = make_surface(true);
        render(*cached, first);
        render(*cached, moved);
        const auto paragraphs = cached->telemetry().paragraphs;
        expect_identical(reference(moved), capture(*cached),
                         "a moved paragraph is drawn from the cache at its new place");
        expect(paragraphs.frame_hits == 1 && paragraphs.frame_misses == 0,
               "a moved paragraph is a cache hit", describe(paragraphs));
        auto subpixel = moved;
        subpixel.root.children[0].bounds.x += 0.5F;
        subpixel.root.children[0].bounds.y += 0.25F;
        expect_second_frame_exact("a paragraph moved by a fraction of a pixel is exact", moved,
                                  subpixel);
    }
    {
        auto first = empty_scene();
        first.root.children.push_back(gradient_node("ramp", {40.0F, 40.0F, 160.0F, 80.0F}));
        auto faded = first;
        faded.root.children[0].opacity = 0.5F;
        expect_second_frame_exact("a folded gradient opacity rebuilds the shader", first, faded);
        auto moved = first;
        moved.root.children[0].bounds.x += 13.5F;
        expect_second_frame_exact("a moved gradient rebuilds the shader", first, moved);
        auto turned = first;
        turned.root.children[0].fill_gradients[0].angle_degrees = 45.0F;
        expect_second_frame_exact("a turned gradient rebuilds the shader", first, turned);
        auto repeating = first;
        repeating.root.children[0].fill_gradients[0].repeating = true;
        repeating.root.children[0].fill_gradients[0].repeat_length = 30.0F;
        expect_second_frame_exact("a repeating gradient rebuilds the shader", first, repeating);
        const auto mutate_from = [&](const std::string& name, GradientKind kind,
                                     const std::function<void(LinearGradient&)>& mutate) {
            auto base = first;
            base.root.children[0].fill_gradients[0].kind = kind;
            if (kind == GradientKind::radial || kind == GradientKind::two_circle_radial) {
                base.root.children[0].fill_gradients[0].angle_degrees = 180.0F;
            }
            auto changed = base;
            mutate(changed.root.children[0].fill_gradients[0]);
            expect_second_frame_exact("a " + name + " gradient input rebuilds the shader", base,
                                      changed);
        };
        const auto mutate_gradient = [&](const std::string& name,
                                         const std::function<void(LinearGradient&)>& mutate) {
            mutate_from(name, GradientKind::linear, mutate);
        };
        mutate_gradient("kind", [](LinearGradient& gradient) {
            gradient.kind = GradientKind::radial;
            gradient.angle_degrees = 180.0F;
        });
        mutate_gradient("spread", [](LinearGradient& gradient) {
            gradient.spread = GradientSpread::reflect;
            gradient.repeating = false;
        });
        mutate_gradient("interpolation", [](LinearGradient& gradient) {
            gradient.interpolation = GradientInterpolation::oklab;
        });
        mutate_gradient("colour space", [](LinearGradient& gradient) {
            gradient.color_space = "display-p3";
        });
        mutate_from("radial centre", GradientKind::radial, [](LinearGradient& gradient) {
            gradient.center_x = 0.22F;
            gradient.center_y = 0.71F;
        });
        mutate_from("radial radii", GradientKind::radial, [](LinearGradient& gradient) {
            gradient.radius_x = 0.2F;
            gradient.radius_y = 0.7F;
        });
        mutate_from("two-circle focal point", GradientKind::two_circle_radial,
                    [](LinearGradient& gradient) {
                        gradient.focal_x = 0.17F;
                        gradient.focal_y = 0.83F;
                    });
        mutate_from("two-circle radii", GradientKind::two_circle_radial,
                    [](LinearGradient& gradient) {
                        gradient.start_radius = 0.1F;
                        gradient.end_radius = 0.6F;
                    });
        mutate_from("conic angle", GradientKind::conic, [](LinearGradient& gradient) {
            gradient.angle_degrees = 135.0F;
        });
        mutate_gradient("stop colour", [](LinearGradient& gradient) {
            gradient.stops[0].color = Color::hex(0x22C55E);
        });
        mutate_gradient("stop position", [](LinearGradient& gradient) {
            gradient.stops[1].position = 0.6F;
        });
    }
    {
        // A producer may rewrite texels in place behind the scene's const view.
        auto texture = checker_texture(200);
        auto first = empty_scene();
        first.root.children.push_back(textured_quad("quad", texture, 60.0F, 60.0F));
        auto cached = make_surface(true);
        render(*cached, first);
        texture->premultiplied_rgba[0] = 3;
        texture->premultiplied_rgba[1] = 250;
        render(*cached, first);
        const auto textures = cached->telemetry().textures;
        expect_identical(reference(first), capture(*cached),
                         "texels rewritten in place are uploaded again");
        expect(textures.frame_misses == 1 && textures.entries == 2,
               "rewritten texels are a different cache key", describe(textures));

        auto copy = std::make_shared<MeshTexture>(*texture);
        auto second = empty_scene();
        second.root.children.push_back(textured_quad("quad", copy, 90.0F, 30.0F));
        render(*cached, second);
        const auto again = cached->telemetry().textures;
        expect_identical(reference(second), capture(*cached),
                         "an equal texture in a new allocation is drawn from the cache");
        expect(again.frame_hits == 1 && again.frame_misses == 0,
               "an equal texture in a new allocation is a cache hit", describe(again));
    }
}

void partial_redraw_with_and_without_caches() {
    auto texture = checker_texture(120);
    auto scene = empty_scene(kDamageModelSceneVersion);
    scene.root.children.push_back(text_node("title", "Partial", 10.0F, 10.0F));
    scene.root.children.push_back(gradient_node("ramp", {150.0F, 10.0F, 140.0F, 50.0F}));
    scene.root.children.push_back(textured_quad("quad", texture, 40.0F, 90.0F));
    scene.root.children.push_back({.id = "mover", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {250.0F, 150.0F, 30.0F, 30.0F},
                                   .fill = Color::hex(0xF472B6)});
    // The mover's damage crosses the gradient at step 1 and the textured quad
    // at step 2, so those partial frames redraw them from the caches.
    const Rect path[] = {{250.0F, 150.0F, 30.0F, 30.0F},
                         {200.0F, 30.0F, 30.0F, 30.0F},
                         {50.0F, 100.0F, 30.0F, 30.0F},
                         {120.0F, 160.0F, 30.0F, 30.0F}};
    auto cached = make_surface(true);
    auto uncached = make_surface(false);
    std::string error;
    for (int step = 0; step < 4; ++step) {
        scene.root.children[3].bounds = path[step];
        if (step == 3) scene.root.children[0].text_runs[0].text = "Partial, again";
        const bool drawn = cached->render_incremental(scene, &error) &&
                           uncached->render_incremental(scene, &error);
        expect(drawn, "step " + std::to_string(step) + " renders incrementally", error);
        const auto stats = cached->frame_stats();
        const auto telemetry = cached->telemetry();
        if (step > 0) {
            expect(!stats.full_repaint && stats.damage_fraction > 0.0 &&
                       stats.damage_fraction < 1.0,
                   "step " + std::to_string(step) + " repaints only its damage",
                   to_json(telemetry));
        }
        expect(telemetry.frame == stats.frame &&
                   telemetry.full_repaint == stats.full_repaint &&
                   telemetry.full_repaint_reason == to_string(stats.full_repaint_reason) &&
                   telemetry.damage_area_pixels == stats.damage_area_pixels &&
                   telemetry.frame_area_pixels == stats.frame_area_pixels &&
                   telemetry.damage_fraction == stats.damage_fraction &&
                   telemetry.paint_commands_total == stats.paint_commands_total &&
                   telemetry.paint_commands_recorded == stats.paint_commands_recorded,
               "step " + std::to_string(step) + " telemetry carries the frame's damage");
        if (step == 1) {
            expect(telemetry.gradients.frame_hits == 1 && telemetry.gradients.frame_misses == 0,
                   "step 1 redraws the gradient from the cache", describe(telemetry.gradients));
        }
        if (step == 2) {
            expect(telemetry.textures.frame_hits == 1 && telemetry.textures.frame_misses == 0,
                   "step 2 redraws the texture from the cache", describe(telemetry.textures));
        }
        const auto expected = reference(scene);
        expect_identical(expected, capture(*uncached),
                         "step " + std::to_string(step) + " uncached partial redraw is exact");
        expect_identical(expected, capture(*cached),
                         "step " + std::to_string(step) + " cached partial redraw is exact");
    }
}

void budgets_are_enforced() {
    auto scene = empty_scene();
    for (int index = 0; index < 5; ++index) {
        scene.root.children.push_back(text_node("t" + std::to_string(index),
                                                "Label " + std::to_string(index), 10.0F,
                                                10.0F + 30.0F * static_cast<float>(index)));
    }
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.paragraphs = {.max_entries = 2, .max_bytes = 1U << 20U};
        surface->set_cache_budgets(budgets);
        render(*surface, scene);
        const auto paragraphs = surface->telemetry().paragraphs;
        expect(paragraphs.frame_misses == 5 && paragraphs.entries == 2 &&
                   paragraphs.evictions == 3,
               "the paragraph count budget keeps the two most recent", describe(paragraphs));
        expect_identical(reference(scene), capture(*surface),
                         "evicting paragraphs mid-frame draws the same pixels");
        render(*surface, scene);
        const auto again = surface->telemetry().paragraphs;
        expect(again.entries == 2 && again.frame_misses + again.frame_hits == 5,
               "a frame larger than the budget still keeps it", describe(again));
    }

    const std::size_t texture_bytes = 8U * 8U * 4U;
    auto textures = empty_scene();
    textures.root.children.push_back(textured_quad("a", checker_texture(10), 10.0F, 10.0F));
    textures.root.children.push_back(textured_quad("b", checker_texture(90), 80.0F, 10.0F));
    textures.root.children.push_back(textured_quad("c", checker_texture(170), 150.0F, 10.0F));
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.textures = {.max_entries = 64, .max_bytes = texture_bytes * 2U};
        surface->set_cache_budgets(budgets);
        render(*surface, textures);
        const auto telemetry = surface->telemetry();
        expect(telemetry.textures.entries == 2 && telemetry.textures.evictions == 1 &&
                   telemetry.textures.bytes == texture_bytes * 2U &&
                   telemetry.atlas_bytes == texture_bytes * 2U,
               "the texture byte budget evicts the least recently used texture",
               describe(telemetry.textures));
        expect_identical(reference(textures), capture(*surface),
                         "evicting textures draws the same pixels");
    }
    {
        auto surface = make_surface(true);
        auto budgets = surface->cache_budgets();
        budgets.textures = {.max_entries = 64, .max_bytes = texture_bytes - 1U};
        surface->set_cache_budgets(budgets);
        render(*surface, textures);
        const auto stats = surface->telemetry().textures;
        expect(stats.refused == 3 && stats.entries == 0 && stats.bytes == 0,
               "a texture larger than the byte budget is refused", describe(stats));
        expect_identical(reference(textures), capture(*surface),
                         "a refused texture is still drawn");
    }
}

// A trim on a settling partial frame keeps what any frame since the resize
// used, and a capture's internal redraw does not count toward settling.
void settle_counts_presented_frames() {
    auto texture = checker_texture(30);
    auto scene = empty_scene(kDamageModelSceneVersion);
    scene.root.children.push_back(text_node("title", "Settle", 10.0F, 10.0F));
    scene.root.children.push_back(gradient_node("ramp", {150.0F, 10.0F, 140.0F, 50.0F}));
    scene.root.children.push_back({.id = "mover", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {20.0F, 150.0F, 20.0F, 20.0F},
                                   .fill = Color::hex(0xF472B6)});
    auto surface = make_surface(true);
    std::string error;
    expect(surface->render_incremental(scene, &error), "settle scene renders", error);
    expect(surface->resize(kWidth, kHeight, 1.0F, &error), "settle resize", error);
    expect(surface->render_incremental(scene, &error), "the resize frame repaints", error);
    for (int step = 0; step < 2; ++step) {
        scene.root.children[2].bounds.x += 25.0F;
        expect(surface->render_incremental(scene, &error), "a partial settling frame", error);
    }
    auto t = surface->telemetry();
    expect(t.resize_settled && t.last_trim == CacheTrimReason::resize_settled && !t.full_repaint &&
               t.paragraphs.entries == 1 && t.gradients.entries == 1,
           "a partial settling frame keeps what the resize frame drew", to_json(t));

    auto bad = scene;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 120.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    expect(surface->resize(kWidth, kHeight, 1.0F, &error), "second resize", error);
    expect(surface->render_incremental(scene, &error), "the second resize frame", error);
    expect(!surface->render_incremental(bad, &error), "a one-stop gradient fails the frame");
    const auto before = surface->telemetry();
    (void)capture(*surface);
    const auto after = surface->telemetry();
    expect(after.paragraphs.frame_hits == before.paragraphs.frame_hits &&
               after.paragraphs.frame_misses == before.paragraphs.frame_misses &&
               after.gradients.frame_hits == before.gradients.frame_hits &&
               after.gradients.frame_misses == before.gradients.frame_misses &&
               after.layer_count == before.layer_count && after.frame == before.frame,
           "a capture's internal redraw leaves the frame telemetry alone", to_json(after));
    expect(surface->render_incremental(scene, &error), "the frame after the capture", error);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 1,
           "a capture's internal redraw does not advance the settle count", to_json(t));
}

// A frame that fails after uploading a texture never submits the upload, so
// the texture must not stay cached.
void failed_frame_drops_its_uploads() {
    auto texture = checker_texture(140);
    auto good = empty_scene();
    good.root.children.push_back(textured_quad("quad", texture, 60.0F, 60.0F));
    auto bad = good;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 60.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    auto surface = make_surface(true);
    std::string error;
    expect(!surface->render(bad, &error), "a frame with a one-stop gradient fails");
    auto t = surface->telemetry();
    expect(t.textures.entries == 0 && t.atlas_bytes == 0,
           "a failed frame keeps none of its uploads", describe(t.textures));
    render(*surface, good);
    t = surface->telemetry();
    expect(t.textures.frame_misses == 1 && t.textures.entries == 1,
           "the next frame uploads the texture again", describe(t.textures));
    expect_identical(reference(good), capture(*surface),
                     "the frame after a failed one draws the texture exactly");
}

// A frame that fails partway leaves the telemetry describing the last frame
// that was submitted: its frame number, layers and per-frame cache counters.
// The failed attempt's lookups still count toward the cumulative totals.
void failed_frame_keeps_the_last_frame_telemetry() {
    auto texture = checker_texture(200);
    const auto good = telemetry_scene(texture);
    auto bad = good;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 150.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    auto surface = make_surface(true);
    render(*surface, good);
    const auto before = surface->telemetry();
    std::string error;
    expect(!surface->render(bad, &error), "a frame with a one-stop gradient fails");
    const auto after = surface->telemetry();
    const auto same_frame = [](const CacheStats& a, const CacheStats& b) {
        return a.frame_hits == b.frame_hits && a.frame_misses == b.frame_misses;
    };
    expect(after.frame == before.frame && after.layer_count == before.layer_count &&
               after.full_repaint_reason == before.full_repaint_reason &&
               same_frame(after.paragraphs, before.paragraphs) &&
               same_frame(after.gradients, before.gradients) &&
               same_frame(after.textures, before.textures),
           "a failed frame leaves the last submitted frame's telemetry",
           to_json(before) + " then " + to_json(after));
    expect(before.paragraphs.frame_misses == 3 && after.paragraphs.frame_hits == 0,
           "the snapshot is the cold first frame", to_json(after));
    expect(after.paragraphs.hits > before.paragraphs.hits &&
               after.paragraphs.hits + after.paragraphs.misses >
                   before.paragraphs.hits + before.paragraphs.misses,
           "the failed attempt's lookups stay in the cumulative totals", to_json(after));
    render(*surface, good);
    const auto next = surface->telemetry();
    expect(next.frame == before.frame + 1U && next.paragraphs.frame_hits == 3 &&
               next.paragraphs.frame_misses == 0 && next.layer_count == before.layer_count,
           "the frame after a failed one reports itself", to_json(next));
}

// After a failed frame a capture redraws the last submitted scene in full.
// That redraw is not a frame, so it must leave the counters of the partial
// frame that was last submitted, which looked up no paragraph at all.
void capture_redraw_keeps_the_partial_frame_counters() {
    auto scene = empty_scene(kDamageModelSceneVersion);
    scene.root.children.push_back(text_node("title", "Redraw", 10.0F, 10.0F));
    scene.root.children.push_back(gradient_node("ramp", {150.0F, 10.0F, 140.0F, 50.0F}));
    scene.root.children.push_back({.id = "mover", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {20.0F, 150.0F, 20.0F, 20.0F},
                                   .fill = Color::hex(0xF472B6)});
    auto surface = make_surface(true);
    std::string error;
    expect(surface->render_incremental(scene, &error), "redraw scene renders", error);
    scene.root.children[2].bounds.x += 25.0F;
    expect(surface->render_incremental(scene, &error), "a partial frame", error);
    auto bad = scene;
    bad.root.children.push_back({.id = "broken", .kind = SceneNode::Kind::rectangle,
                                 .bounds = {200.0F, 120.0F, 40.0F, 40.0F},
                                 .fill_gradients = {{.stops = {{0.0F, Color::hex(0xFFFFFF)}}}}});
    expect(!surface->render_incremental(bad, &error), "a one-stop gradient fails the frame");
    const auto before = surface->telemetry();
    expect(!before.full_repaint && before.paragraphs.frame_hits == 0 &&
               before.paragraphs.frame_misses == 0 && before.gradients.frame_hits == 0 &&
               before.gradients.frame_misses == 0,
           "the last submitted frame is partial and looked up nothing", to_json(before));
    (void)capture(*surface);
    const auto after = surface->telemetry();
    expect(after.paragraphs.frame_hits == 0 && after.paragraphs.frame_misses == 0 &&
               after.gradients.frame_hits == 0 && after.gradients.frame_misses == 0 &&
               after.frame == before.frame && after.layer_count == before.layer_count,
           "the capture's full redraw leaves the partial frame's counters", to_json(after));
    expect(after.paragraphs.hits > before.paragraphs.hits,
           "the capture's redraw lookups stay in the cumulative totals", to_json(after));
}

void scripted_telemetry() {
    auto texture = checker_texture(60);
    const auto full = telemetry_scene(texture);
    const auto reduced = telemetry_scene(texture, false);
    const auto commands = make_paint_commands(full);
    const auto layers = static_cast<std::size_t>(std::count_if(
        commands.begin(), commands.end(),
        [](const auto& command) { return command.kind == PaintCommand::Kind::begin_layer; }));
    const std::size_t texture_bytes = 8U * 8U * 4U;

    auto surface = make_surface(true);
    render(*surface, full);
    auto t = surface->telemetry();
    expect(t.frame == 1 && t.full_repaint && t.full_repaint_reason == "full_render" &&
               t.damage_fraction == 1.0 &&
               t.damage_area_pixels == static_cast<std::int64_t>(kWidth) * kHeight,
           "frame 1 is a full repaint of the whole frame", to_json(t));
    // The faded group is cached as an image from its first frame, so the frame
    // composites it instead of opening an offscreen layer; `layer_count`
    // counts only the layers a frame opens.
    const auto layer_stats = surface->layer_cache_stats();
    expect(layers == 1 && layer_stats.layers == 1 && layer_stats.builds == 1 &&
               t.layer_count == 0,
           "frame 1 caches the faded group's layer as an image", to_json(t));

    // With the layer cache off the same frame opens the layer itself, and
    // draws the same pixels.
    auto direct = make_surface(true);
    direct->set_layer_cache_policy({.enabled = false});
    render(*direct, full);
    t = direct->telemetry();
    expect(direct->layer_cache_stats().layers == 0 && t.layer_count == layers,
           "without the layer cache frame 1 opens the faded group's layer", to_json(t));
    // A separate surface keeps the capture's redraw out of `surface`'s totals.
    auto sibling = make_surface(true);
    render(*sibling, full);
    expect(capture(*direct) == capture(*sibling),
           "the cached faded group draws the pixels the opened layer draws");
    t = surface->telemetry();
    expect(t.paragraphs.frame_misses == 3 && t.paragraphs.frame_hits == 0 &&
               t.paragraphs.entries == 3 && t.gradients.frame_misses == 1 &&
               t.gradients.entries == 1 && t.textures.frame_misses == 1 &&
               t.textures.entries == 1 && t.atlas_bytes == texture_bytes,
           "frame 1 misses every cache and fills it", to_json(t));
    expect(t.trims == 0 && t.last_trim == CacheTrimReason::none && !t.resize_settled,
           "frame 1 trims nothing", to_json(t));

    render(*surface, full);
    t = surface->telemetry();
    expect(t.frame == 2 && t.paragraphs.frame_hits == 3 && t.paragraphs.frame_misses == 0 &&
               t.gradients.frame_hits == 1 && t.textures.frame_hits == 1 &&
               hit_rate(t.paragraphs) == 0.5 && frame_hit_rate(t.paragraphs) == 1.0,
           "frame 2 is drawn from the caches", to_json(t));

    std::string error;
    expect(surface->finish(&error), "frame 2 finishes on the GPU", error);
    const auto stats = surface->frame_stats();
    t = surface->telemetry();
    expect(t.gpu_time_available == stats.gpu_time_available &&
               t.gpu_milliseconds == stats.gpu_milliseconds &&
               (!t.gpu_time_available || t.gpu_milliseconds > 0.0),
           "telemetry carries Graphite's GPU time", to_json(t));

    expect(surface->resize(kWidth + 10U, kHeight + 10U, 1.0F, &error), "resize", error);
    render(*surface, reduced);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 0, "the frame after a resize has not settled",
           to_json(t));
    render(*surface, reduced);
    t = surface->telemetry();
    expect(!t.resize_settled && t.trims == 0, "one stable frame has not settled", to_json(t));
    render(*surface, reduced);
    t = surface->telemetry();
    expect(t.resize_settled && t.trims == 1 && t.last_trim == CacheTrimReason::resize_settled,
           "the second stable frame settles the resize and trims", to_json(t));
    expect(t.paragraphs.entries == 2 && t.paragraphs.trimmed == 1 && t.gradients.entries == 0 &&
               t.gradients.trimmed == 1 && t.textures.entries == 1 &&
               t.atlas_bytes == texture_bytes,
           "the settle trim drops what no frame drew since the resize", to_json(t));
    render(*surface, full);
    t = surface->telemetry();
    expect(t.trims == 1 && t.paragraphs.frame_misses == 1 && t.gradients.frame_misses == 1,
           "a settled surface trims once per resize", to_json(t));

    auto budgets = surface->cache_budgets();
    budgets.paragraphs.max_entries = 1;
    surface->set_cache_budgets(budgets);
    t = surface->telemetry();
    expect(t.trims == 2 && t.last_trim == CacheTrimReason::budget_changed &&
               t.paragraphs.entries == 1,
           "lowering a budget trims to it", to_json(t));

    surface->simulate_device_loss();
    expect(!surface->finish(&error), "finish after a device loss fails");
    t = surface->telemetry();
    expect(t.trims == 3 && t.last_trim == CacheTrimReason::device_lost &&
               t.paragraphs.entries == 0 && t.gradients.entries == 0 &&
               t.textures.entries == 0 && t.atlas_bytes == 0 && t.textures.bytes == 0 &&
               t.device_losses == 1,
           "a device loss drops every cache", to_json(t));
    expect(!surface->finish(&error) && surface->telemetry().trims == 3,
           "a loss seen twice trims once", to_json(surface->telemetry()));
    render(*surface, full);
    t = surface->telemetry();
    expect(t.trims == 3 && t.device_recoveries == 1 && t.paragraphs.entries > 0 &&
               t.full_repaint,
           "the next frame recovers and refills the caches", to_json(t));
    // The recovered device starts a new lifetime, so its loss trims again.
    surface->simulate_device_loss();
    expect(!surface->finish(&error) && surface->telemetry().trims == 4 &&
               surface->telemetry().paragraphs.entries == 0 &&
               surface->telemetry().device_losses == 2,
           "a loss of the recovered device drops its caches", to_json(surface->telemetry()));
}

}  // namespace

int main() {
    try {
        std::string error;
        if (!harness::prepare_device(&error)) {
            std::cerr << "FAIL Dawn bootstrap: " << error << '\n';
            return 1;
        }
        fixtures_with_and_without_caches();
        gradient_pixel_oracle();
        keys_see_every_input();
        partial_redraw_with_and_without_caches();
        budgets_are_enforced();
        scripted_telemetry();
        settle_counts_presented_frames();
        failed_frame_drops_its_uploads();
        failed_frame_keeps_the_last_frame_telemetry();
        capture_redraw_keeps_the_partial_frame_counters();
    } catch (const std::exception& exception) {
        std::cout << "FAIL " << exception.what() << '\n';
        return 1;
    }
    std::cout << "render cache gpu " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
