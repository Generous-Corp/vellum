// The open target hands every RmlUi render call to the caller's own
// Rml::RenderInterface: compiled geometry, clip masks, transforms, layers,
// filters and shaders all arrive, where the sealed Core path flattens them to
// geometry draws (see core_flattening_test.cpp for that control).
#include <vellum/rmlui/native.hpp>

#include "test_support.hpp"

#include <RmlUi/Core.h>

#include <filesystem>
#include <map>
#include <set>
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

// Implements every RmlUi 6 render call and counts it. Handles are fake but
// distinct and non-zero, so RmlUi treats every feature as supported.
class CountingRenderer final : public Rml::RenderInterface {
  public:
    int compile_geometry = 0;
    int render_geometry = 0;
    int release_geometry = 0;
    int generate_texture = 0;
    int scissor_enable = 0;
    int clip_mask_enable = 0;
    int render_to_clip_mask = 0;
    int transform_set = 0;
    int push_layer = 0;
    int composite_layers = 0;
    int composite_filters = 0;
    int pop_layer = 0;
    int save_layer_as_texture = 0;
    std::set<std::string> filters_compiled;
    std::set<std::string> shaders_compiled;
    int render_shader = 0;

    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex>,
                                                Rml::Span<const int>) override {
        return static_cast<Rml::CompiledGeometryHandle>(++compile_geometry);
    }
    void RenderGeometry(Rml::CompiledGeometryHandle, Rml::Vector2f, Rml::TextureHandle) override {
        ++render_geometry;
    }
    void ReleaseGeometry(Rml::CompiledGeometryHandle) override {
        ++release_geometry;
    }
    Rml::TextureHandle LoadTexture(Rml::Vector2i&, const Rml::String&) override {
        return 0;
    }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte>, Rml::Vector2i) override {
        return static_cast<Rml::TextureHandle>(++generate_texture);
    }
    void ReleaseTexture(Rml::TextureHandle) override {}
    void EnableScissorRegion(bool enable) override {
        scissor_enable += enable ? 1 : 0;
    }
    void SetScissorRegion(Rml::Rectanglei) override {}
    void EnableClipMask(bool enable) override {
        clip_mask_enable += enable ? 1 : 0;
    }
    void RenderToClipMask(Rml::ClipMaskOperation, Rml::CompiledGeometryHandle,
                          Rml::Vector2f) override {
        ++render_to_clip_mask;
    }
    void SetTransform(const Rml::Matrix4f* transform) override {
        transform_set += transform ? 1 : 0;
    }
    Rml::LayerHandle PushLayer() override {
        return static_cast<Rml::LayerHandle>(++push_layer);
    }
    void CompositeLayers(Rml::LayerHandle, Rml::LayerHandle, Rml::BlendMode,
                         Rml::Span<const Rml::CompiledFilterHandle> filters) override {
        ++composite_layers;
        composite_filters += static_cast<int>(filters.size());
    }
    void PopLayer() override {
        ++pop_layer;
    }
    Rml::TextureHandle SaveLayerAsTexture() override {
        return static_cast<Rml::TextureHandle>(1000 + ++save_layer_as_texture);
    }
    Rml::CompiledFilterHandle SaveLayerAsMaskImage() override {
        return 0;
    }
    Rml::CompiledFilterHandle CompileFilter(const Rml::String& name,
                                            const Rml::Dictionary&) override {
        filters_compiled.insert(name);
        return static_cast<Rml::CompiledFilterHandle>(filters_compiled.size());
    }
    void ReleaseFilter(Rml::CompiledFilterHandle) override {}
    Rml::CompiledShaderHandle CompileShader(const Rml::String& name,
                                            const Rml::Dictionary&) override {
        shaders_compiled.insert(name);
        return static_cast<Rml::CompiledShaderHandle>(shaders_compiled.size());
    }
    void RenderShader(Rml::CompiledShaderHandle, Rml::CompiledGeometryHandle, Rml::Vector2f,
                      Rml::TextureHandle) override {
        ++render_shader;
    }
    void ReleaseShader(Rml::CompiledShaderHandle) override {}
};

std::string fixture(const char* name) {
    return vellum::rmlui::test::read_binary(std::filesystem::path(VELLUM_RMLUI_TEST_FIXTURES) /
                                            name);
}

constexpr const char* kPlainDocument =
    "<rml><head><style>body { display: block; width: 32px; height: 32px; }"
    "#box { display: block; width: 8px; height: 8px; background-color: #ff3b30; }"
    "</style></head><body><div id='box'/></body></rml>";

void test_every_render_call_reaches_the_callers_renderer() {
    MapAssets assets;
    assets.add("layers_filters.rml", fixture("layers_filters.rml"));
    CountingRenderer renderer;
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.open(160, 120) == Status::ok);
    VELLUM_RMLUI_CHECK(native.load_document("layers_filters.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(native.last_load_diagnostics().errors == 0);
    VELLUM_RMLUI_CHECK(native.last_load_diagnostics().dropped_declarations == 0);
    native.update();
    native.render();

    VELLUM_RMLUI_CHECK(renderer.compile_geometry > 0);
    VELLUM_RMLUI_CHECK(renderer.render_geometry > 0);
    VELLUM_RMLUI_CHECK(renderer.clip_mask_enable > 0);
    VELLUM_RMLUI_CHECK(renderer.render_to_clip_mask > 0);
    VELLUM_RMLUI_CHECK(renderer.transform_set > 0);
    VELLUM_RMLUI_CHECK(renderer.push_layer > 0);
    VELLUM_RMLUI_CHECK(renderer.pop_layer == renderer.push_layer);
    VELLUM_RMLUI_CHECK(renderer.composite_layers > 0);
    VELLUM_RMLUI_CHECK(renderer.composite_filters > 0);
    VELLUM_RMLUI_CHECK(renderer.filters_compiled.count("blur") == 1);
    VELLUM_RMLUI_CHECK(renderer.shaders_compiled.count("linear-gradient") == 1);
    VELLUM_RMLUI_CHECK(renderer.render_shader > 0);

    // The caller reaches RmlUi's own API on the same context.
    VELLUM_RMLUI_CHECK(native.context() != nullptr);
    VELLUM_RMLUI_CHECK(native.document() != nullptr);
    VELLUM_RMLUI_CHECK(native.document()->GetElementById("blur") != nullptr);
    native.close();
    VELLUM_RMLUI_CHECK(NativeContext::open_instances() == 0);
}

// Negative control for the counters: a document with no clip, filter, shader
// or transform produces none of those calls, so the counts above come from
// the fixture and not from the counting renderer.
void test_plain_document_uses_no_layer_features() {
    MapAssets assets;
    assets.add("plain.rml", kPlainDocument);
    CountingRenderer renderer;
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(native.load_document("plain.rml") == Status::ok);
    native.update();
    native.render();
    VELLUM_RMLUI_CHECK(renderer.render_geometry > 0);
    VELLUM_RMLUI_CHECK(renderer.clip_mask_enable == 0);
    VELLUM_RMLUI_CHECK(renderer.push_layer == 0);
    VELLUM_RMLUI_CHECK(renderer.filters_compiled.empty());
    VELLUM_RMLUI_CHECK(renderer.shaders_compiled.empty());
    VELLUM_RMLUI_CHECK(renderer.transform_set == 0);
    native.close();
}

void test_linked_assets_resolve_per_instance() {
    // Two instances with the same relative names: each reads only its own.
    const std::string document =
        "<rml><head><link type='text/rcss' href='style.rcss'/></head>"
        "<body style='display:block;'><div id='box'/></body></rml>";
    MapAssets first_assets;
    first_assets.add("editor.rml", document);
    first_assets.add("style.rcss", "#box { display: block; width: 4px; height: 4px; }");
    MapAssets second_assets;
    second_assets.add("editor.rml", document);
    second_assets.add("style.rcss", "#box { display: block; width: 9px; height: 9px; }");
    CountingRenderer first_renderer;
    CountingRenderer second_renderer;
    NativeContext first(first_assets, first_renderer);
    NativeContext second(second_assets, second_renderer);
    VELLUM_RMLUI_CHECK(first.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(second.open(32, 32) == Status::ok);
    VELLUM_RMLUI_CHECK(NativeContext::open_instances() == 2);
    VELLUM_RMLUI_CHECK(first.load_document("editor.rml") == Status::ok);
    VELLUM_RMLUI_CHECK(second.load_document("editor.rml") == Status::ok);
    first.update();
    second.update();
    const auto width = [](NativeContext& native) {
        return native.document()->GetElementById("box")->GetBox().GetSize().x;
    };
    VELLUM_RMLUI_CHECK(width(first) == 4.0f);
    VELLUM_RMLUI_CHECK(width(second) == 9.0f);
    first.close();
    VELLUM_RMLUI_CHECK(NativeContext::open_instances() == 1);
    second.close();
    VELLUM_RMLUI_CHECK(NativeContext::open_instances() == 0);
}

void test_scope_routes_direct_rmlui_calls() {
    // A file read made from the caller's own RmlUi call resolves through the
    // instance's assets only while its Scope is held.
    MapAssets assets;
    assets.add("plain.rml", kPlainDocument);
    assets.add("extra.rml", kPlainDocument);
    CountingRenderer renderer;
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.open(32, 32) == Status::ok);
    {
        NativeContext::Scope scope(native);
        auto* loaded = native.context()->LoadDocument("extra.rml");
        VELLUM_RMLUI_CHECK(loaded != nullptr);
        native.context()->UnloadDocument(loaded);
    }
    // With no instance in scope a read has nowhere to resolve and fails.
    VELLUM_RMLUI_CHECK(native.context()->LoadDocument("extra.rml") == nullptr);
    VELLUM_RMLUI_CHECK(native.load_document("plain.rml") == Status::ok);
    native.close();
}

void test_debugger_attaches_and_detaches_with_its_context() {
    MapAssets assets;
    assets.add("plain.rml", kPlainDocument);
    CountingRenderer renderer;
    NativeContext native(assets, renderer);
    VELLUM_RMLUI_CHECK(native.attach_debugger() == Status::not_open);
    VELLUM_RMLUI_CHECK(native.open(320, 240) == Status::ok);
    VELLUM_RMLUI_CHECK(native.load_document("plain.rml") == Status::ok);
    if (!NativeContext::fonts_enabled()) {
        // The debugger draws text; without a font engine it is refused.
        VELLUM_RMLUI_CHECK(native.attach_debugger() == Status::rejected);
        native.close();
        return;
    }
    VELLUM_RMLUI_CHECK(native.attach_debugger() == Status::ok);
    native.update();
    native.render();
    native.close();
    // Reopening after the debugger's host context went away works.
    VELLUM_RMLUI_CHECK(native.open(320, 240) == Status::ok);
    VELLUM_RMLUI_CHECK(native.attach_debugger() == Status::ok);
    native.close();
}

} // namespace

int main() {
    test_every_render_call_reaches_the_callers_renderer();
    test_plain_document_uses_no_layer_features();
    test_linked_assets_resolve_per_instance();
    test_scope_routes_direct_rmlui_calls();
    test_debugger_attaches_and_detaches_with_its_context();
    return 0;
}
