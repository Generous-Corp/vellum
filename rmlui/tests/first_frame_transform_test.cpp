// An element that becomes visible draws its first frame with every
// transform it inherits. Each case renders a frame, shows an element, and
// renders again; that frame must equal one drawn with the element shown from
// the start. A host that repaints only on change keeps the first frame on
// screen, so a wrong first frame stays wrong.
//
// The cases include upstream RmlUi issue 982's shape (a positioned,
// transformed window with a hidden child that is then shown), which RmlUi
// fixed in 6df503cf, and two ways a host can disturb the frame: asking an
// element for its absolute offset during the render pass, and a renderer that
// has no layers drawing a box shadow.
#include <vellum/rmlui/cpu_render_interface.hpp>
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#include <RmlUi/Core.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <map>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

using namespace vellum::rmlui;

namespace {

constexpr std::uint32_t kSize = 64;

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

// Forwards to a CpuRenderInterface. Optionally asks an element for its
// absolute offset from inside the render pass, or reports no layer support.
class Forwarding final : public Rml::RenderInterface {
  public:
    explicit Forwarding(CpuRenderInterface& to) : to_(to) {}
    std::function<void()> on_draw;
    bool no_layers = false;

    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex> v, Rml::Span<const int> i) override {
        return to_.CompileGeometry(v, i);
    }
    void RenderGeometry(Rml::CompiledGeometryHandle g, Rml::Vector2f t, Rml::TextureHandle x) override {
        if (on_draw)
            on_draw();
        to_.RenderGeometry(g, t, x);
    }
    void ReleaseGeometry(Rml::CompiledGeometryHandle g) override { to_.ReleaseGeometry(g); }
    Rml::TextureHandle LoadTexture(Rml::Vector2i& d, const Rml::String& s) override { return to_.LoadTexture(d, s); }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte> s, Rml::Vector2i d) override {
        return to_.GenerateTexture(s, d);
    }
    void ReleaseTexture(Rml::TextureHandle t) override { to_.ReleaseTexture(t); }
    void EnableScissorRegion(bool e) override { to_.EnableScissorRegion(e); }
    void SetScissorRegion(Rml::Rectanglei r) override { to_.SetScissorRegion(r); }
    void EnableClipMask(bool e) override { to_.EnableClipMask(e); }
    void RenderToClipMask(Rml::ClipMaskOperation o, Rml::CompiledGeometryHandle g, Rml::Vector2f t) override {
        to_.RenderToClipMask(o, g, t);
    }
    void SetTransform(const Rml::Matrix4f* m) override { to_.SetTransform(m); }
    Rml::LayerHandle PushLayer() override { return no_layers ? 0 : to_.PushLayer(); }
    void CompositeLayers(Rml::LayerHandle s, Rml::LayerHandle d, Rml::BlendMode b,
                         Rml::Span<const Rml::CompiledFilterHandle> f) override {
        if (!no_layers)
            to_.CompositeLayers(s, d, b, f);
    }
    void PopLayer() override {
        if (!no_layers)
            to_.PopLayer();
    }
    Rml::TextureHandle SaveLayerAsTexture() override { return no_layers ? 0 : to_.SaveLayerAsTexture(); }
    Rml::CompiledFilterHandle SaveLayerAsMaskImage() override { return to_.SaveLayerAsMaskImage(); }
    Rml::CompiledFilterHandle CompileFilter(const Rml::String& n, const Rml::Dictionary& p) override {
        return to_.CompileFilter(n, p);
    }
    void ReleaseFilter(Rml::CompiledFilterHandle f) override { to_.ReleaseFilter(f); }
    Rml::CompiledShaderHandle CompileShader(const Rml::String& n, const Rml::Dictionary& p) override {
        return to_.CompileShader(n, p);
    }
    void RenderShader(Rml::CompiledShaderHandle s, Rml::CompiledGeometryHandle g, Rml::Vector2f t,
                      Rml::TextureHandle x) override {
        to_.RenderShader(s, g, t, x);
    }
    void ReleaseShader(Rml::CompiledShaderHandle s) override { to_.ReleaseShader(s); }

  private:
    CpuRenderInterface& to_;
};

std::vector<std::uint8_t> frame(CpuRenderInterface& renderer, NativeContext& native) {
    std::vector<std::uint8_t> pixels(kSize * kSize * 4, 0);
    CpuFrameTarget target;
    target.rgba = pixels.data();
    target.width = kSize;
    target.height = kSize;
    VELLUM_RMLUI_CHECK(renderer.begin_frame(target));
    native.update();
    native.render();
    renderer.end_frame();
    return pixels;
}


// The pixels' bounding box, as "x0,y0-x1,y1".
std::string box(const std::vector<std::uint8_t>& pixels) {
    int x0 = kSize, y0 = kSize, x1 = -1, y1 = -1;
    for (int y = 0; y < static_cast<int>(kSize); ++y)
        for (int x = 0; x < static_cast<int>(kSize); ++x)
            if (pixels[(static_cast<std::size_t>(y) * kSize + static_cast<std::size_t>(x)) * 4u + 3u]) {
                x0 = std::min(x0, x); y0 = std::min(y0, y); x1 = std::max(x1, x); y1 = std::max(y1, y);
            }
    return x1 < 0 ? "empty" : std::to_string(x0) + "," + std::to_string(y0) + "-" + std::to_string(x1) + "," +
                                  std::to_string(y1);
}

// Where the red pixels are: the shown element is the only red thing drawn.
std::vector<bool> red(const std::vector<std::uint8_t>& pixels) {
    std::vector<bool> out(kSize * kSize);
    for (std::size_t i = 0; i < out.size(); ++i)
        out[i] = pixels[i * 4] > 200 && pixels[i * 4 + 1] < 50 && pixels[i * 4 + 2] < 50;
    return out;
}

// Renders before showing, shows, renders frame 1 and 2. The reference shows
// the element before the first frame is ever drawn, on a plain renderer.
// Frame 1 must equal it; where the case disturbs the host (a mid-render
// offset read, no layers), only the shown element's red pixels are compared.
struct Options {
    bool offset_during_render = false; // ask the shown element's offset from inside the render pass
    bool no_layers = false;
};

bool run(const char* name, const std::string& document, const std::function<void(Rml::ElementDocument&)>& show,
         Options options = {}) {
    std::vector<std::uint8_t> first, second, reference;
    for (int pass = 0; pass < 2; ++pass) {
        MapAssets assets;
        assets.add("doc.rml", document);
        CpuRenderInterface renderer;
        Forwarding forwarding(renderer);
        forwarding.no_layers = pass == 0 && options.no_layers;
        NativeContext native(assets, forwarding);
        VELLUM_RMLUI_CHECK(native.open(kSize, kSize) == Status::ok);
        VELLUM_RMLUI_CHECK(native.load_document("doc.rml") == Status::ok);
        if (pass == 0 && options.offset_during_render)
            forwarding.on_draw = [&native] {
                for (const char* id : {"box", "pane_display"})
                    if (auto* element = native.document()->GetElementById(id))
                        (void)element->GetAbsoluteOffset(Rml::BoxArea::Border);
            };
        if (pass == 0) {
            frame(renderer, native);
            {
                NativeContext::Scope scope(native);
                show(*native.document());
            }
            first = frame(renderer, native);
            second = frame(renderer, native);
        } else {
            {
                NativeContext::Scope scope(native);
                show(*native.document());
            }
            reference = frame(renderer, native);
        }
        native.close();
    }
    const bool disturbed = options.offset_during_render || options.no_layers;
    const bool ok = disturbed ? red(first) == red(reference) && red(second) == red(reference) : first == reference;
    std::printf("%-44s frame 1 %-12s frame 2 %-12s reference %-12s %s\n", name, box(first).c_str(),
                box(second).c_str(), box(reference).c_str(), ok ? "ok" : "FIRST FRAME DIFFERS");
    return ok;
}

const std::string kHead = "<rml><head><style>body { display: block; width: 64px; height: 64px; }"
                          "#box { width: 8px; height: 8px; background-color: #ff0000; "
                          "position: absolute; left: 28px; top: 28px; }";

} // namespace

int main() {
    int failures = 0;
    failures += !run("display none -> block, scale(4)",
        kHead + "#box { transform: scale(4); display: none; }</style></head><body><div id='box'/></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("box")->SetProperty("display", "block"); });
    failures += !run("visibility hidden -> visible, scale(4)",
        kHead + "#box { transform: scale(4); visibility: hidden; }</style></head><body><div id='box'/></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("box")->SetProperty("visibility", "visible"); });
    failures += !run("class adds scale(4)",
        kHead + "#box.big { transform: scale(4); }</style></head><body><div id='box'/></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("box")->SetClass("big", true); });
    failures += !run("parent shown, child scale(4)",
        kHead + "#wrap { display: none; } #box { transform: scale(4); }</style></head>"
                "<body><div id='wrap'><div id='box'/></div></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("wrap")->SetProperty("display", "block"); });
    failures += !run("parent scale(4) shown, child plain",
        kHead + "#wrap { display: none; transform: scale(4); width: 64px; height: 64px; }</style></head>"
                "<body><div id='wrap'><div id='box'/></div></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("wrap")->SetProperty("display", "block"); });
    failures += !run("translate(100%) shown",
        kHead + "#box { transform: translate(100%, 100%); display: none; }</style></head>"
                "<body><div id='box'/></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("box")->SetProperty("display", "block"); });
    failures += !run("scaled body, plain child shown",
        "<rml><head><style>body { display: block; width: 64px; height: 64px; transform: scale(0.5); }"
        "#box { display: none; width: 32px; height: 32px; background-color: #ff0000; }"
        "</style></head><body><div id='box'/></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("box")->SetProperty("display", "block"); });
    // Upstream RmlUi issue 982's shape: a positioned, transformed window that
    // is already drawn, with a hidden child that is then shown.
    const std::string window = "<rml><head><style>body { display: block; width: 64px; height: 64px; }"
                               "#window { position: absolute; left: 0; top: 0; width: 32px; height: 32px; "
                               "transform: translateX(32px); }"
                               "div.pane { display: block; width: 8px; height: 8px; background-color: #ff0000; }"
                               "#pane_display { display: none; } #pane_visibility { visibility: hidden; }";
    failures += !run("window transformed, child display shown",
        window + "</style></head><body><div id='window'><div class='pane' id='pane_display'/></div></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("pane_display")->SetProperty("display", "block"); });
    failures += !run("window transformed, child visibility shown",
        window + "</style></head><body><div id='window'><div class='pane' id='pane_visibility'/></div></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("pane_visibility")->SetProperty("visibility", "visible"); });
    failures += !run("window transformed, sibling + child shown",
        window + "</style></head><body><div id='window'><div class='pane' id='ref'/>"
                 "<div class='pane' id='pane_display'/></div></body></rml>",
        [](Rml::ElementDocument& d) { d.GetElementById("pane_display")->SetProperty("display", "block"); });
    failures += !run("new element inserted, scale(4)",
        kHead + "#box { transform: scale(4); }</style></head><body/></rml>",
        [](Rml::ElementDocument& d) {
            auto element = d.CreateElement("div");
            element->SetId("box");
            d.AppendChild(std::move(element));
        });
    // The host asks for an absolute offset from inside the render pass.
    failures += !run("window transformed, offset read mid-render",
                     window + "</style></head><body><div id='window'><div class='pane' id='pane_display'/></div>"
                              "</body></rml>",
                     [](Rml::ElementDocument& d) { d.GetElementById("pane_display")->SetProperty("display", "block"); },
                     Options{true, false});
    // A box shadow on a renderer with no layers, before the shown element.
    failures += !run("window transformed, shadow without layers",
                     window + "#shadow { display: block; width: 8px; height: 8px; background-color: #00ff00; "
                              "box-shadow: 2px 2px 0px #0000ff; }</style></head><body><div id='window'>"
                              "<div id='shadow'/><div class='pane' id='pane_display'/></div></body></rml>",
                     [](Rml::ElementDocument& d) { d.GetElementById("pane_display")->SetProperty("display", "block"); },
                     Options{false, true});
    if (failures)
        std::fprintf(stderr, "%d case(s) drew a first frame unlike the reference\n", failures);
    return failures == 0 ? 0 : 1;
}
