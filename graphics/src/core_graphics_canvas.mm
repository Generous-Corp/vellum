#include <vellum/graphics/core_graphics_canvas.hpp>

#include <CoreText/CoreText.h>

#include <algorithm>
#include <array>
#include <stdexcept>
#include <utility>

namespace vellum::graphics {

CoreGraphicsCanvas::CoreGraphicsCanvas(
    CGContextRef context, float width, float height) noexcept
    : context_(context), width_(width), height_(height) {}

void CoreGraphicsCanvas::set_fill_color(Color color) noexcept {
    fill_ = color;
}

void CoreGraphicsCanvas::set_font(std::string family, float size) {
    font_family_ = family.empty() ? "Helvetica" : std::move(family);
    font_size_ = std::max(size, 1.0F);
}

void CoreGraphicsCanvas::set_fill_gradient_linear(
    float x0,
    float y0,
    float x1,
    float y1,
    const Color* colors,
    const float* positions,
    std::size_t count) {
    if (colors == nullptr || positions == nullptr || count < 2) {
        throw std::invalid_argument("a linear gradient needs at least two stops");
    }
    gradient_.start = CGPointMake(x0, y0);
    gradient_.end = CGPointMake(x1, y1);
    gradient_.colors.assign(colors, colors + count);
    gradient_.positions.clear();
    gradient_.positions.reserve(count);
    for (std::size_t index = 0; index < count; ++index) {
        gradient_.positions.push_back(std::clamp<CGFloat>(positions[index], 0.0, 1.0));
    }
}

void CoreGraphicsCanvas::clear_fill_gradient() noexcept {
    gradient_.colors.clear();
    gradient_.positions.clear();
}

void CoreGraphicsCanvas::apply_fill() const noexcept {
    CGContextSetRGBFillColor(context_, fill_.red, fill_.green, fill_.blue, fill_.alpha);
}

void CoreGraphicsCanvas::fill_current_path() {
    if (gradient_.colors.empty()) {
        apply_fill();
        CGContextFillPath(context_);
        return;
    }

    std::vector<CGFloat> components;
    components.reserve(gradient_.colors.size() * 4);
    for (const auto color : gradient_.colors) {
        components.insert(
            components.end(), {color.red, color.green, color.blue, color.alpha});
    }
    CGColorSpaceRef color_space = CGColorSpaceCreateDeviceRGB();
    CGGradientRef gradient = CGGradientCreateWithColorComponents(
        color_space,
        components.data(),
        gradient_.positions.data(),
        gradient_.colors.size());
    CGContextSaveGState(context_);
    CGContextClip(context_);
    CGContextDrawLinearGradient(
        context_,
        gradient,
        gradient_.start,
        gradient_.end,
        kCGGradientDrawsBeforeStartLocation | kCGGradientDrawsAfterEndLocation);
    CGContextRestoreGState(context_);
    CGGradientRelease(gradient);
    CGColorSpaceRelease(color_space);
}

void CoreGraphicsCanvas::fill_rect(float x, float y, float width, float height) {
    CGContextBeginPath(context_);
    CGContextAddRect(context_, CGRectMake(x, y, width, height));
    fill_current_path();
}

void CoreGraphicsCanvas::fill_rounded_rect(
    float x, float y, float width, float height, float radius) {
    const auto rect = CGRectMake(x, y, width, height);
    const auto clamped_radius = std::max(
        0.0F, std::min(radius, std::min(width, height) * 0.5F));
    CGPathRef path = CGPathCreateWithRoundedRect(
        rect, clamped_radius, clamped_radius, nullptr);
    CGContextBeginPath(context_);
    CGContextAddPath(context_, path);
    CGPathRelease(path);
    fill_current_path();
}

void CoreGraphicsCanvas::fill_text(
    const std::string& text, float x, float baseline_y) {
    CFStringRef family = CFStringCreateWithCString(
        kCFAllocatorDefault, font_family_.c_str(), kCFStringEncodingUTF8);
    CTFontRef font = CTFontCreateWithName(family, font_size_, nullptr);
    CFRelease(family);

    const std::array<CGFloat, 4> components{
        fill_.red, fill_.green, fill_.blue, fill_.alpha};
    CGColorSpaceRef color_space = CGColorSpaceCreateDeviceRGB();
    CGColorRef color = CGColorCreate(color_space, components.data());
    CFStringRef string = CFStringCreateWithBytes(
        kCFAllocatorDefault,
        reinterpret_cast<const UInt8*>(text.data()),
        static_cast<CFIndex>(text.size()),
        kCFStringEncodingUTF8,
        false);
    const void* keys[] = {kCTFontAttributeName, kCTForegroundColorAttributeName};
    const void* values[] = {font, color};
    CFDictionaryRef attributes = CFDictionaryCreate(
        kCFAllocatorDefault,
        keys,
        values,
        2,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    CFAttributedStringRef attributed = CFAttributedStringCreate(
        kCFAllocatorDefault, string, attributes);
    CTLineRef line = CTLineCreateWithAttributedString(attributed);

    CGContextSaveGState(context_);
    CGContextSetTextMatrix(context_, CGAffineTransformIdentity);
    CGContextSetTextPosition(context_, x, baseline_y);
    CTLineDraw(line, context_);
    CGContextRestoreGState(context_);

    CFRelease(line);
    CFRelease(attributed);
    CFRelease(attributes);
    CFRelease(string);
    CGColorRelease(color);
    CGColorSpaceRelease(color_space);
    CFRelease(font);
}

}  // namespace vellum::graphics
