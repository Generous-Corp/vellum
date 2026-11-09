#include "scene_capture_harness.hpp"

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <sstream>
#include <system_error>
#include <utility>

namespace vellum::graphics::capture_harness {
namespace {

using Clock = std::chrono::steady_clock;

constexpr std::array<char, 8> golden_magic{'V', 'L', 'M', 'R', 'G', 'B', 'A', '1'};

std::chrono::microseconds elapsed(Clock::time_point from, Clock::time_point to) {
    return std::chrono::duration_cast<std::chrono::microseconds>(to - from);
}

void set_error(std::string* error, std::string message) {
    if (error != nullptr) *error = std::move(message);
}

Verdict pass(std::string message = {}) { return {true, std::move(message)}; }
Verdict fail(std::string message) { return {false, std::move(message)}; }

std::string describe(const PixelComparison& comparison) {
    std::ostringstream out;
    out << comparison.differing_pixels << " of " << comparison.pixel_count
        << " pixels differ, max channel delta " << comparison.max_channel_delta;
    if (comparison.first) {
        const auto& first = *comparison.first;
        out << "; first at (" << first.x << ", " << first.y << ") expected "
            << int(first.expected[0]) << ',' << int(first.expected[1]) << ','
            << int(first.expected[2]) << ',' << int(first.expected[3]) << " got "
            << int(first.actual[0]) << ',' << int(first.actual[1]) << ','
            << int(first.actual[2]) << ',' << int(first.actual[3]);
    }
    return out.str();
}

void put_u32(std::ostream& out, std::uint32_t value) {
    for (int shift = 0; shift < 32; shift += 8) {
        out.put(static_cast<char>((value >> shift) & 0xFFU));
    }
}

bool get_u32(std::istream& in, std::uint32_t& value) {
    value = 0;
    for (int shift = 0; shift < 32; shift += 8) {
        const auto byte = in.get();
        if (byte == std::char_traits<char>::eof()) return false;
        value |= static_cast<std::uint32_t>(byte & 0xFF) << shift;
    }
    return true;
}

SceneNode root_node(float width, float height) {
    return {
        .id = "root",
        .kind = SceneNode::Kind::group,
        .bounds = {0.0F, 0.0F, width, height},
    };
}

void append_rectangles(SceneNode& root, Rect area, const std::string& prefix) {
    const float inset = std::min(area.width, area.height) * 0.12F;
    root.children.push_back({
        .id = prefix + "gradient-shadow",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {area.x + inset, area.y + inset, area.width * 0.55F,
                   area.height - 2.0F * inset},
        .fill_gradients = {{
            .angle_degrees = 135.0F,
            .stops = {
                {.position = 0.0F, .color = Color::hex(0x6366F1)},
                {.position = 0.5F, .color = Color::hex(0x14B8A6)},
                {.position = 1.0F, .color = Color::hex(0xF59E0B)},
            },
        }},
        .box_shadows = {{
            .offset_x = 4.0F,
            .offset_y = 6.0F,
            .blur_radius = 12.0F,
            .spread_radius = 2.0F,
            .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.45F),
        }},
        .corner_radius = inset * 0.6F,
    });
    root.children.push_back({
        .id = prefix + "repeating-gradient",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {area.x + area.width * 0.55F + 2.0F * inset, area.y + inset,
                   area.width * 0.45F - 3.0F * inset, area.height - 2.0F * inset},
        .fill_gradients = {{
            .angle_degrees = 90.0F,
            .repeating = true,
            .repeat_length = 16.0F,
            .stops = {
                {.position = 0.0F, .color = Color::hex(0xEF4444)},
                {.position = 1.0F, .color = Color::hex(0x3B82F6)},
            },
        }},
        .corner_radius = 6.0F,
    });
}

void append_text(SceneNode& root, Rect area, const std::string& prefix) {
    const float size = std::max(12.0F, area.height / 6.0F);
    root.children.push_back({
        .id = prefix + "headline",
        .kind = SceneNode::Kind::text,
        .bounds = {area.x + 8.0F, area.y + 8.0F, area.width - 16.0F, size * 1.6F},
        .text_runs = {{
            .text = "Capture ",
            .style = {.font_family = "Jost", .font_weight = 600,
                      .font_size = size, .color = Color::hex(0xF9FAFB)},
        }, {
            .text = "harness",
            .style = {.font_family = "Jost", .font_weight = 400,
                      .font_size = size, .letter_spacing = 1.0F,
                      .color = Color::hex(0x2DD4BF), .underline = true},
        }},
    });
    root.children.push_back({
        .id = prefix + "body",
        .kind = SceneNode::Kind::text,
        .bounds = {area.x + 8.0F, area.y + 8.0F + size * 1.8F, area.width - 16.0F,
                   area.height - 16.0F - size * 1.8F},
        .text_runs = {{
            .text = "The quick brown fox jumps over the lazy dog 0123456789",
            .style = {.font_family = "Inter", .font_weight = 400,
                      .font_size = size * 0.6F, .color = Color::hex(0xFDE68A)},
        }},
    });
}

void append_mesh(SceneNode& root, Rect area, const std::string& prefix) {
    auto fan = std::make_shared<TriangleMesh>();
    const float cx = area.x + area.width * 0.35F;
    const float cy = area.y + area.height * 0.5F;
    const float radius = std::min(area.width, area.height) * 0.4F;
    constexpr int segments = 12;
    constexpr std::array<std::uint32_t, 3> palette{0xFF0000FFU, 0x00FF00FFU, 0x0000FFFFU};
    fan->vertices.push_back({.x = cx, .y = cy, .premultiplied_rgba = 0xFFFFFFFFU});
    for (int index = 0; index <= segments; ++index) {
        const float angle = static_cast<float>(index) * 6.2831853F / segments;
        fan->vertices.push_back({
            .x = cx + radius * std::cos(angle),
            .y = cy + radius * std::sin(angle),
            .premultiplied_rgba = palette[static_cast<std::size_t>(index) % palette.size()],
        });
    }
    for (std::uint32_t index = 1; index <= segments; ++index) {
        fan->indices.insert(fan->indices.end(), {0U, index, index + 1U});
    }
    root.children.push_back({
        .id = prefix + "colour-fan",
        .kind = SceneNode::Kind::mesh,
        .mesh = fan,
    });

    auto texture = std::make_shared<MeshTexture>(MeshTexture{
        .width = 2,
        .height = 2,
        .premultiplied_rgba = {255, 255, 0, 255, 255, 0, 255, 255,
                               0, 255, 255, 255, 255, 255, 255, 255},
    });
    auto quad = std::make_shared<TriangleMesh>();
    const float x0 = area.x + area.width * 0.72F;
    const float x1 = area.x + area.width * 0.95F;
    const float y0 = area.y + area.height * 0.15F;
    const float y1 = area.y + area.height * 0.85F;
    quad->vertices = {
        {.x = x0, .y = y0, .u = 0.0F, .v = 0.0F},
        {.x = x1, .y = y0, .u = 1.0F, .v = 0.0F},
        {.x = x1, .y = y1, .u = 1.0F, .v = 1.0F},
        {.x = x0, .y = y1, .u = 0.0F, .v = 1.0F},
    };
    quad->indices = {0, 1, 2, 0, 2, 3};
    quad->texture = texture;
    quad->clip_enabled = true;
    quad->clip = {x0, y0, x1 - x0, (y1 - y0) * 0.75F};
    root.children.push_back({
        .id = prefix + "textured-quad",
        .kind = SceneNode::Kind::mesh,
        .mesh = quad,
    });
}

}  // namespace

bool prepare_device(std::string* error) {
    return vellum::app_host::register_native_dawn_bootstrap(error);
}

CaptureResult render_offscreen(
    const Scene& scene, std::uint32_t width, std::uint32_t height, float scale) {
    CaptureResult result;
    if (!prepare_device(&result.error)) return result;
    const auto start = Clock::now();
    auto surface = SkiaDawnSurface::create(
        {.width = width, .height = height, .scale = scale,
         .native_surface_handle = nullptr, .vsync = false},
        &result.error);
    const auto created = Clock::now();
    result.surface_creation = elapsed(start, created);
    if (!surface) {
        if (result.error.empty()) result.error = "offscreen surface creation failed";
        return result;
    }
    result.evidence = surface->evidence();
    if (!result.evidence.available || result.evidence.native_surface ||
        result.evidence.fallback) {
        result.error = "offscreen surface evidence is not a GPU offscreen target";
        return result;
    }
    if (!surface->render(scene, &result.error)) return result;
    const auto rendered = Clock::now();
    result.render = elapsed(created, rendered);
    if (!surface->capture_rgba(
            result.rgba, result.pixel_width, result.pixel_height, &result.error)) {
        return result;
    }
    result.capture = elapsed(rendered, Clock::now());
    const auto expected_width =
        static_cast<std::uint32_t>(std::lround(static_cast<double>(width) * scale));
    const auto expected_height =
        static_cast<std::uint32_t>(std::lround(static_cast<double>(height) * scale));
    if (result.pixel_width != expected_width || result.pixel_height != expected_height) {
        std::ostringstream out;
        out << "capture is " << result.pixel_width << 'x' << result.pixel_height
            << ", expected " << expected_width << 'x' << expected_height;
        result.error = out.str();
        return result;
    }
    result.stats = analyze_capture_rgba(result.rgba, result.pixel_width, result.pixel_height);
    result.ok = true;
    return result;
}

PixelComparison compare_pixels(
    std::span<const std::uint8_t> expected, std::uint32_t expected_width,
    std::uint32_t expected_height, std::span<const std::uint8_t> actual,
    std::uint32_t actual_width, std::uint32_t actual_height, int max_channel_delta) {
    PixelComparison comparison;
    const auto bytes = checked_image_byte_count(expected_width, expected_height, 4U);
    comparison.same_size = expected_width == actual_width &&
                           expected_height == actual_height && bytes &&
                           expected.size() == *bytes && actual.size() == *bytes;
    if (!comparison.same_size) return comparison;
    comparison.pixel_count = *bytes / 4U;
    for (std::size_t index = 0; index < comparison.pixel_count; ++index) {
        const auto offset = index * 4U;
        int delta = 0;
        for (std::size_t channel = 0; channel < 4U; ++channel) {
            delta = std::max(delta, std::abs(int(expected[offset + channel]) -
                                             int(actual[offset + channel])));
        }
        comparison.max_channel_delta = std::max(comparison.max_channel_delta, delta);
        if (delta <= max_channel_delta) continue;
        ++comparison.differing_pixels;
        if (!comparison.first) {
            PixelDifference first{
                .x = static_cast<std::uint32_t>(index % expected_width),
                .y = static_cast<std::uint32_t>(index / expected_width),
            };
            std::copy_n(expected.begin() + static_cast<std::ptrdiff_t>(offset), 4,
                        first.expected);
            std::copy_n(actual.begin() + static_cast<std::ptrdiff_t>(offset), 4,
                        first.actual);
            comparison.first = first;
        }
    }
    return comparison;
}

Verdict assert_identical(const CaptureResult& expected, const CaptureResult& actual) {
    if (!expected.ok || !actual.ok) {
        return fail("capture failed: " + (expected.ok ? actual.error : expected.error));
    }
    const auto comparison = compare_pixels(
        expected.rgba, expected.pixel_width, expected.pixel_height, actual.rgba,
        actual.pixel_width, actual.pixel_height);
    if (!comparison.same_size) return fail("captures differ in size");
    if (comparison.differing_pixels != 0) {
        return fail("captures are not byte-identical: " + describe(comparison));
    }
    return pass();
}

Verdict assert_deterministic(
    const Scene& scene, std::uint32_t width, std::uint32_t height, float scale,
    CaptureResult* first, CaptureResult* second) {
    auto one = render_offscreen(scene, width, height, scale);
    auto two = render_offscreen(scene, width, height, scale);
    auto verdict = assert_identical(one, two);
    if (first != nullptr) *first = std::move(one);
    if (second != nullptr) *second = std::move(two);
    return verdict;
}

Verdict assert_content_floor(const CaptureResult& result) {
    if (!result.ok) return fail("capture failed: " + result.error);
    const auto& stats = result.stats;
    std::ostringstream out;
    out << "pixels=" << stats.pixel_count << " unique_colors=" << stats.unique_colors
        << " opaque=" << stats.opaque_pixels
        << " non_background=" << stats.non_background_pixels
        << " luminance_stddev=" << stats.luminance_standard_deviation;
    if (!passes_content_floor(stats)) return fail("content floor not met: " + out.str());
    return pass(out.str());
}

bool update_goldens_requested() {
    const char* value = std::getenv("VELLUM_UPDATE_GOLDENS");
    return value != nullptr && std::string_view(value) == "1";
}

std::filesystem::path default_golden_directory() {
    return VELLUM_CAPTURE_GOLDEN_DIR;
}

Verdict compare_to_golden(
    const CaptureResult& result, const std::filesystem::path& golden,
    int max_channel_delta, double max_differing_fraction, GoldenMode mode) {
    if (!result.ok) return fail("capture failed: " + result.error);
    const auto path = golden.is_absolute() ? golden : default_golden_directory() / golden;
    const bool environment_update =
        mode == GoldenMode::from_environment && update_goldens_requested();
    if (environment_update) {
        const char* ci = std::getenv("CI");
        if (ci != nullptr && *ci != '\0') {
            return fail("VELLUM_UPDATE_GOLDENS=1 is refused when CI is set");
        }
    }
    const bool update = mode == GoldenMode::update || environment_update;
    std::string error;
    if (update) {
        if (!write_raw_rgba(path, result.pixel_width, result.pixel_height, result.rgba,
                            &error)) {
            return fail(error);
        }
        return pass("golden updated: " + path.string());
    }
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> rgba;
    if (!read_raw_rgba(path, width, height, rgba, &error)) {
        return fail(error + "; rerun with VELLUM_UPDATE_GOLDENS=1 to record it");
    }
    const auto comparison = compare_pixels(
        rgba, width, height, result.rgba, result.pixel_width, result.pixel_height,
        max_channel_delta);
    if (!comparison.same_size) {
        std::ostringstream out;
        out << "golden " << path.string() << " is " << width << 'x' << height
            << ", capture is " << result.pixel_width << 'x' << result.pixel_height;
        return fail(out.str());
    }
    const double fraction = static_cast<double>(comparison.differing_pixels) /
                            static_cast<double>(comparison.pixel_count);
    if (fraction > max_differing_fraction) {
        return fail("golden " + path.string() + " mismatch: " + describe(comparison));
    }
    return pass(describe(comparison));
}

bool write_raw_rgba(
    const std::filesystem::path& path, std::uint32_t width, std::uint32_t height,
    std::span<const std::uint8_t> rgba, std::string* error) {
    const auto bytes = checked_image_byte_count(width, height, 4U);
    if (!bytes || rgba.size() != *bytes) {
        set_error(error, "RGBA size does not match " + std::to_string(width) + 'x' +
                             std::to_string(height));
        return false;
    }
    std::error_code ignored;
    if (path.has_parent_path()) std::filesystem::create_directories(path.parent_path(), ignored);
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(golden_magic.data(), golden_magic.size());
    put_u32(out, width);
    put_u32(out, height);
    out.write(reinterpret_cast<const char*>(rgba.data()),
              static_cast<std::streamsize>(rgba.size()));
    if (!out) {
        set_error(error, "cannot write golden " + path.string());
        return false;
    }
    return true;
}

bool read_raw_rgba(
    const std::filesystem::path& path, std::uint32_t& width, std::uint32_t& height,
    std::vector<std::uint8_t>& rgba, std::string* error) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        set_error(error, "golden " + path.string() + " is missing");
        return false;
    }
    std::array<char, 8> magic{};
    in.read(magic.data(), magic.size());
    if (!in || magic != golden_magic || !get_u32(in, width) || !get_u32(in, height)) {
        set_error(error, "golden " + path.string() + " has no raw RGBA header");
        return false;
    }
    const auto bytes = checked_image_byte_count(width, height, 4U);
    if (!bytes) {
        set_error(error, "golden " + path.string() + " has invalid dimensions");
        return false;
    }
    rgba.resize(*bytes);
    in.read(reinterpret_cast<char*>(rgba.data()), static_cast<std::streamsize>(*bytes));
    if (!in || in.peek() != std::char_traits<char>::eof()) {
        set_error(error, "golden " + path.string() + " has the wrong payload size");
        return false;
    }
    return true;
}

namespace fixtures {

Scene empty(float width, float height) {
    return {.width = width, .height = height, .background = Color::hex(0x111827),
            .root = root_node(width, height)};
}

Scene gradient_shadow_rectangle(float width, float height) {
    auto scene = empty(width, height);
    append_rectangles(scene.root, {0.0F, 0.0F, width, height}, "");
    return scene;
}

Scene text(float width, float height) {
    auto scene = empty(width, height);
    append_text(scene.root, {0.0F, 0.0F, width, height}, "");
    return scene;
}

Scene mesh(float width, float height) {
    auto scene = empty(width, height);
    append_mesh(scene.root, {0.0F, 0.0F, width, height}, "");
    return scene;
}

Scene compositing(float width, float height) {
    auto scene = empty(width, height);
    const float cell = std::min(width, height) * 0.25F;
    const auto rectangle = [](std::string id, Rect bounds, std::uint32_t color) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = bounds,
            .fill = Color::hex(color),
        };
    };
    // A quarter turn about the bar's centre.
    auto turned = rectangle("turned", {cell, cell, cell * 2.0F, cell * 0.5F}, 0xF97316);
    turned.transform = concat(translation(cell, cell * 0.25F),
        concat(Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F},
               translation(-cell, -cell * 0.25F)));
    scene.root.children.push_back(std::move(turned));
    // Two overlapping members of a half-opacity group, composited first.
    SceneNode faded{
        .id = "faded",
        .kind = SceneNode::Kind::group,
        .bounds = {width - cell * 2.5F, cell * 0.5F, cell * 2.0F, cell * 2.0F},
        .opacity = 0.5F,
    };
    faded.children.push_back(rectangle("faded/a", {0.0F, 0.0F, cell * 1.4F, cell}, 0x38BDF8));
    faded.children.push_back(
        rectangle("faded/b", {cell * 0.6F, cell * 0.5F, cell * 1.4F, cell}, 0xA3E635));
    scene.root.children.push_back(std::move(faded));
    // Per-corner radii, scaled about the node's origin, at a folded opacity.
    auto corners = rectangle(
        "corners", {cell * 0.5F, height - cell * 1.5F, cell * 2.0F, cell}, 0xE879F9);
    corners.corner_radii = CornerRadii{0.0F, cell * 0.5F, cell * 0.1F, cell * 0.4F};
    corners.transform = Affine2D{.a = 1.25F, .d = 1.0F};
    corners.opacity = 0.75F;
    scene.root.children.push_back(std::move(corners));
    return scene;
}

Scene combined(float width, float height) {
    auto scene = empty(width, height);
    const float half_width = width * 0.5F;
    const float half_height = height * 0.5F;
    append_rectangles(scene.root, {0.0F, 0.0F, half_width, half_height}, "rect-");
    append_mesh(scene.root, {half_width, 0.0F, half_width, half_height}, "mesh-");
    append_text(scene.root, {0.0F, half_height, width, half_height}, "text-");
    return scene;
}

std::vector<NamedScene> all(float width, float height) {
    return {
        {"gradient_shadow_rectangle", gradient_shadow_rectangle(width, height)},
        {"text", text(width, height)},
        {"mesh", mesh(width, height)},
        {"compositing", compositing(width, height)},
        {"combined", combined(width, height)},
    };
}

}  // namespace fixtures

}  // namespace vellum::graphics::capture_harness
