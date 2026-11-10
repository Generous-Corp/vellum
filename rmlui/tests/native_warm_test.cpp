// NativeContext::warm() with the CPU renderer: after warm(), the first real
// frame loads no texture and gives the same bytes as a first frame drawn
// cold. The cold frame is the control: it must load a texture, so a frame that
// loaded nothing would show the test cannot see a load.
//
// RmlUi loads an <img> element's texture during layout, to learn its size, so
// that one loads in load_document() either way; a decorator's image loads
// only when first drawn, which is the load warm() moves.
#include <vellum/rmlui/cpu_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#include <RmlUi/Core.h>

#include <cstdint>
#include <cstdio>
#include <map>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kSize = 32;

class MapAssets final : public AssetProvider {
  public:
    void add(std::string path, std::string bytes) {
        assets_[std::move(path)] = std::move(bytes);
    }
    Status load(std::string_view path, Asset& out) override {
        const auto found = assets_.find(std::string(path));
        if (found == assets_.end())
            return Status::missing_asset;
        out.path = found->first;
        out.bytes = found->second;
        return Status::ok;
    }

  private:
    std::map<std::string, std::string> assets_;
};

// Two images, as an element and as a decorator, over a fill.
constexpr const char* kDocument =
    "<rml><head><style>"
    "body { display: block; width: 32px; height: 32px; background-color: #203040; }"
    "img { display: block; width: 16px; height: 16px; }"
    "#tile { display: block; width: 16px; height: 8px; decorator: image(tile.png); }"
    "</style></head><body><img src='face.png'/><div id='tile'/></body></rml>";

struct Result {
    int loads_before_frame = 0; // decodes before the first real frame
    int loads_in_frame = 0;     // decodes during it
    std::vector<std::uint8_t> frame;
};

Result first_frame(bool warm) {
    MapAssets assets;
    assets.add("doc.rml", kDocument);
    // Any bytes: the decoder below ignores them.
    assets.add("face.png", "face");
    assets.add("tile.png", "tile");
    CpuRenderInterface renderer;
    int decodes = 0;
    renderer.set_image_decoder([&](const std::vector<Rml::byte>& encoded, std::uint32_t& width,
                                   std::uint32_t& height, std::vector<std::uint8_t>& rgba) {
        ++decodes;
        width = 2;
        height = 2;
        const std::uint8_t shade = encoded.size() == 4 && encoded[0] == 'f' ? 200 : 90;
        rgba = {shade, 0, 0, 255, 0, shade, 0, 255, 0, 0, shade, 255, shade, shade, shade, 255};
        return true;
    });
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.open(kSize, kSize) == Status::ok);
    VELLUM_RMLUI_CHECK(native.load_document("doc.rml") == Status::ok);
    if (warm)
        native.warm();
    Result result;
    result.loads_before_frame = decodes;
    result.frame.assign(kSize * kSize * 4, 0);
    CpuFrameTarget frame;
    frame.rgba = result.frame.data();
    frame.width = kSize;
    frame.height = kSize;
    VELLUM_RMLUI_CHECK(renderer.begin_frame(frame));
    native.update();
    native.render();
    renderer.end_frame();
    result.loads_in_frame = decodes - result.loads_before_frame;
    native.close();
    return result;
}

} // namespace

int main() {
    // warm() before a context is open does nothing.
    {
        MapAssets assets;
        CpuRenderInterface renderer;
        NativeContext native(assets, renderer);
        native.warm();
        VELLUM_RMLUI_CHECK(!native.is_open());
    }
    const Result cold = first_frame(false);
    const Result warm = first_frame(true);
    std::printf("cold: %d decodes in the first frame; warm: %d before it, %d in it\n", cold.loads_in_frame,
                warm.loads_before_frame, warm.loads_in_frame);
    // Control: drawn cold, the first frame loads the decorator's image.
    VELLUM_RMLUI_CHECK(cold.loads_in_frame >= 1);
    // Warmed, everything loads before the frame and the frame loads none.
    VELLUM_RMLUI_CHECK(warm.loads_in_frame == 0);
    VELLUM_RMLUI_CHECK(warm.loads_before_frame == cold.loads_before_frame + cold.loads_in_frame);
    // The images were drawn, and warming changed no byte.
    bool image_ink = false;
    for (std::size_t i = 0; i < cold.frame.size(); i += 4)
        image_ink = image_ink || cold.frame[i] == 200;
    VELLUM_RMLUI_CHECK(image_ink);
    VELLUM_RMLUI_CHECK(warm.frame == cold.frame);
    return 0;
}
