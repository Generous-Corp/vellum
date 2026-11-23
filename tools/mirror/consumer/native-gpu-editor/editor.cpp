#include <vellum/platform/gpu_fwd.hpp>
#include <vellum/view/plugin_view_host.hpp>

namespace {

class Editor final : public vellum::view::PluginViewHost {
public:
    vellum::view::NativeViewHandle native_handle() override { return nullptr; }
    void attach_to_parent(vellum::view::NativeViewHandle) override {}
    void detach() override {}
    void repaint() override {}
    void set_size(uint32_t, uint32_t) override {}
    Size get_size() const override { return {}; }
};

}  // namespace

int native_editor_translation_unit() {
    Editor editor;
    return editor.gpu_surface() == nullptr ? 0 : 1;
}
