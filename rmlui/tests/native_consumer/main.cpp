// Builds against the installed Native component alone: RmlUi's headers and
// archive come from the package, and the consumer's own render interface
// draws the context.
#include <vellum/rmlui/native.hpp>

#include <RmlUi/Core.h>
#include <RmlUi/Debugger.h>

#include <cstdio>
#include <string>

namespace {

struct Assets final : vellum::rmlui::AssetProvider {
    vellum::rmlui::Status load(std::string_view path, vellum::rmlui::Asset& out) override {
        if (path != "editor.rml")
            return vellum::rmlui::Status::missing_asset;
        out.path = std::string(path);
        out.bytes = "<rml><body style='display:block;width:16px;height:16px;"
                    "background-color:#ff3b30;'></body></rml>";
        return vellum::rmlui::Status::ok;
    }
};

struct Renderer final : Rml::RenderInterface {
    int draws = 0;
    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex>,
                                                Rml::Span<const int>) override {
        return 1;
    }
    void RenderGeometry(Rml::CompiledGeometryHandle, Rml::Vector2f, Rml::TextureHandle) override {
        ++draws;
    }
    void ReleaseGeometry(Rml::CompiledGeometryHandle) override {}
    Rml::TextureHandle LoadTexture(Rml::Vector2i&, const Rml::String&) override { return 0; }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte>, Rml::Vector2i) override {
        return 1;
    }
    void ReleaseTexture(Rml::TextureHandle) override {}
    void EnableScissorRegion(bool) override {}
    void SetScissorRegion(Rml::Rectanglei) override {}
};

} // namespace

int main() {
    Assets assets;
    Renderer renderer;
    vellum::rmlui::NativeContext native(assets, renderer);
    if (native.open(16, 16) != vellum::rmlui::Status::ok ||
        native.load_document("editor.rml") != vellum::rmlui::Status::ok)
        return 1;
    native.update();
    native.render();
    // The Debugger library links either way; attaching it needs fonts.
    const bool debugger_linked =
        native.attach_debugger() == (vellum::rmlui::NativeContext::fonts_enabled()
                                         ? vellum::rmlui::Status::ok
                                         : vellum::rmlui::Status::rejected);
    native.close();
    if (renderer.draws == 0 || !debugger_linked)
        return 1;
    std::printf("installed VellumRmlUi::Native drew %d geometry calls\n", renderer.draws);
    return 0;
}
