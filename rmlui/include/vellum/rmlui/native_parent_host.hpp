#pragma once

#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include <cstdint>
#include <memory>
#include <string_view>
#include <vector>

namespace vellum::rmlui {

enum class NativeParentKind : std::uint8_t { none, cocoa_view };

struct NativeParent {
    NativeParentKind kind = NativeParentKind::none;
    void* handle = nullptr;
};

struct NativeHostSize {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    double scale = 1.0;
};

// A small native-parent editor host. On macOS the parent is an NSView; the
// host adds one child view and presents the renderer's premultiplied RGBA
// frame in that view's layer. The RmlUi context and all input calls remain
// owned by this object and are serialized by the caller's UI thread. Text
// arrives as committed text through the input system; IME composition
// (marked text) is not supported.
// When constructed with an external RenderInterface, idle() submits the frame
// to that interface and does not update the Cocoa layer. The owner of that
// interface is responsible for presenting its draw commands.
class VELLUM_RMLUI_API NativeParentHost final {
  public:
    explicit NativeParentHost(AssetProvider&, RenderInterface* renderer = nullptr);
    ~NativeParentHost();
    NativeParentHost(const NativeParentHost&) = delete;
    NativeParentHost& operator=(const NativeParentHost&) = delete;

    Status open(std::uint32_t width, std::uint32_t height);
    Status load_document(std::string_view path);
    Status bind_model(std::string_view name, std::shared_ptr<ModelHost> model,
                      std::vector<std::string> fields);
    Status dirty_model(std::string_view name, std::string_view field);

    bool supports_parent(NativeParentKind) const noexcept;
    bool attach(const NativeParent&);
    void detach();
    void invalidate();
    bool set_size(const NativeHostSize&);
    NativeHostSize size() const noexcept { return size_; }
    void idle();
    void visibility_changed(bool visible);
    void focus_changed(bool focused);
    Status process_mouse_move(int x, int y, int modifiers = 0);
    Status process_mouse_button_down(int button, int modifiers = 0);
    Status process_mouse_button_up(int button, int modifiers = 0);
    Status process_mouse_wheel(float x, float y, int modifiers = 0);
    Status process_mouse_leave();
    Status process_key_down(int key, int modifiers = 0);
    Status process_key_up(int key, int modifiers = 0);
    Status process_text_input(std::string_view text);

    [[nodiscard]] bool is_open() const noexcept;
    [[nodiscard]] void* native_view() const noexcept;
    [[nodiscard]] const SoftRenderer* default_renderer() const noexcept { return soft_; }

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    NativeHostSize size_{};
    SoftRenderer* soft_ = nullptr;
};

} // namespace vellum::rmlui
