// HeadlessSurface on a real Dawn device: a deterministic clear-only frame, a
// paint callback encoded to PNG, and file-backed images and SVG documents
// drawn on the Graphite path. The image and SVG cases assert the asset's own
// ink in the readback, because a draw call can return true while recording
// nothing. Offscreen only. The executable exits 77 (skipped) when there is no
// adapter.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/render/headless_surface.hpp>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>
#include <vector>

namespace {

using vellum::render::HeadlessSurface;

constexpr uint32_t kW = 256;
constexpr uint32_t kH = 128;
// A sentinel background distinct enough that counting non-background pixels
// does not mistake noise for the painted content.
constexpr uint8_t kBgR = 20, kBgG = 40, kBgB = 80;

HeadlessSurface::Config config() {
    HeadlessSurface::Config cfg;
    cfg.width = kW;
    cfg.height = kH;
    cfg.clear_r = kBgR;
    cfg.clear_g = kBgG;
    cfg.clear_b = kBgB;
    cfg.clear_a = 255;
    return cfg;
}

// Pixels within `tol` (sum of per-channel differences) of a target colour.
uint32_t count_near_color(const HeadlessSurface::Rgba& rgba, uint8_t tr, uint8_t tg, uint8_t tb,
                          int tol = 24) {
    const size_t n = rgba.pixel_count();
    if (n == 0 || rgba.pixels.size() < n * 4u)
        return 0;
    uint32_t count = 0;
    for (size_t i = 0; i < n; ++i) {
        const int dr = std::abs(static_cast<int>(rgba.pixels[i * 4 + 0]) - tr);
        const int dg = std::abs(static_cast<int>(rgba.pixels[i * 4 + 1]) - tg);
        const int db = std::abs(static_cast<int>(rgba.pixels[i * 4 + 2]) - tb);
        if (dr + dg + db <= tol)
            ++count;
    }
    return count;
}

uint32_t count_non_background(const HeadlessSurface::Rgba& rgba) {
    const size_t n = rgba.pixel_count();
    if (n == 0 || rgba.pixels.size() < n * 4u)
        return 0;
    uint32_t count = 0;
    for (size_t i = 0; i < n; ++i) {
        const int dr = std::abs(static_cast<int>(rgba.pixels[i * 4 + 0]) - kBgR);
        const int dg = std::abs(static_cast<int>(rgba.pixels[i * 4 + 1]) - kBgG);
        const int db = std::abs(static_cast<int>(rgba.pixels[i * 4 + 2]) - kBgB);
        if (dr + dg + db > 24)
            ++count;
    }
    return count;
}

HeadlessSurface::Rgba solid(uint32_t w, uint32_t h, uint8_t r, uint8_t g, uint8_t b) {
    HeadlessSurface::Rgba src;
    src.width = w;
    src.height = h;
    src.pixels.resize(static_cast<size_t>(w) * h * 4u);
    for (size_t i = 0; i < static_cast<size_t>(w) * h; ++i) {
        src.pixels[i * 4 + 0] = r;
        src.pixels[i * 4 + 1] = g;
        src.pixels[i * 4 + 2] = b;
        src.pixels[i * 4 + 3] = 255;
    }
    return src;
}

// Writes a solid-colour PNG with the wrapper's own encoder, so the fixture
// needs no checked-in binary asset.
bool write_solid_png(const std::string& path, uint32_t w, uint32_t h, uint8_t r, uint8_t g,
                     uint8_t b) {
    const auto png = HeadlessSurface::encode_png(solid(w, h, r, g, b));
    if (png.empty())
        return false;
    std::ofstream out(path, std::ios::binary);
    if (!out)
        return false;
    out.write(reinterpret_cast<const char*>(png.data()), static_cast<std::streamsize>(png.size()));
    return out.good();
}

std::string temp_path(const char* leaf) {
    auto dir = std::filesystem::temp_directory_path() / "vellum-headless-surface";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return (dir / leaf).string();
}

std::string base64(const std::vector<uint8_t>& bytes) {
    static const char* table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    for (size_t i = 0; i < bytes.size(); i += 3) {
        const uint32_t a = bytes[i];
        const uint32_t b = i + 1 < bytes.size() ? bytes[i + 1] : 0;
        const uint32_t c = i + 2 < bytes.size() ? bytes[i + 2] : 0;
        const uint32_t v = (a << 16) | (b << 8) | c;
        out += table[(v >> 18) & 63];
        out += table[(v >> 12) & 63];
        out += i + 1 < bytes.size() ? table[(v >> 6) & 63] : '=';
        out += i + 2 < bytes.size() ? table[v & 63] : '=';
    }
    return out;
}

FRAMEWORK_TEST(create_rejects_an_invalid_size_without_touching_the_device) {
    HeadlessSurface::Config cfg;
    cfg.width = 0;
    cfg.height = 0;
    std::string err;
    auto surface = HeadlessSurface::create(cfg, &err);
    CHECK(surface == nullptr);
    CHECK(!err.empty());
}

FRAMEWORK_TEST(a_clear_only_frame_is_reproducible_and_empty) {
    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    CHECK(surface->is_ready());

    auto frame_a = surface->render_rgba(nullptr);
    CHECK(!frame_a.empty());
    CHECK(frame_a.width == kW);
    CHECK(frame_a.height == kH);

    // Consecutive renders of the same frame match on all but sub-LSB readback
    // jitter: at most 1% of bytes may differ.
    auto frame_b = surface->render_rgba(nullptr);
    CHECK(!frame_b.empty());
    CHECK(frame_a.pixels.size() == frame_b.pixels.size());
    size_t diff_bytes = 0;
    for (size_t i = 0; i < frame_a.pixels.size() && i < frame_b.pixels.size(); ++i)
        if (frame_a.pixels[i] != frame_b.pixels[i])
            ++diff_bytes;
    CHECK(diff_bytes * 100u < frame_a.pixels.size());

    // A cleared frame carries essentially no non-background pixels.
    CHECK(count_non_background(frame_a) < frame_a.pixel_count() / 100u);
}

FRAMEWORK_TEST(a_paint_callback_renders_and_encodes_to_png) {
    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;

    auto paint_red_band = [](vellum::canvas::Canvas& c) {
        c.set_fill_color(vellum::canvas::Color::rgba8(220, 30, 30, 255));
        c.fill_rect(16.0f, 16.0f, static_cast<float>(kW) - 32.0f, static_cast<float>(kH) - 32.0f);
    };

    auto rgba = surface->render_rgba(paint_red_band);
    CHECK(!rgba.empty());
    CHECK(rgba.width == kW);
    CHECK(rgba.height == kH);
    CHECK(count_non_background(rgba) > 1000u);

    auto png = surface->render_png(paint_red_band);
    CHECK(png.size() >= 8u);
    static constexpr uint8_t kMagic[8] = {0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A};
    CHECK(png.size() >= 8u && std::memcmp(png.data(), kMagic, sizeof(kMagic)) == 0);
}

FRAMEWORK_TEST(a_file_backed_image_draws_its_own_ink) {
    constexpr uint8_t kImgR = 240, kImgG = 200, kImgB = 20;
    const std::string png_path = temp_path("solid-amber.png");
    CHECK(write_solid_png(png_path, 64, 64, kImgR, kImgG, kImgB));

    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    bool drew = false;
    auto rgba = surface->render_rgba([&](vellum::canvas::Canvas& c) {
        drew = c.draw_image_from_file(png_path, 32.0f, 16.0f, 96.0f, 96.0f);
    });
    CHECK(drew);
    // The 96x96 destination is 9216 pixels; a dropped draw yields exactly 0.
    CHECK(count_near_color(rgba, kImgR, kImgG, kImgB) > 4000u);
}

FRAMEWORK_TEST(an_svg_document_draws_its_own_ink) {
    constexpr uint8_t kSvgR = 30, kSvgG = 220, kSvgB = 120;
    const std::string svg =
        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"100\" height=\"100\" "
        "viewBox=\"0 0 100 100\">"
        "<rect x=\"0\" y=\"0\" width=\"100\" height=\"100\" fill=\"#1EDC78\"/>"
        "</svg>";

    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    bool drew = false;
    auto rgba = surface->render_rgba(
        [&](vellum::canvas::Canvas& c) { drew = c.draw_svg(svg, 32.0f, 16.0f, 96.0f, 96.0f); });
    CHECK(drew);
    CHECK(count_near_color(rgba, kSvgR, kSvgG, kSvgB) > 4000u);
}

// An SVG that embeds its artwork as a data-URI image is decoded into a raster
// image and drawn straight to the canvas. Without an image provider on the
// recorder, Graphite discards that draw while draw_svg still returns true.
FRAMEWORK_TEST(an_svgs_embedded_raster_image_draws_on_every_frame) {
    constexpr uint8_t kImgR = 240, kImgG = 200, kImgB = 20;
    const auto png = HeadlessSurface::encode_png(solid(64, 64, kImgR, kImgG, kImgB));
    CHECK(!png.empty());
    const std::string svg =
        "<svg xmlns=\"http://www.w3.org/2000/svg\" "
        "xmlns:xlink=\"http://www.w3.org/1999/xlink\" "
        "width=\"100\" height=\"100\" viewBox=\"0 0 100 100\">"
        "<image x=\"0\" y=\"0\" width=\"100\" height=\"100\" "
        "xlink:href=\"data:image/png;base64," +
        base64(png) + "\"/></svg>";

    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    // The first frame uploads through the provider and the rest hit its
    // cache; a cache that handed back a stale texture would lose the artwork
    // on a later frame.
    for (int frame = 0; frame < 3; ++frame) {
        auto rgba = surface->render_rgba([&](vellum::canvas::Canvas& c) {
            c.draw_svg(svg, 32.0f, 16.0f, 96.0f, 96.0f);
        });
        CHECK(count_near_color(rgba, kImgR, kImgG, kImgB) > 4000u);
    }
}

// A file-backed image is decoded and uploaded once, then reused from the
// path-keyed cache; a repaint that lost the artwork would be a cache handing
// back something the current recorder cannot draw.
FRAMEWORK_TEST(a_file_backed_image_keeps_drawing_across_frames) {
    constexpr uint8_t kImgR = 240, kImgG = 200, kImgB = 20;
    const std::string png_path = temp_path("solid-amber-repeat.png");
    CHECK(write_solid_png(png_path, 256, 256, kImgR, kImgG, kImgB));

    std::string err;
    auto surface = HeadlessSurface::create(config(), &err);
    CHECK(surface != nullptr);
    if (!surface)
        return;
    for (int frame = 0; frame < 3; ++frame) {
        auto rgba = surface->render_rgba([&](vellum::canvas::Canvas& c) {
            c.draw_image_from_file(png_path, 32.0f, 16.0f, 96.0f, 96.0f);
        });
        CHECK(count_near_color(rgba, kImgR, kImgG, kImgB) > 4000u);
    }
}

} // namespace

int main() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    std::string err;
    auto probe = HeadlessSurface::create(config(), &err);
    if (!probe) {
        std::printf("SKIP no headless GPU surface: %s\n", err.c_str());
        return 77;
    }
    probe.reset();
    return framework_test::run_all();
}
