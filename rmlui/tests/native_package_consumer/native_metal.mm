// Links VellumRmlUi::Native and VellumRmlUi::Metal from the VellumRmlUiNative
// package: Native draws a document through the Metal render interface into an
// offscreen texture, read back as opaque red. Core is linked by a separate
// executable, since Core and Native each carry their own RmlUi.
#include <vellum/rmlui/metal_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#import <Metal/Metal.h>

#include <cstdint>
#include <cstdio>
#include <string>

namespace {

constexpr const char* kDocument =
    "<rml><body style='display:block;width:16px;height:16px;"
    "background-color:#ff0000;'></body></rml>";

struct Assets final : vellum::rmlui::AssetProvider {
    vellum::rmlui::Status load(std::string_view path, vellum::rmlui::Asset& out) override {
        if (path != "editor.rml")
            return vellum::rmlui::Status::missing_asset;
        out.path = std::string(path);
        out.bytes = kDocument;
        return vellum::rmlui::Status::ok;
    }
};

int native_through_metal() {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) {
        std::printf("no Metal device\n");
        return 1;
    }
    vellum::rmlui::MetalRenderInterface metal(device);
    if (!metal.valid())
        return 1;
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:16
                                                          height:16
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
    id<MTLCommandQueue> queue = [device newCommandQueue];

    Assets assets;
    vellum::rmlui::NativeContext native(assets, metal);
    if (native.open(16, 16) != vellum::rmlui::Status::ok ||
        native.load_document("editor.rml") != vellum::rmlui::Status::ok)
        return 1;
    native.update();
    id<MTLCommandBuffer> commands = [queue commandBuffer];
    vellum::rmlui::MetalFrameTarget frame;
    frame.command_buffer = commands;
    frame.target = target;
    frame.viewport = MTLViewport{0, 0, 16, 16, 0, 1};
    metal.begin_frame(frame);
    native.render();
    metal.end_frame();
    [commands commit];
    [commands waitUntilCompleted];
    native.close();

    std::uint8_t pixel[4] = {};
    [target getBytes:pixel bytesPerRow:16 * 4 fromRegion:MTLRegionMake2D(8, 8, 1, 1) mipmapLevel:0];
    if (pixel[0] != 255 || pixel[1] != 0 || pixel[2] != 0 || pixel[3] != 255) {
        std::printf("Metal drew %u,%u,%u,%u, not opaque red\n", pixel[0], pixel[1], pixel[2], pixel[3]);
        return 1;
    }
    std::printf("installed VellumRmlUi::Native drew through VellumRmlUi::Metal\n");
    return 0;
}

} // namespace

int main() {
    @autoreleasepool {
        return native_through_metal();
    }
}
