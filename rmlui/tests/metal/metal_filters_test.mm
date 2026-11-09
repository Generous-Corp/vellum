// Filters, mask images and gradient shaders drawn by VellumRmlUi::Metal,
// headless into an offscreen texture and checked at pixels whose colour the
// document and the filter's arithmetic fix. Each probe reads differently if
// its feature is dropped: an unfiltered box keeps its own colour, a missing
// gradient leaves the body colour, a missing blur leaves a hard edge.
#import <vellum/rmlui/metal_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "visual_common.hpp"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <vector>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

namespace {

constexpr int kSkip = 77;

// Expected RGB with a tolerance, or (range) each channel within [lo, hi].
struct Probe {
    const char* what;
    int x, y;
    int r, g, b;
    int tolerance;
    bool range = false;
    int r_hi = 0, g_hi = 0, b_hi = 0;
};

// Body #1b1e24 = (27, 30, 36).
const Probe kProbes[] = {
    // linear-gradient(to right, red, blue) over 96px: t = 0.255 at x = 32,
    // eased by smoothstep to 0.162, so (214, 0, 41); linear interpolation
    // would give (190, 0, 65).
    {"linear gradient, left end", 9, 20, 255, 0, 0, 8},
    {"linear gradient, quarter (eased)", 32, 20, 214, 0, 41, 8},
    {"linear gradient, middle", 56, 20, 126, 0, 129, 8},
    {"linear gradient, right end", 102, 20, 0, 0, 255, 8},
    {"radial gradient, centre", 135, 31, 0, 254, 0, 8},
    {"radial gradient, corner", 113, 9, 0, 0, 0, 8},
    // Conic from the top, clockwise: just right of the top is the first stop.
    {"conic gradient, just clockwise of the top", 194, 12, 255, 0, 0, 12},
    {"conic gradient, just anticlockwise of the top", 189, 12, 0, 0, 255, 12},
    {"opacity(0.5) of white over the body", 20, 76, 141, 142, 145, 8},
    {"grayscale(1) of red", 52, 76, 54, 54, 54, 8},
    {"invert(1) of red", 84, 76, 0, 255, 255, 8},
    {"hue-rotate(180deg) of red", 116, 76, 0, 109, 109, 8},
    {"brightness(0.5) of white", 148, 76, 128, 128, 128, 8},
    {"contrast(0.5) of white", 180, 76, 191, 191, 191, 8},
    {"sepia(1) of white", 212, 76, 255, 255, 239, 8},
    {"blur(4px): the middle stays white", 24, 144, 245, 245, 245, 0, true, 255, 255, 255},
    {"blur(4px): just outside the edge is between", 42, 144, 50, 50, 55, 0, true, 200, 200, 205},
    {"blur(4px): far outside is the body", 64, 144, 27, 30, 36, 8},
    {"drop-shadow: the box", 78, 134, 255, 255, 255, 8},
    {"drop-shadow: the shadow past the box", 102, 158, 0, 0, 255, 8},
    {"drop-shadow: beside the shadow", 102, 134, 27, 30, 36, 8},
    {"blurred box-shadow: inside the shadow", 168, 140, 0, 0, 180, 0, true, 60, 60, 255},
    {"blurred box-shadow: its soft edge", 174, 140, 15, 15, 60, 0, true, 40, 40, 200},
    {"mask-image: opaque end", 194, 144, 255, 255, 255, 8},
    {"mask-image: transparent end", 238, 144, 27, 30, 36, 8},
    {"backdrop-filter: invert(1) of the body", 32, 216, 228, 225, 219, 8},
};

bool check(const std::uint8_t* px, const Probe& p) {
    if (px[3] < 247)
        return false;
    if (p.range)
        return px[0] >= p.r && px[0] <= p.r_hi && px[1] >= p.g && px[1] <= p.g_hi &&
               px[2] >= p.b && px[2] <= p.b_hi;
    const auto near = [&](int a, int b) { return std::abs(a - b) <= p.tolerance; };
    return near(px[0], p.r) && near(px[1], p.g) && near(px[2], p.b);
}

} // namespace

int main(int argc, char** argv) {
    @autoreleasepool {
        if (argc != 3) {
            std::fprintf(stderr, "usage: %s <rmlui-source> <fixtures>\n", argv[0]);
            return 2;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            std::printf("SKIP: no Metal device on this host\n");
            return kSkip;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];
        MetalRenderInterface renderer(device);
        if (!renderer.valid())
            return 1;
        DirectoryAssets assets(argv[1], argv[2]);
        NativeContext native(assets, renderer);
        if (native.open(kWidth, kHeight) != Status::ok ||
            native.load_document("fixtures/filters_shaders.rml") != Status::ok)
            return 1;

        MTLTextureDescriptor* descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                               width:kWidth
                                                              height:kHeight
                                                           mipmapped:NO];
        descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        descriptor.storageMode = MTLStorageModeShared;
        id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
        for (int pass = 0; pass < 2; ++pass) {
            id<MTLCommandBuffer> commands = [queue commandBuffer];
            MetalFrameTarget frame;
            frame.command_buffer = commands;
            frame.target = target;
            frame.viewport = {0.0, 0.0, double(kWidth), double(kHeight), 0.0, 1.0};
            renderer.begin_frame(frame);
            native.update();
            native.render();
            renderer.end_frame();
            [commands commit];
            [commands waitUntilCompleted];
            if (commands.status != MTLCommandBufferStatusCompleted) {
                std::fprintf(stderr, "command buffer failed: %s\n",
                             commands.error.localizedDescription.UTF8String);
                return 1;
            }
        }
        native.close();
        std::vector<std::uint8_t> pixels(static_cast<std::size_t>(kWidth) * kHeight * 4u);
        [target getBytes:pixels.data()
             bytesPerRow:kWidth * 4u
              fromRegion:MTLRegionMake2D(0, 0, kWidth, kHeight)
             mipmapLevel:0];
        if (const char* dump = std::getenv("VELLUM_RMLUI_METAL_DUMP"))
            write_rgba(std::filesystem::path(dump) / "filters_shaders.rgba", pixels);

        int failures = 0;
        if (renderer.unsupported_calls() != 0) {
            std::fprintf(stderr, "the fixture used %zu calls this renderer does not draw\n",
                         renderer.unsupported_calls());
            ++failures;
        }
        for (const auto& probe : kProbes) {
            const auto* px = &pixels[(static_cast<std::size_t>(probe.y) * kWidth + probe.x) * 4u];
            const bool ok = check(px, probe);
            std::printf("%-50s (%3d,%3d) = %3d %3d %3d %3d %s\n", probe.what, probe.x, probe.y,
                        px[0], px[1], px[2], px[3], ok ? "ok" : "WRONG");
            failures += ok ? 0 : 1;
        }
        return failures == 0 ? 0 : 1;
    }
}
