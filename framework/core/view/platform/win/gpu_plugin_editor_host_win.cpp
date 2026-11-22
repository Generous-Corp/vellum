#include <vellum/view/plugin_view_host.hpp>

#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/render/skia_surface.hpp>
#include <vellum/view/window_host.hpp>

#include <windows.h>

#include <algorithm>
#include <memory>
#include <mutex>
#include <vector>

namespace vellum::view::detail {
namespace {

class WinPluginViewHost;
LRESULT CALLBACK host_wnd_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam);

ATOM register_host_class() {
    static std::once_flag once;
    static ATOM atom = 0;
    std::call_once(once, [] {
        WNDCLASSW wc{};
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpfnWndProc = host_wnd_proc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"VellumPluginEditorHostWin32";
        wc.style = CS_OWNDC;
        atom = RegisterClassW(&wc);
        if (!atom && GetLastError() == ERROR_CLASS_ALREADY_EXISTS)
            atom = 1;
    });
    return atom;
}

class WinPluginViewHost final : public PluginViewHost {
public:
    WinPluginViewHost(View& root, const Options& options)
        : root_(root), size_(options.size), background_rgb_(options.background_rgb),
          use_gpu_(options.use_gpu), present_policy_(options.present_policy),
          enable_gpu_timing_(options.enable_gpu_timing) {
        mark_gpu_surface_pending();
        if (!register_host_class()) {
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }
        hwnd_ = CreateWindowExW(0, L"VellumPluginEditorHostWin32", L"",
                                WS_POPUP | WS_CLIPCHILDREN,
                                0, 0, static_cast<int>(size_.width),
                                static_cast<int>(size_.height), nullptr, nullptr,
                                GetModuleHandleW(nullptr), this);
        if (!hwnd_)
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
    }

    ~WinPluginViewHost() override {
        if (hwnd_)
            SetWindowLongPtrW(hwnd_, GWLP_USERDATA, 0);
        skia_.reset();
        gpu_.reset();
        if (hwnd_)
            DestroyWindow(hwnd_);
        hwnd_ = nullptr;
        publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
    }

    NativeViewHandle native_handle() override { return hwnd_; }

    void attach_to_parent(NativeViewHandle parent) override {
        if (!hwnd_) return;
        HWND parent_hwnd = static_cast<HWND>(parent);
        if (!parent_hwnd) {
            detach();
            return;
        }
        SetWindowLongPtrW(hwnd_, GWL_STYLE,
                          (GetWindowLongPtrW(hwnd_, GWL_STYLE) & ~WS_POPUP) |
                              WS_CHILD | WS_VISIBLE);
        SetParent(hwnd_, parent_hwnd);
        SetWindowPos(hwnd_, nullptr, 0, 0, static_cast<int>(size_.width),
                     static_cast<int>(size_.height),
                     SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW);
        ensure_surface();
        repaint();
    }

    bool is_attached() const noexcept override {
        return hwnd_ && GetParent(hwnd_) != nullptr;
    }

    void detach() override {
        if (!hwnd_) return;
        SetParent(hwnd_, nullptr);
        SetWindowLongPtrW(hwnd_, GWL_STYLE,
                          (GetWindowLongPtrW(hwnd_, GWL_STYLE) & ~WS_CHILD) |
                              WS_POPUP);
    }

    void repaint() override {
        if (hwnd_) InvalidateRect(hwnd_, nullptr, FALSE);
    }

    void set_size(uint32_t width, uint32_t height) override {
        size_ = {width, height};
        if (hwnd_) {
            SetWindowPos(hwnd_, nullptr, 0, 0, static_cast<int>(width),
                         static_cast<int>(height),
                         SWP_NOACTIVATE | SWP_NOZORDER);
        }
        resize_surfaces();
        repaint();
    }

    Size get_size() const override { return size_; }

    void set_design_viewport(float width, float height) override {
        design_width_ = width;
        design_height_ = height;
        root_.set_bounds(has_design_viewport()
            ? Rect{0, 0, design_width_, design_height_}
            : Rect{0, 0, static_cast<float>(size_.width), static_cast<float>(size_.height)});
        root_.layout_children_if_needed();
        repaint();
    }

    void set_design_viewport_top_align(bool top_align) override {
        design_top_align_ = top_align;
        repaint();
    }

    Point window_to_root_point(Point point) const override {
        return WindowHost::design_viewport_window_to_root(
            point, size_.width, size_.height, design_width_, design_height_, design_top_align_);
    }

    bool design_viewport_transform(float& sx, float& sy, float& tx, float& ty) const override {
        return WindowHost::compute_design_viewport_transform(
            size_.width, size_.height, design_width_, design_height_, sx, sy, tx, ty,
            design_top_align_);
    }

    bool is_gpu_backed() const override { return gpu_ready_; }
    render::GpuSurface* gpu_surface() const override { return gpu_.get(); }
    bool present_first_frame() override { return paint(); }

    std::vector<uint8_t> capture_back_buffer_png() override {
        if (!gpu_ready_ || !gpu_->begin_frame()) return {};
        auto* canvas = skia_->begin_frame();
        if (!canvas) { gpu_->end_frame(); return {}; }
        paint_scene(*canvas);
        skia_->end_frame();
        std::vector<uint8_t> pixels;
        uint32_t width = 0, height = 0;
        const bool ok = skia_->read_current_rgba(pixels, width, height);
        gpu_->end_frame();
        if (!ok) return {};
        return render::HeadlessSurface::encode_png({std::move(pixels), width, height});
    }

    bool paint() {
        if (!gpu_ready_ || !gpu_->begin_frame()) return false;
        auto* canvas = skia_->begin_frame();
        if (!canvas) { gpu_->end_frame(); return false; }
        paint_scene(*canvas);
        const auto outcome = skia_->end_frame();
        gpu_->end_frame();
        if (render::frame_reached_output(outcome)) clear_pending_dirty();
        return render::frame_reached_output(outcome);
    }

    void handle_size_message() {
        if (!hwnd_) return;
        RECT rect{};
        GetClientRect(hwnd_, &rect);
        size_ = {static_cast<uint32_t>(std::max<LONG>(0, rect.right - rect.left)),
                 static_cast<uint32_t>(std::max<LONG>(0, rect.bottom - rect.top))};
        resize_surfaces();
        repaint();
    }

    void handle_dpi_message() {
        resize_surfaces();
        repaint();
    }

private:
    bool has_design_viewport() const {
        return design_width_ > 0.0f && design_height_ > 0.0f;
    }

    float scale_factor() const {
        const UINT dpi = hwnd_ ? GetDpiForWindow(hwnd_) : USER_DEFAULT_SCREEN_DPI;
        return static_cast<float>(dpi ? dpi : USER_DEFAULT_SCREEN_DPI) /
               static_cast<float>(USER_DEFAULT_SCREEN_DPI);
    }

    void ensure_surface() {
        if (gpu_ready_ || gpu_ || !use_gpu_ || !hwnd_ || !is_attached()) return;
        gpu_ = render::GpuSurface::create_dawn();
        if (!gpu_) {
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }
        const float scale = scale_factor();
        render::GpuSurface::Config config;
        config.width = static_cast<uint32_t>(size_.width * scale);
        config.height = static_cast<uint32_t>(size_.height * scale);
        config.vsync = present_policy_ == PresentPolicy::vsync;
        config.enable_gpu_timing = enable_gpu_timing_;
        config.backend_preference =
            render::GpuSurface::AdapterBackendPreference::d3d12;
        config.native_surface_handle = hwnd_;
        if (!gpu_->initialize(config)) {
            gpu_.reset();
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }
        render::SkiaSurface::Config skia_config;
        skia_config.width = size_.width;
        skia_config.height = size_.height;
        skia_config.scale_factor = scale;
        skia_ = render::SkiaSurface::create(*gpu_, skia_config);
        if (!skia_ || !skia_->is_available()) {
            skia_.reset();
            gpu_.reset();
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }
        gpu_ready_ = true;
        publish_gpu_surface(gpu_.get(), GpuSurfaceState::ready);
    }

    void resize_surfaces() {
        if (!gpu_) return;
        const float scale = scale_factor();
        gpu_->resize(static_cast<uint32_t>(size_.width * scale),
                     static_cast<uint32_t>(size_.height * scale));
        if (skia_)
            skia_->resize(size_.width, size_.height, scale);
    }

    void paint_scene(canvas::Canvas& canvas) {
        canvas.set_fill_color(canvas::Color::hex(background_rgb_));
        canvas.fill_rect(0, 0, size_.width, size_.height);
        if (!root_presentation_allowed()) return;
        float sx, sy, tx, ty;
        if (!design_viewport_transform(sx, sy, tx, ty)) {
            root_.paint_all(canvas);
            return;
        }
        root_.set_bounds({0, 0, design_width_, design_height_});
        root_.layout_children_if_needed();
        const int saved = canvas.save_count();
        canvas.save();
        canvas.translate(tx, ty);
        canvas.scale(sx, sy);
        root_.paint_all(canvas);
        canvas.restore_to_count(saved);
    }

    View& root_;
    HWND hwnd_ = nullptr;
    Size size_{};
    uint32_t background_rgb_ = kEditorHostClearRgb;
    bool use_gpu_ = false;
    bool gpu_ready_ = false;
    PresentPolicy present_policy_ = PresentPolicy::nonblocking;
    bool enable_gpu_timing_ = false;
    float design_width_ = 0.0f;
    float design_height_ = 0.0f;
    bool design_top_align_ = false;
    std::unique_ptr<render::GpuSurface> gpu_;
    std::unique_ptr<render::SkiaSurface> skia_;
};

LRESULT CALLBACK host_wnd_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* host = reinterpret_cast<WinPluginViewHost*>(
        GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<const CREATESTRUCTW*>(lparam);
        host = static_cast<WinPluginViewHost*>(create->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(host));
    }
    if (host) {
        switch (message) {
        case WM_PAINT: {
            PAINTSTRUCT paint{};
            BeginPaint(hwnd, &paint);
            host->paint();
            EndPaint(hwnd, &paint);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_SIZE:
            host->handle_size_message();
            return 0;
        case WM_DPICHANGED:
            host->handle_dpi_message();
            return 0;
        case WM_NCDESTROY:
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            break;
        default:
            break;
        }
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

}  // namespace

std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(
    View& root, const PluginViewHost::Options& options) {
    return std::make_unique<WinPluginViewHost>(root, options);
}

}  // namespace vellum::view::detail
