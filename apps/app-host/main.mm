#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <vellum/authoring/js_application.hpp>
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace {

using vellum::authoring::Interaction;
using vellum::authoring::JsApplication;
using vellum::authoring::RenderedApplication;
using vellum::graphics::SkiaDawnSurface;

struct Options final {
    std::filesystem::path bundle;
    std::filesystem::path capture;
    std::vector<std::string> presses;
    bool no_window = false;
};

void usage() {
    std::cerr << "usage: vellum-app-host [--bundle FILE] [--self-test|--no-window] "
                 "[--press NODE_ID] [--capture PNG]\n";
}

std::optional<Options> parse_options(int argc, const char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument = argv[index];
        if (argument == "--self-test" || argument == "--no-window") {
            options.no_window = true;
        } else if (argument == "--bundle" || argument == "--capture" ||
                   argument == "--press") {
            if (++index >= argc) return std::nullopt;
            if (argument == "--bundle") options.bundle = argv[index];
            if (argument == "--capture") options.capture = argv[index];
            if (argument == "--press") options.presses.emplace_back(argv[index]);
        } else {
            return std::nullopt;
        }
    }
    return options;
}

std::filesystem::path bundled_application_script() {
    NSString* resource = [NSBundle.mainBundle pathForResource:@"app" ofType:@"js"];
    return resource == nil ? std::filesystem::path{} :
                             std::filesystem::path(resource.UTF8String);
}

bool read_file(const std::filesystem::path& path, std::string& output) {
    std::ifstream input(path, std::ios::binary);
    std::ostringstream contents;
    contents << input.rdbuf();
    if (!input || contents.str().empty()) return false;
    output = contents.str();
    return true;
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

bool validate_gpu(const SkiaDawnSurface& surface, bool native, std::string* error) {
    const auto& evidence = surface.evidence();
    if (evidence.available && evidence.native_surface == native && !evidence.fallback &&
        evidence.renderer == "Skia Graphite" && evidence.backend == "Metal") {
        return true;
    }
    if (error) *error = "renderer is not native Skia Graphite on Dawn/Metal";
    return false;
}

bool press_node(JsApplication& application, RenderedApplication& rendered,
                std::string_view node_id, std::string* error) {
    const auto interaction = std::find_if(
        rendered.interactions.begin(), rendered.interactions.end(),
        [node_id](const Interaction& item) {
            return item.node_id == node_id && item.event == "press";
        });
    if (interaction == rendered.interactions.end()) {
        if (error) *error = "scenario press target is missing or not pressable: " +
                            std::string(node_id);
        return false;
    }
    return application.dispatch(
        interaction->action, R"({"pointerType":"automation"})", rendered, error);
}

bool write_png(SkiaDawnSurface& surface, const std::filesystem::path& output,
               std::size_t& byte_count, std::string* error) {
    std::vector<std::uint8_t> png;
    if (!surface.capture_png(png, error) || png.size() < 8U ||
        png[0] != 0x89U || png[1] != 0x50U || png[2] != 0x4EU || png[3] != 0x47U) {
        if (error && error->empty()) *error = "GPU capture did not produce a PNG";
        return false;
    }
    std::error_code filesystem_error;
    std::filesystem::create_directories(output.parent_path(), filesystem_error);
    if (filesystem_error) {
        if (error) *error = "could not create capture directory";
        return false;
    }
    std::ofstream file(output, std::ios::binary);
    file.write(reinterpret_cast<const char*>(png.data()),
               static_cast<std::streamsize>(png.size()));
    if (!file) {
        if (error) *error = "could not write capture";
        return false;
    }
    byte_count = png.size();
    return true;
}

int run_headless(const Options& options, std::string_view bundle) {
    @autoreleasepool {
        std::string error;
        auto application = JsApplication::create(bundle, &error);
        RenderedApplication rendered;
        if (!application || !application->render(rendered, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        for (const auto& node_id : options.presses) {
            if (!press_node(*application, rendered, node_id, &error)) {
                std::cerr << error << '\n';
                return 1;
            }
        }
        const auto width = static_cast<std::uint32_t>(rendered.scene.width);
        const auto height = static_cast<std::uint32_t>(rendered.scene.height);
        auto surface = SkiaDawnSurface::create(
            {.width = width, .height = height, .scale = 1.0F}, &error);
        if (!surface || !validate_gpu(*surface, false, &error) ||
            !surface->render(rendered.scene, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        std::vector<std::uint8_t> rgba;
        std::uint32_t pixel_width = 0;
        std::uint32_t pixel_height = 0;
        if (!surface->capture_rgba(rgba, pixel_width, pixel_height, &error) ||
            !vellum::graphics::passes_content_floor(
                vellum::graphics::analyze_capture_rgba(rgba, pixel_width, pixel_height))) {
            std::cerr << (error.empty() ? "rendered frame failed content floor" : error) << '\n';
            return 1;
        }
        std::size_t png_bytes = 0;
        if (!options.capture.empty() &&
            !write_png(*surface, options.capture, png_bytes, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        std::cout << "vellum-app-host: renderer=Skia Graphite backend=Metal "
                  << "fallback=false width=" << pixel_width
                  << " height=" << pixel_height
                  << " interactions=" << rendered.interactions.size()
                  << " png_bytes=" << png_bytes << '\n';
    }
    return 0;
}

}  // namespace

@interface VellumApplicationView : NSView {
@private
    std::unique_ptr<JsApplication> _application;
    RenderedApplication _rendered;
    std::unique_ptr<SkiaDawnSurface> _surface;
}
- (instancetype)initWithFrame:(NSRect)frame bundle:(const std::string&)bundle;
@end

@implementation VellumApplicationView
- (instancetype)initWithFrame:(NSRect)frame bundle:(const std::string&)bundle {
    self = [super initWithFrame:frame];
    if (self) {
        std::string error;
        _application = JsApplication::create(bundle, &error);
        if (!_application || !_application->render(_rendered, &error)) {
            NSLog(@"Vellum application initialization failed: %s", error.c_str());
            return nil;
        }
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
    if (!_surface || !validate_gpu(*_surface, true, &error) ||
        !_surface->render(_rendered.scene, &error)) {
        NSLog(@"Vellum GPU initialization failed: %s", error.c_str());
    }
}

- (void)mouseDown:(NSEvent*)event {
    const NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    const auto interaction = std::find_if(
        _rendered.interactions.begin(), _rendered.interactions.end(),
        [point](const Interaction& item) {
            return item.event == "press" && point.x >= item.bounds.x &&
                   point.y >= item.bounds.y &&
                   point.x <= item.bounds.x + item.bounds.width &&
                   point.y <= item.bounds.y + item.bounds.height;
        });
    if (interaction == _rendered.interactions.end()) return;
    std::string error;
    if (!_application->dispatch(
            interaction->action, R"({"pointerType":"mouse"})", _rendered, &error) ||
        !_surface || !_surface->render(_rendered.scene, &error)) {
        NSLog(@"Vellum interaction failed: %s", error.c_str());
    }
}
@end

@interface VellumApplicationDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow* window;
@property(nonatomic, copy) NSData* bundleData;
@end

@implementation VellumApplicationDelegate
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    (void)notification;
    const std::string bundle(
        static_cast<const char*>(self.bundleData.bytes), self.bundleData.length);
    std::string error;
    auto probe = JsApplication::create(bundle, &error);
    RenderedApplication rendered;
    if (!probe || !probe->render(rendered, &error)) {
        NSLog(@"Vellum application failed: %s", error.c_str());
        [NSApp terminate:nil];
        return;
    }
    const NSRect frame = NSMakeRect(0, 0, rendered.scene.width, rendered.scene.height);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable)
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = NSBundle.mainBundle.infoDictionary[@"CFBundleDisplayName"]
        ?: @"Vellum Application";
    self.window.contentView = [[VellumApplicationView alloc] initWithFrame:frame bundle:bundle];
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
    const auto parsed = parse_options(argc, argv);
    if (!parsed) {
        usage();
        return 2;
    }
    Options options = *parsed;
    if (options.bundle.empty()) options.bundle = bundled_application_script();
    std::string bundle;
    if (options.bundle.empty() || !read_file(options.bundle, bundle)) {
        std::cerr << "could not read application bundle: " << options.bundle << '\n';
        return 2;
    }
    if (options.no_window || !options.capture.empty() || !options.presses.empty()) {
        return run_headless(options, bundle);
    }
    @autoreleasepool {
        NSApplication* application = NSApplication.sharedApplication;
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        VellumApplicationDelegate* delegate = [[VellumApplicationDelegate alloc] init];
        delegate.bundleData = [NSData dataWithBytes:bundle.data() length:bundle.size()];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
