#import <Cocoa/Cocoa.h>
#import <CoreGraphics/CoreGraphics.h>

#include <vellum/graphics/core_graphics_canvas.hpp>
#include <vellum/runtime/kernel.hpp>

#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <string_view>
#include <unordered_set>

namespace {

using vellum::graphics::Color;
using vellum::graphics::CoreGraphicsCanvas;

void paint_scene(CGContextRef context, float width, float height) {
    CoreGraphicsCanvas canvas(context, width, height);
    canvas.set_fill_color(Color::hex(0x171B24));
    canvas.fill_rect(0.0f, 0.0f, width, height);

    const Color stops[] = {
        Color::hex(0x8A5CFF),
        Color::hex(0x35D6ED),
    };
    const float positions[] = {0.0f, 1.0f};
    canvas.set_fill_gradient_linear(36.0f, 36.0f, width - 36.0f, height - 36.0f,
                                    stops, positions, 2);
    canvas.fill_rounded_rect(36.0f, 36.0f, width - 72.0f, height - 72.0f, 28.0f);
    canvas.clear_fill_gradient();

    canvas.set_fill_color(Color::rgba(0.07f, 0.08f, 0.12f, 0.88f));
    canvas.fill_rounded_rect(64.0f, 64.0f, width - 128.0f, height - 128.0f, 20.0f);
    canvas.set_fill_color(Color::hex(0xF5F3FF));
    canvas.set_font("Helvetica Neue", 30.0f);
    canvas.fill_text("Vellum", 92.0f, 122.0f);
    canvas.set_fill_color(Color::hex(0xB7C1D9));
    canvas.set_font("Helvetica Neue", 16.0f);
    canvas.fill_text("audio-free retained canvas smoke", 92.0f, 154.0f);
}

bool render_self_test() {
    constexpr std::size_t width = 320;
    constexpr std::size_t height = 200;
    constexpr std::size_t bytes_per_row = width * 4;
    auto pixels = std::make_unique<std::uint8_t[]>(bytes_per_row * height);
    std::memset(pixels.get(), 0, bytes_per_row * height);

    auto color_space = CGColorSpaceCreateDeviceRGB();
    auto context = CGBitmapContextCreate(
        pixels.get(), width, height, 8, bytes_per_row, color_space,
        static_cast<CGBitmapInfo>(
            static_cast<std::uint32_t>(kCGImageAlphaPremultipliedLast) |
            static_cast<std::uint32_t>(kCGBitmapByteOrder32Big)));
    CGColorSpaceRelease(color_space);
    if (context == nullptr) {
        std::cerr << "could not create CoreGraphics bitmap context\n";
        return false;
    }

    paint_scene(context, static_cast<float>(width), static_cast<float>(height));
    CGContextFlush(context);

    std::size_t non_zero_bytes = 0;
    std::unordered_set<std::uint32_t> colors;
    for (std::size_t i = 0; i < bytes_per_row * height; ++i) {
        non_zero_bytes += pixels[i] != 0 ? 1 : 0;
    }
    for (std::size_t i = 0; i < width * height; ++i) {
        const auto offset = i * 4;
        const auto color = (static_cast<std::uint32_t>(pixels[offset]) << 24U) |
                           (static_cast<std::uint32_t>(pixels[offset + 1]) << 16U) |
                           (static_cast<std::uint32_t>(pixels[offset + 2]) << 8U) |
                           static_cast<std::uint32_t>(pixels[offset + 3]);
        colors.insert(color);
    }
    CGContextRelease(context);

    const auto minimum_content = width * height;
    if (non_zero_bytes < minimum_content) {
        std::cerr << "rendered frame failed content floor: " << non_zero_bytes << '\n';
        return false;
    }
    if (colors.size() < 64) {
        std::cerr << "rendered frame lacks visual variation: " << colors.size() << '\n';
        return false;
    }
    std::cout << "vellum-smoke-native: retained CoreGraphics canvas rendered "
              << non_zero_bytes << " non-zero bytes and " << colors.size()
              << " colors\n";
    return true;
}

}  // namespace

@interface VellumSmokeView : NSView
@end

@implementation VellumSmokeView
- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    auto* context = NSGraphicsContext.currentContext.CGContext;
    paint_scene(context, static_cast<float>(self.bounds.size.width),
                static_cast<float>(self.bounds.size.height));
}
@end

@interface VellumSmokeDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow* window;
@end

@implementation VellumSmokeDelegate
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    (void)notification;
    const NSRect frame = NSMakeRect(0, 0, 640, 400);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:(NSWindowStyleMaskTitled |
                             NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable |
                             NSWindowStyleMaskResizable)
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"Vellum Smoke";
    self.window.contentView = [[VellumSmokeView alloc] initWithFrame:frame];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)sender {
    (void)sender;
    return YES;
}
@end

int main(int argc, const char* argv[]) {
    vellum::runtime::Kernel kernel(
        {.application_id = "dev.vellum.smoke-native"});
    if (const auto status = kernel.start(); !status) {
        std::cerr << status.message() << '\n';
        return 1;
    }

    if (argc == 2 && std::string_view(argv[1]) == "--self-test") {
        const bool ok = render_self_test();
        if (const auto status = kernel.stop(); !status) {
            std::cerr << status.message() << '\n';
            return 1;
        }
        return ok ? 0 : 1;
    }

    @autoreleasepool {
        NSApplication* application = NSApplication.sharedApplication;
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        VellumSmokeDelegate* delegate = [[VellumSmokeDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }

    if (const auto status = kernel.stop(); !status) {
        std::cerr << status.message() << '\n';
        return 1;
    }
    return 0;
}
