// MetalRenderInterface::trim(): the cached render targets are released
// between frames and drawn again, to the same pixels, by the next frame. The
// document has layers, clip masks and filters, so the layer textures, the
// stencil and the filter targets are all in use.
//
// Checked: a frame leaves bytes cached (the control, so a renderer that never
// caches would show the test cannot see a release); trim() inside a frame
// releases nothing; trim() between frames returns what was cached and leaves
// none; trim() straight after a commit, with the frame still on the GPU, does
// not corrupt it; and the frame after a trim has the same pixels.
#import <vellum/rmlui/metal_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "visual_common.hpp"

#include <cstdio>
#include <string>
#include <vector>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

static_assert(VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM);

namespace {

constexpr int kSkip = 77;

#define CHECK(condition)                                                                 \
    do {                                                                                 \
        if (!(condition)) {                                                              \
            std::fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #condition); \
            return 1;                                                                    \
        }                                                                                \
    } while (false)

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
        CHECK(renderer.valid());
        DirectoryAssets assets(argv[1], argv[2]);
        NativeContext native(assets, renderer);
        CHECK(native.open(kWidth, kHeight) == Status::ok);
        CHECK(native.load_document("fixtures/layers_filters.rml") == Status::ok);

        MTLTextureDescriptor* descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                               width:kWidth
                                                              height:kHeight
                                                           mipmapped:NO];
        descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        descriptor.storageMode = MTLStorageModeShared;
        id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];

        const auto read = [&] {
            std::vector<std::uint8_t> pixels(static_cast<std::size_t>(kWidth) * kHeight * 4u);
            [target getBytes:pixels.data()
                 bytesPerRow:kWidth * 4u
                  fromRegion:MTLRegionMake2D(0, 0, kWidth, kHeight)
                 mipmapLevel:0];
            return pixels;
        };
        // One frame. With `wait` false the buffer is committed and left running.
        const auto draw = [&](bool wait, id<MTLCommandBuffer> __strong& out) {
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
            if (wait)
                [commands waitUntilCompleted];
            out = commands;
        };
        id<MTLCommandBuffer> commands = nil;

        CHECK(renderer.cached_target_bytes() == 0 && renderer.trim() == 0);
        draw(true, commands);
        CHECK(commands.status == MTLCommandBufferStatusCompleted);
        const std::vector<std::uint8_t> reference = read();
        CHECK(renderer.unsupported_calls() == 0);
        const std::size_t held = renderer.cached_target_bytes();
        // Control: the frame cached targets, at least one layer's worth.
        CHECK(held >= static_cast<std::size_t>(kWidth) * kHeight * 4u);

        // Inside a frame nothing is released.
        {
            MetalFrameTarget frame;
            frame.command_buffer = [queue commandBuffer];
            frame.target = target;
            frame.viewport = {0.0, 0.0, double(kWidth), double(kHeight), 0.0, 1.0};
            renderer.begin_frame(frame);
            CHECK(renderer.trim() == 0);
            CHECK(renderer.cached_target_bytes() >= held);
            renderer.end_frame();
            [frame.command_buffer commit];
            [frame.command_buffer waitUntilCompleted];
        }

        // Between frames it releases what is cached and says how much.
        const std::size_t cached = renderer.cached_target_bytes();
        CHECK(renderer.trim() == cached);
        CHECK(renderer.cached_target_bytes() == 0);
        CHECK(renderer.trim() == 0);

        // The next frame draws the same pixels.
        draw(true, commands);
        CHECK(read() == reference);
        CHECK(renderer.cached_target_bytes() >= held);

        // A trim straight after a commit, the frame still on the GPU, leaves
        // that frame intact: the command buffer holds its resources.
        for (int round = 0; round < 8; ++round) {
            draw(false, commands);
            CHECK(renderer.trim() > 0);
            [commands waitUntilCompleted];
            CHECK(commands.status == MTLCommandBufferStatusCompleted);
            CHECK(read() == reference);
        }
        native.close();
        return 0;
    }
}
