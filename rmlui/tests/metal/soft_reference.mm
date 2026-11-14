// Renders the Metal visual-test documents with the soft renderer and writes
// each frame as premultiplied RGBA8: the reference metal_visual_test compares
// the Metal renderer with.
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include "visual_common.hpp"

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

#include <cstdio>
#include <filesystem>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

namespace {

class ScaledSoftRenderer final : public RenderInterface {
  public:
    explicit ScaledSoftRenderer(std::uint32_t scale) : scale_(scale) {}
    void begin_frame(std::uint32_t width, std::uint32_t height) override {
        renderer_.begin_frame(width * scale_, height * scale_);
    }
    void submit(const DrawCommand& input) override {
        auto command = input;
        command.x *= scale_;
        command.y *= scale_;
        command.width *= scale_;
        command.height *= scale_;
        command.translation_x *= scale_;
        command.translation_y *= scale_;
        if (command.scissor_enabled) {
            command.scissor.x *= static_cast<int>(scale_);
            command.scissor.y *= static_cast<int>(scale_);
            command.scissor.width *= static_cast<int>(scale_);
            command.scissor.height *= static_cast<int>(scale_);
        }
        if (command.transform_enabled) {
            command.transform[12] *= scale_;
            command.transform[13] *= scale_;
        }
        if (command.vertices) {
            auto vertices = std::make_shared<std::vector<DrawVertex>>(*command.vertices);
            for (auto& vertex : *vertices) {
                vertex.x *= scale_;
                vertex.y *= scale_;
            }
            command.vertices = std::move(vertices);
        }
        renderer_.submit(command);
    }
    void end_frame() override { renderer_.end_frame(); }
    void set_image_decoder(SoftRenderer::ImageDecoder decoder) {
        renderer_.set_image_decoder(std::move(decoder));
    }
    [[nodiscard]] const std::vector<std::uint8_t>& pixels() const noexcept {
        return renderer_.pixels();
    }
    [[nodiscard]] std::size_t skipped_draws() const noexcept { return renderer_.skipped_draws(); }
  private:
    std::uint32_t scale_;
    SoftRenderer renderer_{0x00000000u};
};

// The same decode the Metal renderer performs: ImageIO into a premultiplied
// RGBA8 bitmap in the device RGB space, so both sides sample identical texels.
bool decode(const std::string& encoded, std::uint32_t& width, std::uint32_t& height,
            std::vector<std::uint8_t>& out) {
    NSData* data = [NSData dataWithBytes:encoded.data() length:encoded.size()];
    CGImageSourceRef source = CGImageSourceCreateWithData((__bridge CFDataRef)data, nullptr);
    if (!source)
        return false;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, nullptr);
    CFRelease(source);
    if (!image)
        return false;
    width = static_cast<std::uint32_t>(CGImageGetWidth(image));
    height = static_cast<std::uint32_t>(CGImageGetHeight(image));
    out.assign(static_cast<std::size_t>(width) * height * 4u, 0);
    CGColorSpaceRef colour_space = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(
        out.data(), width, height, 8, static_cast<std::size_t>(width) * 4u, colour_space,
        static_cast<CGBitmapInfo>(kCGImageAlphaPremultipliedLast) | kCGBitmapByteOrderDefault);
    CGColorSpaceRelease(colour_space);
    const bool ok = context != nullptr;
    if (ok) {
        CGContextDrawImage(context, CGRectMake(0, 0, width, height), image);
        CGContextRelease(context);
    }
    CGImageRelease(image);
    return ok;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::fprintf(stderr, "usage: %s <rmlui-source> <fixtures> <output-dir>\n", argv[0]);
        return 2;
    }
    if (!Backend::fonts_enabled()) {
        std::fprintf(stderr, "the visual references need VELLUM_RMLUI_FONTS=ON\n");
        return 2;
    }
    DirectoryAssets assets(argv[1], argv[2]);
    const std::filesystem::path out_dir(argv[3]);
    std::filesystem::create_directories(out_dir);
    for (const char* document : kDocuments) {
        const auto scale = atlas_filter_document(document) ? 2u : 1u;
        ScaledSoftRenderer renderer(scale);
        renderer.set_image_decoder(decode);
        HeadlessRecorder lua;
        Backend backend(assets, renderer, lua);
        if (backend.set_density_ratio(static_cast<float>(scale)) != Status::ok ||
            backend.open(kWidth / scale, kHeight / scale) != Status::ok)
            return 1;
        for (const auto& font : kFonts) {
            FontFaceOptions options;
            options.style = font.italic ? FontStyle::italic : FontStyle::normal;
            options.weight = font.weight;
            if (backend.load_font_face(font.path, "LatoLatin", options) != Status::ok) {
                std::fprintf(stderr, "font failed: %s\n", font.path);
                return 1;
            }
        }
        if (backend.load_document(document) != Status::ok) {
            std::fprintf(stderr, "document failed: %s\n", document);
            return 1;
        }
        backend.render();
        backend.render(); // a second pass, after layout settles
        if (renderer.skipped_draws() != 0) {
            std::fprintf(stderr, "%s: the soft renderer skipped %zu draws\n", document,
                         static_cast<std::size_t>(renderer.skipped_draws()));
            return 1;
        }
        if (!write_rgba(out_dir / reference_name(document), renderer.pixels()))
            return 1;
        backend.close();
    }
    return 0;
}
