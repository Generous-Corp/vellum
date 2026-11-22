#pragma once

#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>
#include <vellum/view/view.hpp>

#include <memory>
#include <string_view>

namespace vellum::rmlui {

// A RmlUi document exposed as a Vellum View. PluginViewHost owns the native
// child window and paints this view through its existing platform surface.
// The first cross-platform implementation deliberately uses SoftRenderer.
class VELLUM_RMLUI_API PluginView final : public view::View {
  public:
    explicit PluginView(AssetProvider& assets);
    ~PluginView() override;

    Status open(std::uint32_t width, std::uint32_t height);
    Status load_document(std::string_view path);
    Status bind_model(std::string_view name, std::shared_ptr<ModelHost> model,
                      std::vector<std::string> fields);
    Status dirty_model(std::string_view name, std::string_view field);
    void render();
    Backend& backend() noexcept { return backend_; }
    const SoftRenderer& renderer() const noexcept { return renderer_; }

    void paint(canvas::Canvas& canvas) override;
    void on_mouse_event(const view::MouseEvent& event) override;
    bool on_key_event(const view::KeyEvent& event) override;
    void on_text_input(const view::TextInputEvent& event) override;
    void on_mouse_leave() override;

  private:
    class NullLua;
    std::unique_ptr<NullLua> lua_;
    SoftRenderer renderer_;
    Backend backend_;
    std::string frame_png_;
};

}  // namespace vellum::rmlui
