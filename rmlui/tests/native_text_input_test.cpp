// A click into a text input under VellumRmlUi::Native: the field must take
// focus and place its caret, and must not crash when the field's font cannot
// be resolved (the field then has no laid-out lines to hit-test).
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#include <RmlUi/Core.h>

#include <cstdio>
#include <map>
#include <string>
#include <string_view>
#include <utility>

using namespace vellum::rmlui;

namespace {

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

class NullRenderer final : public Rml::RenderInterface {
  public:
    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex>,
                                                Rml::Span<const int>) override {
        return 1;
    }
    void RenderGeometry(Rml::CompiledGeometryHandle, Rml::Vector2f, Rml::TextureHandle) override {}
    void ReleaseGeometry(Rml::CompiledGeometryHandle) override {}
    Rml::TextureHandle LoadTexture(Rml::Vector2i&, const Rml::String&) override {
        return 0;
    }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte>, Rml::Vector2i) override {
        return 1;
    }
    void ReleaseTexture(Rml::TextureHandle) override {}
    void EnableScissorRegion(bool) override {}
    void SetScissorRegion(Rml::Rectanglei) override {}
};

std::string document(std::string_view family) {
    return "<rml><head><style>"
           "body { display: block; width: 300px; height: 100px; font-family: " +
           std::string(family) +
           "; font-size: 14px; }"
           "input { display: inline-block; width: 200px; height: 24px; }"
           "</style></head><body><input id='field' type='text' value='hello world'/></body></rml>";
}

// Opens a context on `document`, renders a frame, clicks into the field and
// returns whether it took focus.
bool click_into_field(std::string_view family) {
    MapAssets assets;
    assets.add("doc.rml", document(family));
#if defined(VELLUM_RMLUI_TEST_FONT)
    assets.add("font.ttf", vellum::rmlui::test::read_binary(VELLUM_RMLUI_TEST_FONT));
#endif
    NullRenderer renderer;
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.open(300, 100) == Status::ok);
    {
        NativeContext::Scope scope(native);
        VELLUM_RMLUI_CHECK(Rml::LoadFontFace("font.ttf", false));
    }
    VELLUM_RMLUI_CHECK(native.load_document("doc.rml") == Status::ok);
    native.update();
    native.render();
    bool focused = false;
    {
        NativeContext::Scope scope(native);
        auto* field = native.document()->GetElementById("field");
        VELLUM_RMLUI_CHECK(field != nullptr);
        const auto box = field->GetAbsoluteOffset(Rml::BoxArea::Border);
        native.context()->ProcessMouseMove(int(box.x) + 20, int(box.y) + 10, 0);
        native.context()->ProcessMouseButtonDown(0, 0);
        native.context()->ProcessMouseButtonUp(0, 0);
        focused = native.context()->GetFocusElement() == field;
    }
    native.update();
    native.render();
    native.close();
    return focused;
}

} // namespace

int main() {
    if (!NativeContext::fonts_enabled() || std::string_view(VELLUM_RMLUI_TEST_FONT_FAMILY).empty())
        return 77; // needs a font engine and a test font
    // The field's font resolves: the click focuses it.
    std::printf("case: font resolved\n");
    std::fflush(stdout);
    VELLUM_RMLUI_CHECK(click_into_field(VELLUM_RMLUI_TEST_FONT_FAMILY));
    // The field's font does not resolve, so it lays out no text lines. The
    // click must still be handled without reading past the empty line list.
    std::printf("case: font unresolved\n");
    std::fflush(stdout);
    click_into_field("NoSuchFamily");
    return 0;
}
