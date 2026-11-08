// Clip masks and layers drawn by VellumRmlUi::Metal, headless into an
// offscreen texture, checked at pixels whose colour the document fixes. The
// soft renderer cannot serve as the reference here: it has no masks or
// layers. A probe that reads the clipped or composited colour fails if masks
// are ignored (the corners show the child) or layers are dropped (no shadow).
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

struct Probe {
    const char* what;
    int x, y;
    std::uint8_t r, g, b;
};

// Body #1b1e24; #round is a circle at (16,16) d=80 holding red; #outer is a
// circle at (112,16) d=80 holding #inner, a 20px-radius box starting 40px in,
// holding green; #shadowed at (24,112) 32x24 casts a blue shadow 12,8 away.
constexpr Probe kProbes[] = {
    {"inside the clipped circle", 56, 56, 255, 0, 0},
    {"circle bounding-box corner, outside the circle", 19, 19, 0x1b, 0x1e, 0x24},
    {"intersected clips: inside both", 168, 56, 0, 255, 0},
    {"intersected clips: inside inner box, outside outer circle", 187, 22, 0x1b, 0x1e, 0x24},
    {"intersected clips: inside outer circle, outside inner box", 140, 56, 0x1b, 0x1e, 0x24},
    {"the shadowed box", 30, 118, 0xf2, 0xf2, 0xf2},
    {"the shadow, past the box", 64, 132, 0, 0, 255},
    {"beside the shadow", 64, 108, 0x1b, 0x1e, 0x24},
};

bool close_to(const std::uint8_t* px, const Probe& p) {
    const auto near = [](int a, int b) { return std::abs(a - b) <= 8; };
    return near(px[0], p.r) && near(px[1], p.g) && near(px[2], p.b) && px[3] >= 247;
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
            native.load_document("fixtures/clip_layers.rml") != Status::ok)
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
            write_rgba(std::filesystem::path(dump) / "clip_layers.rgba", pixels);

        if (renderer.unsupported_calls() != 0) {
            std::fprintf(stderr, "the fixture used %zu calls this renderer does not draw\n",
                         renderer.unsupported_calls());
            return 1;
        }
        int failures = 0;
        for (const auto& probe : kProbes) {
            const auto* px = &pixels[(static_cast<std::size_t>(probe.y) * kWidth + probe.x) * 4u];
            const bool ok = close_to(px, probe);
            std::printf("%-58s (%3d,%3d) = %3d %3d %3d %3d %s\n", probe.what, probe.x, probe.y, px[0],
                        px[1], px[2], px[3], ok ? "ok" : "WRONG");
            failures += ok ? 0 : 1;
        }
        return failures == 0 ? 0 : 1;
    }
}
