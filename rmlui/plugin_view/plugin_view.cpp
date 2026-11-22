#include <vellum/rmlui/plugin_view.hpp>

#include <RmlUi/Core/Input.h>

namespace vellum::rmlui {

class PluginView::NullLua final : public LuaInterface {
  public:
    Status load(std::string_view, std::string_view) override { return Status::rejected; }
    Status dispatch(std::string_view) override { return Status::rejected; }
};

namespace {

int rml_modifiers(std::uint16_t modifiers) {
    int result = 0;
    if (modifiers & view::kModCtrl) result |= Rml::Input::KM_CTRL;
    if (modifiers & view::kModShift) result |= Rml::Input::KM_SHIFT;
    if (modifiers & view::kModAlt) result |= Rml::Input::KM_ALT;
    if (modifiers & view::kModMeta) result |= Rml::Input::KM_META;
    return result;
}

int rml_key(view::KeyCode key) {
    using namespace Rml::Input;
    const int value = static_cast<int>(key);
    if (value >= 'a' && value <= 'z') return KI_A + value - 'a';
    if (value >= '0' && value <= '9') return KI_0 + value - '0';
    switch (key) {
        case view::KeyCode::left: return KI_LEFT;
        case view::KeyCode::right: return KI_RIGHT;
        case view::KeyCode::up: return KI_UP;
        case view::KeyCode::down: return KI_DOWN;
        case view::KeyCode::home: return KI_HOME;
        case view::KeyCode::end_: return KI_END;
        case view::KeyCode::page_up: return KI_PRIOR;
        case view::KeyCode::page_down: return KI_NEXT;
        case view::KeyCode::backspace: return KI_BACK;
        case view::KeyCode::delete_: return KI_DELETE;
        case view::KeyCode::tab: return KI_TAB;
        case view::KeyCode::enter: return KI_RETURN;
        case view::KeyCode::escape: return KI_ESCAPE;
        case view::KeyCode::space: return KI_SPACE;
        case view::KeyCode::f1: return KI_F1;
        case view::KeyCode::f2: return KI_F2;
        case view::KeyCode::f3: return KI_F3;
        case view::KeyCode::f4: return KI_F4;
        case view::KeyCode::f5: return KI_F5;
        case view::KeyCode::f6: return KI_F6;
        case view::KeyCode::f7: return KI_F7;
        case view::KeyCode::f8: return KI_F8;
        case view::KeyCode::f9: return KI_F9;
        case view::KeyCode::f10: return KI_F10;
        case view::KeyCode::f11: return KI_F11;
        case view::KeyCode::f12: return KI_F12;
        default: return KI_UNKNOWN;
    }
}

int rml_button(view::MouseButton button) {
    switch (button) {
        case view::MouseButton::right: return 1;
        case view::MouseButton::middle: return 2;
        default: return 0;
    }
}

}  // namespace

PluginView::PluginView(AssetProvider& assets)
    : lua_(std::make_unique<NullLua>()), renderer_(0x00000000u),
      backend_(assets, renderer_, *lua_) {}

PluginView::~PluginView() { backend_.close(); }

Status PluginView::open(std::uint32_t width, std::uint32_t height) {
    set_bounds({0, 0, static_cast<float>(width), static_cast<float>(height)});
    return backend_.open(width, height);
}

Status PluginView::load_document(std::string_view path) {
    return backend_.load_document(path);
}

Status PluginView::bind_model(std::string_view name, std::shared_ptr<ModelHost> model,
                              std::vector<std::string> fields) {
    return backend_.bind_model(name, std::move(model), std::move(fields));
}

Status PluginView::dirty_model(std::string_view name, std::string_view field) {
    return backend_.dirty_model(name, field);
}

void PluginView::render() {
    if (!backend_.is_open()) return;
    backend_.render();
    frame_png_ = renderer_.encode_png();
}

void PluginView::paint(canvas::Canvas& canvas) {
    render();
    if (!frame_png_.empty())
        canvas.draw_image_from_data(reinterpret_cast<const std::uint8_t*>(frame_png_.data()),
                                    frame_png_.size(), 0, 0, bounds().width, bounds().height);
}

void PluginView::on_mouse_event(const view::MouseEvent& event) {
    const int x = static_cast<int>(event.position.x);
    const int y = static_cast<int>(event.position.y);
    const int modifiers = rml_modifiers(event.modifiers);
    if (event.is_wheel) {
        backend_.process_mouse_wheel(event.scroll_delta_x, event.scroll_delta_y, modifiers);
    } else if (event.isPress()) {
        backend_.process_mouse_move(x, y, modifiers);
        backend_.process_mouse_button_down(rml_button(event.button), modifiers);
    } else if (event.isRelease()) {
        backend_.process_mouse_move(x, y, modifiers);
        backend_.process_mouse_button_up(rml_button(event.button), modifiers);
    } else {
        backend_.process_mouse_move(x, y, modifiers);
    }
    request_repaint();
}

bool PluginView::on_key_event(const view::KeyEvent& event) {
    const int key = rml_key(event.key);
    const int modifiers = rml_modifiers(event.modifiers);
    if (event.is_down) backend_.process_key_down(key, modifiers);
    else backend_.process_key_up(key, modifiers);
    request_repaint();
    return true;
}

void PluginView::on_text_input(const view::TextInputEvent& event) {
    backend_.process_text_input(event.text);
    request_repaint();
}

void PluginView::on_mouse_leave() {
    backend_.process_mouse_leave();
    request_repaint();
}

}  // namespace vellum::rmlui
