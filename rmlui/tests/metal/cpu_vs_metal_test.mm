// Renders the visual-test documents, plus the clip-mask and layer fixture,
// with VellumRmlUi::Cpu and with VellumRmlUi::Metal in one process, and
// requires each pair of frames within the renderer tolerance (image_compare.hpp).
// The CPU renderer decodes images through ImageIO here, as the Metal renderer
// does, so both draw the same texels.
//
// Built twice: as the test, and with VELLUM_RMLUI_CPU_TEST_STRAIGHT_ALPHA_BLEND
// as the control, whose CPU renderer blends as if colours were straight alpha.
// The control requires the translucent document to fall outside the
// tolerance, which shows the comparison can see a blending fault.
#import <vellum/rmlui/metal_render_interface.hpp>
#include <vellum/rmlui/cpu_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "visual_common.hpp"

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <dispatch/dispatch.h>

#include <RmlUi/Core.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unistd.h>
#include <vector>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

namespace {

class Watchdog final {
  public:
    Watchdog() {
        timer_ = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                        dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
        dispatch_source_set_timer(timer_, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC),
                                  DISPATCH_TIME_FOREVER, 0);
        dispatch_source_set_event_handler(timer_, ^{
            std::fprintf(stderr, "watchdog: stuck in %s\n", stage_.load());
            std::fflush(stderr);
            _exit(3);
        });
        dispatch_resume(timer_);
    }
    ~Watchdog() { dispatch_source_cancel(timer_); }
    void stage(const char* name) { stage_.store(name); }

  private:
    std::atomic<const char*> stage_{"startup"};
    dispatch_source_t timer_ = nullptr;
};

constexpr int kSkip = 77;

#if defined(VELLUM_RMLUI_CPU_TEST_STRAIGHT_ALPHA_BLEND)
constexpr bool kControl = true;
#else
constexpr bool kControl = false;
#endif

bool load_fonts(DirectoryAssets& assets, std::vector<std::string>& storage) {
    for (const auto& font : kFonts) {
        Asset asset;
        if (assets.load(font.path, asset) != Status::ok)
            return false;
        storage.push_back(std::move(asset.bytes));
        const auto& bytes = storage.back();
        if (!Rml::LoadFontFace(
                Rml::Span<const Rml::byte>(reinterpret_cast<const Rml::byte*>(bytes.data()),
                                           bytes.size()),
                "LatoLatin", font.italic ? Rml::Style::FontStyle::Italic : Rml::Style::FontStyle::Normal,
                static_cast<Rml::Style::FontWeight>(font.weight)))
            return false;
    }
    return true;
}

// Premultiplied RGBA8 through ImageIO, top row first.
bool decode(const std::vector<Rml::byte>& encoded, std::uint32_t& width, std::uint32_t& height,
            std::vector<std::uint8_t>& rgba) {
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
    bool ok = width > 0 && height > 0;
    if (ok) {
        rgba.assign(static_cast<std::size_t>(width) * height * 4u, 0);
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(
            rgba.data(), width, height, 8, static_cast<std::size_t>(width) * 4u, space,
            static_cast<CGBitmapInfo>(kCGImageAlphaPremultipliedLast) | kCGBitmapByteOrderDefault);
        CGColorSpaceRelease(space);
        ok = context != nullptr;
        if (ok) {
            CGContextDrawImage(context, CGRectMake(0, 0, width, height), image);
            CGContextRelease(context);
        }
    }
    CGImageRelease(image);
    return ok;
}

bool render_metal(id<MTLDevice> device, id<MTLCommandQueue> queue, MetalRenderInterface& renderer,
                  NativeContext& native, const char* document, Watchdog& watchdog,
                  std::vector<std::uint8_t>& pixels) {
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:kWidth
                                                          height:kHeight
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
    if (!target)
        return false;
    for (int pass = 0; pass < 2; ++pass) { // a second pass, after layout settles
        watchdog.stage("render");
        std::fprintf(stderr, "progress: render %s Metal pass %d\n", document, pass + 1);
        std::fflush(stderr);
        id<MTLCommandBuffer> commands = [queue commandBuffer];
        MetalFrameTarget frame;
        frame.command_buffer = commands;
        frame.target = target;
        frame.viewport = {0.0, 0.0, double(kWidth), double(kHeight), 0.0, 1.0};
        frame.scale = 1.0f;
        renderer.begin_frame(frame);
        native.update();
        native.render();
        renderer.end_frame();
        [commands commit];
        watchdog.stage("command buffer completion");
        [commands waitUntilCompleted];
        if (commands.status != MTLCommandBufferStatusCompleted)
            return false;
    }
    pixels.assign(static_cast<std::size_t>(kWidth) * kHeight * 4u, 0);
    watchdog.stage("readback");
    std::fprintf(stderr, "progress: readback %s Metal\n", document);
    std::fflush(stderr);
    [target getBytes:pixels.data()
         bytesPerRow:kWidth * 4u
          fromRegion:MTLRegionMake2D(0, 0, kWidth, kHeight)
         mipmapLevel:0];
    return true;
}

bool render_cpu(CpuRenderInterface& renderer, NativeContext& native, const char* document,
                Watchdog& watchdog, std::vector<std::uint8_t>& pixels) {
    pixels.assign(static_cast<std::size_t>(kWidth) * kHeight * 4u, 0);
    for (int pass = 0; pass < 2; ++pass) {
        watchdog.stage("render");
        std::fprintf(stderr, "progress: render %s CPU pass %d\n", document, pass + 1);
        std::fflush(stderr);
        CpuFrameTarget frame;
        frame.rgba = pixels.data();
        frame.width = kWidth;
        frame.height = kHeight;
        if (!renderer.begin_frame(frame))
            return false;
        native.update();
        native.render();
        renderer.end_frame();
    }
    return true;
}

bool has_ink(const std::vector<std::uint8_t>& pixels) {
    for (std::size_t i = 3; i < pixels.size(); i += 4)
        if (pixels[i] != 0)
            return true;
    return false;
}

} // namespace

int main(int argc, char** argv) {
    @autoreleasepool {
        Watchdog watchdog;
        if (argc != 3) {
            std::fprintf(stderr, "usage: %s <rmlui-source> <fixtures>\n", argv[0]);
            return 2;
        }
        if (!NativeContext::fonts_enabled()) {
            std::fprintf(stderr, "the comparison needs VELLUM_RMLUI_FONTS=ON\n");
            return 2;
        }
        watchdog.stage("Metal device creation");
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        std::fprintf(stderr, "progress: Metal device %s\n", device ? "created" : "missing");
        if (device)
            std::fprintf(stderr, "progress: Metal device name %s\n", [[device name] UTF8String]);
        std::fflush(stderr);
        if (!device) {
            std::printf("SKIP: no Metal device on this host\n");
            return kSkip;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];
        watchdog.stage("Metal renderer construction");
        MetalRenderInterface metal(device);
        std::fprintf(stderr, "progress: Metal renderer constructed\n");
        std::fflush(stderr);
        if (!metal.valid()) {
            std::fprintf(stderr, "the Metal renderer could not build its pipelines\n");
            return 1;
        }
        watchdog.stage("CPU renderer construction");
        CpuRenderInterface cpu;
        std::fprintf(stderr, "progress: CPU renderer constructed\n");
        std::fflush(stderr);
        cpu.set_image_decoder(decode);

        DirectoryAssets assets(argv[1], argv[2]);
        watchdog.stage("Native context construction");
        NativeContext keepalive(assets, cpu);
        std::fprintf(stderr, "progress: Native context constructed\n");
        std::fflush(stderr);
        std::vector<std::string> font_bytes;
        if (keepalive.open(1, 1) != Status::ok) {
            std::fprintf(stderr, "open failed\n");
            return 1;
        }
        {
            NativeContext::Scope scope(keepalive);
            if (!load_fonts(assets, font_bytes)) {
                std::fprintf(stderr, "fonts failed\n");
                return 1;
            }
        }

        std::vector<const char*> documents(std::begin(kDocuments), std::end(kDocuments));
        documents.push_back("fixtures/clip_layers.rml");
        int failures = 0;
        bool control_tripped = false;
        for (const char* document : documents) {
            std::vector<std::uint8_t> frames[2];
            const std::size_t metal_before = metal.unsupported_calls();
            const std::size_t cpu_before = cpu.unsupported_calls();
            for (int which = 0; which < 2; ++which) {
                NativeContext native(assets, which == 0 ? static_cast<Rml::RenderInterface&>(metal)
                                                        : static_cast<Rml::RenderInterface&>(cpu));
                watchdog.stage("context creation");
                if (native.open(kWidth, kHeight) != Status::ok ||
                    native.load_document(document) != Status::ok) {
                    std::fprintf(stderr, "document failed: %s\n", document);
                    return 1;
                }
                std::fprintf(stderr, "progress: document loaded %s\n", document);
                std::fflush(stderr);
                const bool rendered = which == 0
                    ? render_metal(device, queue, metal, native, document, watchdog, frames[0])
                    : render_cpu(cpu, native, document, watchdog, frames[1]);
                native.close();
                if (!rendered) {
                    std::fprintf(stderr, "%s render failed: %s\n", which == 0 ? "Metal" : "CPU", document);
                    return 1;
                }
            }
            if (metal.unsupported_calls() != metal_before || cpu.unsupported_calls() != cpu_before) {
                std::fprintf(stderr, "%s uses a feature one renderer does not draw: remove it here\n",
                             document);
                ++failures;
                continue;
            }
            // Two blank frames would match: require ink in both.
            if (!has_ink(frames[0]) || !has_ink(frames[1])) {
                std::fprintf(stderr, "%s: a frame is empty\n", document);
                ++failures;
                continue;
            }
            // VELLUM_RMLUI_CPU_DUMP=<dir> writes both frames, for inspecting a difference.
            if (const char* dump = std::getenv("VELLUM_RMLUI_CPU_DUMP")) {
                const auto name = reference_name(document);
                write_rgba(std::filesystem::path(dump) / ("metal_" + name), frames[0]);
                write_rgba(std::filesystem::path(dump) / ("cpu_" + name), frames[1]);
            }
            const auto d = compare_rgba(frames[1], frames[0]);
            const bool ok = within_renderer_tolerance(d);
            watchdog.stage("comparison");
            std::fprintf(stderr, "progress: comparison %s %s\n", document, ok ? "ok" : "outside tolerance");
            std::fflush(stderr);
            std::printf("%-60s mean=%.3f within=%.4f %s\n", document, d.mean, d.within,
                        ok ? "ok" : "OUTSIDE TOLERANCE");
#if defined(VELLUM_RMLUI_IOS_METAL_WRONG_EXPECTED_PIXEL)
            if (frames[0][0] != 255u) {
                std::fprintf(stderr, "negative control: expected pixel 255, got %u\n",
                             static_cast<unsigned>(frames[0][0]));
                return 1;
            }
#endif
            if (kControl) {
                if (translucent(document) && !ok)
                    control_tripped = true;
            } else if (!ok) {
                ++failures;
            }
        }
        keepalive.close();
        if (kControl) {
            if (!control_tripped) {
                std::fprintf(stderr, "control: a straight-alpha CPU blend stayed within tolerance on the "
                                     "translucent document, so the comparison cannot see blending faults\n");
                return 1;
            }
            std::printf("control: the broken blend was detected\n");
            return 0;
        }
        return failures == 0 ? 0 : 1;
    }
}
