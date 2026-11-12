// Provenance: written for this module from Apple's ImageIO and CoreGraphics
// documentation. It contains no code from any GPL-licensed project.

#include "metal_image_decode.hpp"

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

namespace vellum::rmlui::detail {

bool decode_premultiplied(const std::vector<std::uint8_t>& encoded, int& width, int& height,
                          std::vector<std::uint8_t>& out) {
    NSData* data = [NSData dataWithBytesNoCopy:const_cast<std::uint8_t*>(encoded.data())
                                        length:encoded.size()
                                  freeWhenDone:NO];
    CGImageSourceRef source = CGImageSourceCreateWithData((__bridge CFDataRef)data, nullptr);
    if (!source)
        return false;
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, nullptr);
    CFRelease(source);
    if (!image)
        return false;
    width = static_cast<int>(CGImageGetWidth(image));
    height = static_cast<int>(CGImageGetHeight(image));
    bool ok = width > 0 && height > 0;
    if (ok) {
        out.assign(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4u, 0);
        CGColorSpaceRef colour_space = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(
            out.data(), static_cast<std::size_t>(width), static_cast<std::size_t>(height), 8,
            static_cast<std::size_t>(width) * 4u, colour_space,
            static_cast<CGBitmapInfo>(kCGImageAlphaPremultipliedLast) | kCGBitmapByteOrderDefault);
        CGColorSpaceRelease(colour_space);
        ok = context != nullptr;
        if (ok) {
            CGContextDrawImage(context, CGRectMake(0, 0, width, height), image);
            CGContextRelease(context);
        }
    }
    CGImageRelease(image);
    return ok;
}

} // namespace vellum::rmlui::detail
