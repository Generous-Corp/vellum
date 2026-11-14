// Renders the visual-test documents with VellumRmlUi::Metal, headless into an
// offscreen MTLTexture, and compares each frame with the soft renderer's
// reference (soft_reference.cpp) within a stated tolerance.
//
// Built twice: as the test, which requires every document within tolerance,
// and with VELLUM_RMLUI_METAL_TEST_STRAIGHT_ALPHA_BLEND as the control, whose
// renderer blends as if colours were straight alpha. The control requires the
// translucent document to fall outside the tolerance, which shows the
// comparison can see a blending fault.
#import <vellum/rmlui/metal_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "visual_common.hpp"

#include <RmlUi/Core.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using namespace vellum::rmlui;
using namespace vellum::rmlui::metal_test;

namespace {

constexpr int kSkip = 77;

#if defined(VELLUM_RMLUI_METAL_TEST_STRAIGHT_ALPHA_BLEND)
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

bool render(id<MTLDevice> device, id<MTLCommandQueue> queue, MetalRenderInterface& renderer,
            NativeContext& native, float scale, bool switch_to_nearest,
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
        if (switch_to_nearest && pass == 1)
            renderer.set_atlas_filter(MetalRenderInterface::AtlasFilter::nearest);
        id<MTLCommandBuffer> commands = [queue commandBuffer];
        MetalFrameTarget frame;
        frame.command_buffer = commands;
        frame.target = target;
        frame.viewport = {0.0, 0.0, double(kWidth), double(kHeight), 0.0, 1.0};
        frame.scale = scale;
        renderer.begin_frame(frame);
        native.update();
        native.render();
        renderer.end_frame();
        [commands commit];
        [commands waitUntilCompleted];
        if (commands.status != MTLCommandBufferStatusCompleted)
            return false;
    }
    pixels.assign(static_cast<std::size_t>(kWidth) * kHeight * 4u, 0);
    [target getBytes:pixels.data()
         bytesPerRow:kWidth * 4u
          fromRegion:MTLRegionMake2D(0, 0, kWidth, kHeight)
         mipmapLevel:0];
    return true;
}

} // namespace

int main(int argc, char** argv) {
    @autoreleasepool {
        if (argc != 4) {
            std::fprintf(stderr, "usage: %s <rmlui-source> <fixtures> <reference-dir>\n", argv[0]);
            return 2;
        }
        if (!NativeContext::fonts_enabled()) {
            std::fprintf(stderr, "the visual test needs VELLUM_RMLUI_FONTS=ON\n");
            return 2;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            std::printf("SKIP: no Metal device on this host\n");
            return kSkip;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];
        // Cold-open cost: the shader library compiles at construction and the
        // pipelines at the first frame. Reported, not gated.
        const auto compile_start = std::chrono::steady_clock::now();
        MetalRenderInterface renderer(device);
        const double library_ms = std::chrono::duration<double, std::milli>(
                                      std::chrono::steady_clock::now() - compile_start)
                                      .count();
        double first_frame_ms = -1.0;
        if (!renderer.valid()) {
            std::fprintf(stderr, "the Metal renderer could not build its pipelines\n");
            return 1;
        }
        if (renderer.atlas_filter() != MetalRenderInterface::AtlasFilter::linear) {
            std::fprintf(stderr, "Metal renderer default atlas filter changed\n");
            return 1;
        }
        DirectoryAssets assets(argv[1], argv[2]);
        const std::filesystem::path references(argv[3]);
        // Holds the shared RmlUi runtime, and with it the font faces, open
        // across every document; each document gets a fresh context.
        NativeContext keepalive(assets, renderer);
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
        int failures = 0;
        bool control_tripped = false;
        for (const char* document : kDocuments) {
            NativeContext native(assets, renderer);
            const auto scale = atlas_filter_document(document) ? 2.0f : 1.0f;
            if (native.set_density_ratio(scale) != Status::ok ||
                native.open(static_cast<std::uint32_t>(kWidth / scale),
                            static_cast<std::uint32_t>(kHeight / scale)) != Status::ok) {
                std::fprintf(stderr, "open failed\n");
                return 1;
            }
            if (native.load_document(document) != Status::ok) {
                std::fprintf(stderr, "document failed: %s\n", document);
                return 1;
            }
            const auto unsupported_before = renderer.unsupported_calls();
            std::vector<std::uint8_t> metal;
            const auto frame_start = std::chrono::steady_clock::now();
            const bool rendered = render(device, queue, renderer, native, scale,
                                         atlas_filter_document(document), metal);
            if (first_frame_ms < 0.0)
                first_frame_ms = std::chrono::duration<double, std::milli>(
                                     std::chrono::steady_clock::now() - frame_start)
                                     .count();
            if (!rendered) {
                std::fprintf(stderr, "Metal render failed: %s\n", document);
                return 1;
            }
            native.close();
            if (renderer.unsupported_calls() != unsupported_before) {
                std::fprintf(stderr,
                             "%s uses clip masks, layers, filters or shaders, which this "
                             "renderer version does not draw: remove it from kDocuments\n",
                             document);
                ++failures;
                continue;
            }
            // A blank frame would match a blank reference: require ink.
            bool drew = false;
            for (std::size_t i = 3; i < metal.size() && !drew; i += 4)
                drew = metal[i] != 0;
            if (!drew) {
                std::fprintf(stderr, "%s: the Metal frame is empty\n", document);
                ++failures;
                continue;
            }
            // VELLUM_RMLUI_METAL_DUMP=<dir> writes the Metal frames beside the
            // references, for inspecting a difference.
            if (const char* dump = std::getenv("VELLUM_RMLUI_METAL_DUMP"))
                write_rgba(std::filesystem::path(dump) / reference_name(document), metal);
            std::vector<std::uint8_t> soft;
            if (!read_rgba(references / reference_name(document), soft)) {
                std::fprintf(stderr, "missing reference for %s\n", document);
                return 1;
            }
            const auto d = compare_rgba(metal, soft);
            const bool ok = within_renderer_tolerance(d);
            std::printf("%-60s mean=%.3f within=%.4f %s\n", document, d.mean, d.within,
                        ok ? "ok" : "OUTSIDE TOLERANCE");
            if (kControl) {
                if (translucent(document) && !ok)
                    control_tripped = true;
            } else if (!ok) {
                ++failures;
            }
        }
        keepalive.close();
        std::printf("cold open: shader library %.2f ms; first document (pipelines + 2 frames) %.2f ms\n",
                    library_ms, first_frame_ms);
        if (kControl) {
            if (!control_tripped) {
                std::fprintf(stderr, "control: a straight-alpha blend stayed within tolerance on "
                                     "the translucent document, so the comparison cannot see "
                                     "blending faults\n");
                return 1;
            }
            std::printf("control: the broken blend was detected\n");
            return 0;
        }
        return failures == 0 ? 0 : 1;
    }
}
