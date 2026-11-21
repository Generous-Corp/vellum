#include "test_support.hpp"

#include <RmlUi/Core.h>

#include <limits>

namespace {

class NoopRenderer final : public Rml::RenderInterface {
  public:
    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex>,
                                                Rml::Span<const int>) override {
        return 0;
    }
    void RenderGeometry(Rml::CompiledGeometryHandle, Rml::Vector2f, Rml::TextureHandle) override {}
    void ReleaseGeometry(Rml::CompiledGeometryHandle) override {}
    Rml::TextureHandle LoadTexture(Rml::Vector2i&, const Rml::String&) override { return 0; }
    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte>, Rml::Vector2i) override { return 0; }
    void ReleaseTexture(Rml::TextureHandle) override {}
    void EnableScissorRegion(bool) override {}
    void SetScissorRegion(Rml::Rectanglei) override {}
};

} // namespace

int main() {
    NoopRenderer renderer;
    VELLUM_RMLUI_CHECK(Rml::Initialise());
    auto* context = Rml::CreateContext("idle", {100, 100}, &renderer);
    VELLUM_RMLUI_CHECK(context);
    auto* document = context->LoadDocumentFromMemory(
        "<rml><head><title>Idle</title></head>"
        "<body><div style='width: 20px; height: 20px; background-color: red;'></div></body></rml>");
    VELLUM_RMLUI_CHECK(document);
    document->Show();
    VELLUM_RMLUI_CHECK(context->Update());
    VELLUM_RMLUI_CHECK(document->IsVisible());

    VELLUM_RMLUI_CHECK(context->Update());
    const auto delay = context->GetNextUpdateDelay();
    if (delay != std::numeric_limits<double>::infinity()) {
        std::fprintf(stderr, "Idle document requested another update in %g seconds\n", delay);
        return 1;
    }

    Rml::Shutdown();
}
