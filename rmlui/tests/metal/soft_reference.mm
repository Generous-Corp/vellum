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
        SoftRenderer renderer(0x00000000u);
        renderer.set_image_decoder(decode);
        HeadlessRecorder lua;
        Backend backend(assets, renderer, lua);
        if (backend.open(kWidth, kHeight) != Status::ok)
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
