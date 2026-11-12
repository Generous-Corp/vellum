// The Metal renderer's image decode premultiplies straight 8-bit colour as
// round(c * a / 255) = (c * a + 127) / 255, for every one of the 65,536
// (c, a) pairs. A 256 x 256 straight-alpha PNG holds them all, one pair per
// pixel (red = c, green = 255 - c, blue = c, alpha = a). CoreGraphics does the
// arithmetic, so this test is what notices if a macOS release changes it.
//
// The same pixels tagged with the sRGB profile must decode identically: skin
// PNGs embed "sRGB IEC61966-2.1", and drawing into a DeviceRGB context leaves
// them unchanged today. A CoreGraphics change that colour-matched tagged
// images differently would show up here.
//
// Control: the shift forms (c * a + 0x7f) >> 8 and (c * a + 0xff) >> 8 must
// each disagree with the table somewhere, or the table could not tell the
// rounding apart.
#include "metal_image_decode.hpp"

#include "../test_support.hpp"

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {

constexpr int kSide = 256;

// A PNG of straight (not premultiplied) RGBA8 pixels, untagged or tagged sRGB.
std::vector<std::uint8_t> encode_png(const std::vector<std::uint8_t>& straight, bool srgb) {
    CGColorSpaceRef space = srgb ? CGColorSpaceCreateWithName(kCGColorSpaceSRGB) : CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef provider = CGDataProviderCreateWithData(nullptr, straight.data(), straight.size(), nullptr);
    CGImageRef image = CGImageCreate(kSide, kSide, 8, 32, kSide * 4, space,
                                     static_cast<CGBitmapInfo>(kCGImageAlphaLast) | kCGBitmapByteOrderDefault,
                                     provider, nullptr, false, kCGRenderingIntentDefault);
    NSMutableData* png = [NSMutableData data];
    CGImageDestinationRef destination =
        CGImageDestinationCreateWithData((__bridge CFMutableDataRef)png, CFSTR("public.png"), 1, nullptr);
    CGImageDestinationAddImage(destination, image, nullptr);
    const bool written = CGImageDestinationFinalize(destination);
    CFRelease(destination);
    CGImageRelease(image);
    CGDataProviderRelease(provider);
    CGColorSpaceRelease(space);
    VELLUM_RMLUI_CHECK(written);
    const auto* bytes = static_cast<const std::uint8_t*>(png.bytes);
    return {bytes, bytes + png.length};
}

} // namespace

int main() {
    @autoreleasepool {
        std::vector<std::uint8_t> straight(kSide * kSide * 4);
        for (int a = 0; a < kSide; ++a)
            for (int c = 0; c < kSide; ++c) {
                auto* p = &straight[static_cast<std::size_t>(a * kSide + c) * 4u];
                p[0] = static_cast<std::uint8_t>(c);
                p[1] = static_cast<std::uint8_t>(255 - c);
                p[2] = static_cast<std::uint8_t>(c);
                p[3] = static_cast<std::uint8_t>(a);
            }
        int width = 0;
        int height = 0;
        std::vector<std::uint8_t> decoded;
        VELLUM_RMLUI_CHECK(
            vellum::rmlui::detail::decode_premultiplied(encode_png(straight, false), width, height, decoded));
        VELLUM_RMLUI_CHECK(width == kSide && height == kSide);

        // Tagged sRGB: the same bytes. The tag must actually be in the file,
        // or this compares an untagged PNG with itself.
        const auto untagged = encode_png(straight, false);
        const auto tagged = encode_png(straight, true);
        const std::string tagged_bytes(tagged.begin(), tagged.end());
        VELLUM_RMLUI_CHECK(tagged != untagged);
        VELLUM_RMLUI_CHECK(tagged_bytes.find("iCCP") != std::string::npos ||
                           tagged_bytes.find("sRGB") != std::string::npos);
        std::vector<std::uint8_t> decoded_tagged;
        VELLUM_RMLUI_CHECK(
            vellum::rmlui::detail::decode_premultiplied(tagged, width, height, decoded_tagged));
        VELLUM_RMLUI_CHECK(decoded_tagged == decoded);

        int wrong = 0;
        int shift_7f_differs = 0;
        int shift_ff_differs = 0;
        for (int a = 0; a < kSide; ++a)
            for (int c = 0; c < kSide; ++c) {
                const auto* p = &decoded[static_cast<std::size_t>(a * kSide + c) * 4u];
                const int channels[3] = {c, 255 - c, c};
                for (int k = 0; k < 3; ++k) {
                    const int want = (channels[k] * a + 127) / 255;
                    if (p[k] != want && wrong++ < 5)
                        std::fprintf(stderr, "c=%d a=%d channel %d: decoded %d, round(c*a/255) is %d\n",
                                     channels[k], a, k, p[k], want);
                    shift_7f_differs += ((channels[k] * a + 0x7f) >> 8) != want ? 1 : 0;
                    shift_ff_differs += ((channels[k] * a + 0xff) >> 8) != want ? 1 : 0;
                }
                if (p[3] != a && wrong++ < 5)
                    std::fprintf(stderr, "c=%d a=%d: decoded alpha %d\n", c, a, p[3]);
            }
        if (wrong) {
            std::fprintf(stderr, "%d channel values differ from round(c * a / 255)\n", wrong);
            return 1;
        }
        VELLUM_RMLUI_CHECK(shift_7f_differs > 0 && shift_ff_differs > 0);
        std::printf("all 65,536 (c, a) pairs premultiply as round(c * a / 255); the shift forms would differ in "
                    "%d and %d channel values\n",
                    shift_7f_differs, shift_ff_differs);
        return 0;
    }
}
