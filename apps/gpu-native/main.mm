#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace {

using vellum::graphics::Color;
using vellum::graphics::Rect;
using vellum::graphics::Scene;
using vellum::graphics::SceneNode;
using vellum::graphics::SkiaDawnSurface;

Scene proof_scene(float width, float height) {
    Scene scene;
    scene.width = width;
    scene.height = height;
    scene.background = Color::hex(0x0B1020);
    scene.root = {
        .id = "proof/root",
        .kind = SceneNode::Kind::group,
        .bounds = {0.0F, 0.0F, width, height},
        .children = {
            {
                .id = "proof/card",
                .kind = SceneNode::Kind::rectangle,
                .bounds = {32.0F, 28.0F, width - 64.0F, height - 56.0F},
                .fill = Color::hex(0x3346A8),
                .corner_radius = 24.0F,
                .children = {
                    {
                        .id = "proof/card/surface",
                        .kind = SceneNode::Kind::rectangle,
                        .bounds = {18.0F, 18.0F, width - 100.0F, height - 92.0F},
                        .fill = Color::rgba(0.04F, 0.06F, 0.13F, 0.93F),
                        .corner_radius = 16.0F,
                    },
                    {
                        .id = "proof/title",
                        .kind = SceneNode::Kind::text,
                        .bounds = {44.0F, 38.0F, width - 120.0F, 42.0F},
                        .fill = Color::hex(0xF4F1FF),
                        .text = "Vellum GPU",
                        .font_size = 28.0F,
                    },
                    {
                        .id = "proof/status",
                        .kind = SceneNode::Kind::text,
                        .bounds = {44.0F, 78.0F, width - 120.0F, 28.0F},
                        .fill = Color::hex(0x79E2F2),
                        .text = "retained scene -> Skia Graphite -> Dawn -> Metal",
                        .font_size = 14.0F,
                    },
                    {
                        .id = "proof/action",
                        .kind = SceneNode::Kind::rectangle,
                        .bounds = {44.0F, height - 132.0F, 150.0F, 44.0F},
                        .fill = Color::hex(0x14B8A6),
                        .corner_radius = 12.0F,
                        .children = {
                            {
                                .id = "proof/action/label",
                                .kind = SceneNode::Kind::text,
                                .bounds = {22.0F, 10.0F, 110.0F, 24.0F},
                                .fill = Color::hex(0x041412),
                                .text = "Create board",
                                .font_size = 15.0F,
                            },
                        },
                    },
                },
            },
        },
    };
    return scene;
}

CAMetalLayer* make_metal_layer(std::uint32_t width, std::uint32_t height, float scale) {
    CAMetalLayer* layer = [CAMetalLayer layer];
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(width * scale, height * scale);
    return layer;
}

bool validate_surface(
    SkiaDawnSurface& surface, bool expect_native, std::string* error) {
    const auto& evidence = surface.evidence();
    if (!evidence.available || evidence.native_surface != expect_native ||
        evidence.fallback ||
        evidence.renderer != "Skia Graphite" || evidence.backend != "Metal") {
        if (error) {
            *error = "GPU evidence does not prove native Skia Graphite on Dawn/Metal";
        }
        return false;
    }
    return true;
}

int self_test(std::string_view output, bool native_surface) {
    constexpr std::uint32_t width = 640;
    constexpr std::uint32_t height = 400;
    @autoreleasepool {
        CAMetalLayer* layer = native_surface
            ? make_metal_layer(width, height, 1.0F)
            : nil;
        if (native_surface && layer.device == nil) {
            std::cerr << "Metal device unavailable\n";
            return 1;
        }

        std::string error;
        auto surface = SkiaDawnSurface::create(
            {.width = width,
             .height = height,
             .scale = 1.0F,
             .native_surface_handle = native_surface ? (__bridge void*)layer : nullptr},
            &error);
        if (!surface || !validate_surface(*surface, native_surface, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        const auto scene = proof_scene(width, height);
        if (vellum::graphics::find_node(scene, "proof/action") == nullptr ||
            vellum::graphics::find_node(scene, "proof/missing") != nullptr) {
            std::cerr << "semantic addressability check failed\n";
            return 1;
        }
        if (!surface->render(scene, &error)) {
            std::cerr << error << '\n';
            return 1;
        }

        std::vector<std::uint8_t> rgba;
        std::uint32_t pixel_width = 0;
        std::uint32_t pixel_height = 0;
        if (!surface->capture_rgba(rgba, pixel_width, pixel_height, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        const auto stats = vellum::graphics::analyze_capture_rgba(
            rgba, pixel_width, pixel_height);
        if (!vellum::graphics::passes_content_floor(stats)) {
            std::cerr << "GPU capture failed content floor: colors="
                      << stats.unique_colors << " stddev="
                      << stats.luminance_standard_deviation << " content="
                      << stats.non_background_pixels << '\n';
            return 1;
        }

        std::vector<std::uint8_t> blank(pixel_width * pixel_height * 4U, 0U);
        const auto blank_stats = vellum::graphics::analyze_capture_rgba(
            blank, pixel_width, pixel_height);
        if (vellum::graphics::passes_content_floor(blank_stats)) {
            std::cerr << "content-floor negative control accepted a blank frame\n";
            return 1;
        }

        std::vector<std::uint8_t> png;
        if (!surface->capture_png(png, &error) || png.size() < 1024U ||
            png[0] != 0x89U || png[1] != 0x50U || png[2] != 0x4EU ||
            png[3] != 0x47U) {
            std::cerr << (error.empty() ? "invalid PNG capture" : error) << '\n';
            return 1;
        }
        if (!output.empty()) {
            std::ofstream file(std::string(output), std::ios::binary);
            file.write(reinterpret_cast<const char*>(png.data()),
                       static_cast<std::streamsize>(png.size()));
            if (!file) {
                std::cerr << "could not write capture: " << output << '\n';
                return 1;
            }
        }

        const auto& evidence = surface->evidence();
        std::cout << "vellum-gpu-native: renderer=" << evidence.renderer
                  << " backend=" << evidence.backend
                  << " native_surface=" << (native_surface ? "true" : "false")
                  << " fallback=false colors="
                  << stats.unique_colors << " content_pixels="
                  << stats.non_background_pixels << " png_bytes=" << png.size()
                  << '\n';
    }
    return 0;
}

}  // namespace

@interface VellumGpuView : NSView {
@private
    std::unique_ptr<SkiaDawnSurface> _surface;
}
@end

@implementation VellumGpuView
- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (self) {
        self.wantsLayer = YES;
        self.layer = make_metal_layer(
            static_cast<std::uint32_t>(frame.size.width),
            static_cast<std::uint32_t>(frame.size.height), 1.0F);
    }
    return self;
}

- (BOOL)isFlipped { return YES; }

- (void)viewDidMoveToWindow {
    [super viewDidMoveToWindow];
    if (self.window == nil || _surface) return;
    CAMetalLayer* layer = static_cast<CAMetalLayer*>(self.layer);
    const float scale = static_cast<float>(self.window.backingScaleFactor);
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(self.bounds.size.width * scale,
                                    self.bounds.size.height * scale);
    std::string error;
    _surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(self.bounds.size.width),
         .height = static_cast<std::uint32_t>(self.bounds.size.height),
         .scale = scale,
         .native_surface_handle = (__bridge void*)layer},
        &error);
    if (!_surface || !_surface->render(
            proof_scene(static_cast<float>(self.bounds.size.width),
                        static_cast<float>(self.bounds.size.height)),
            &error)) {
        NSLog(@"Vellum GPU initialization failed: %s", error.c_str());
    }
}

- (void)setFrameSize:(NSSize)newSize {
    [super setFrameSize:newSize];
    if (!_surface || self.window == nil) return;
    const float scale = static_cast<float>(self.window.backingScaleFactor);
    CAMetalLayer* layer = static_cast<CAMetalLayer*>(self.layer);
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(newSize.width * scale, newSize.height * scale);
    std::string error;
    if (!_surface->resize(static_cast<std::uint32_t>(newSize.width),
                          static_cast<std::uint32_t>(newSize.height), scale, &error) ||
        !_surface->render(proof_scene(static_cast<float>(newSize.width),
                                     static_cast<float>(newSize.height)), &error)) {
        NSLog(@"Vellum GPU resize failed: %s", error.c_str());
    }
}
@end

@interface VellumGpuDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow* window;
@end

@implementation VellumGpuDelegate
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    (void)notification;
    const NSRect frame = NSMakeRect(0, 0, 640, 400);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable |
                             NSWindowStyleMaskResizable)
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"Vellum GPU Proof";
    self.window.contentView = [[VellumGpuView alloc] initWithFrame:frame];
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
    if (argc >= 2 && (std::string_view(argv[1]) == "--self-test" ||
                      std::string_view(argv[1]) == "--offscreen-self-test")) {
        const bool native_surface = std::string_view(argv[1]) == "--self-test";
        const std::string_view output = argc == 4 &&
                                                std::string_view(argv[2]) == "--capture"
                                            ? std::string_view(argv[3])
                                            : std::string_view{};
        return self_test(output, native_surface);
    }

    @autoreleasepool {
        NSApplication* application = NSApplication.sharedApplication;
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        VellumGpuDelegate* delegate = [[VellumGpuDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
