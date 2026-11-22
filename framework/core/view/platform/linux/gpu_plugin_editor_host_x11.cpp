#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/window_host.hpp>
#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>

#include <X11/Xlib.h>

#include <algorithm>
#include <cstdint>
#include <memory>

namespace vellum::view::detail {
namespace {

class X11PluginViewHost final : public PluginViewHost {
public:
    X11PluginViewHost(View& root, const Options& options)
        : root_(root), size_(options.size), background_rgb_(options.background_rgb) {
        mark_gpu_surface_pending();
        display_ = XOpenDisplay(nullptr);
        if (!display_) {
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }

        window_ = XCreateSimpleWindow(display_, DefaultRootWindow(display_), 0, 0,
                                      size_.width, size_.height, 0, 0, background_rgb_);
        if (!window_) {
            XCloseDisplay(display_);
            display_ = nullptr;
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
            return;
        }
        XSelectInput(display_, window_, ExposureMask | StructureNotifyMask);
        XSetWindowBackground(display_, window_, background_rgb_);

        gpu_ = render::GpuSurface::create_dawn();
        if (gpu_) {
            render::GpuSurface::Config config;
            config.width = size_.width;
            config.height = size_.height;
            config.vsync = options.present_policy == PresentPolicy::vsync;
            render::GpuSurface::X11NativeHandle native{display_, window_};
            config.native_surface_handle = &native;
            if (gpu_->initialize(config)) {
                render::SkiaSurface::Config skia_config;
                skia_config.width = size_.width;
                skia_config.height = size_.height;
                skia_config.scale_factor = scale_factor_;
                skia_ = render::SkiaSurface::create(*gpu_, skia_config);
            }
        }
        if (gpu_ && skia_ && skia_->is_available()) {
            gpu_ready_ = true;
            publish_gpu_surface(gpu_.get(), GpuSurfaceState::ready);
        } else {
            skia_.reset();
            gpu_.reset();
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
        }
    }

    ~X11PluginViewHost() override {
        if (display_ && window_) {
            XUnmapWindow(display_, window_);
            XDestroyWindow(display_, window_);
            XSync(display_, False);
        }
        skia_.reset();
        gpu_.reset();
        if (display_) XCloseDisplay(display_);
    }

    NativeViewHandle native_handle() override {
        return reinterpret_cast<void*>(static_cast<uintptr_t>(window_));
    }

    void attach_to_parent(NativeViewHandle parent) override {
        if (!display_ || !window_ || !parent) return;
        const Window parent_window = static_cast<Window>(reinterpret_cast<uintptr_t>(parent));
        XReparentWindow(display_, window_, parent_window, 0, 0);
        XMapWindow(display_, window_);
        XFlush(display_);
        attached_ = true;
        repaint();
    }

    bool is_attached() const noexcept override { return attached_; }

    void detach() override {
        if (!display_ || !window_ || !attached_) return;
        XUnmapWindow(display_, window_);
        XReparentWindow(display_, window_, DefaultRootWindow(display_), 0, 0);
        XFlush(display_);
        attached_ = false;
    }

    void repaint() override {
        if (display_ && window_) XClearArea(display_, window_, 0, 0, 0, 0, True);
    }

    void set_size(uint32_t width, uint32_t height) override {
        size_ = {width, height};
        if (display_ && window_) XResizeWindow(display_, window_, width, height);
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

    void set_scale_factor(float scale) override {
        if (scale <= 0.0f) return;
        scale_factor_ = scale;
        resize_surfaces();
        repaint();
    }

    float scale_factor() const override { return scale_factor_; }

    void pump_x_events() override {
        if (!display_) return;
        while (XPending(display_)) {
            XEvent event{};
            XNextEvent(display_, &event);
            if (event.type == Expose && event.xexpose.count == 0) {
                paint();
            } else if (event.type == ConfigureNotify) {
                const auto width = static_cast<uint32_t>(std::max(0, event.xconfigure.width));
                const auto height = static_cast<uint32_t>(std::max(0, event.xconfigure.height));
                if (width != size_.width || height != size_.height) {
                    size_ = {width, height};
                    resize_surfaces();
                    if (resize_callback_) resize_callback_(width, height);
                }
            }
        }
    }

    void set_resize_callback(std::function<void(uint32_t, uint32_t)> callback) override {
        resize_callback_ = std::move(callback);
    }

private:
    bool has_design_viewport() const {
        return design_width_ > 0.0f && design_height_ > 0.0f;
    }

    bool paint() {
        if (!gpu_ready_ || !gpu_->begin_frame()) return false;
        auto* canvas = skia_->begin_frame();
        if (!canvas) {
            gpu_->end_frame();
            return false;
        }
        canvas->set_fill_color(canvas::Color::hex(background_rgb_));
        canvas->fill_rect(0, 0, size_.width, size_.height);
        if (root_presentation_allowed()) {
            float sx, sy, tx, ty;
            if (design_viewport_transform(sx, sy, tx, ty)) {
                root_.set_bounds({0, 0, design_width_, design_height_});
                root_.layout_children_if_needed();
                const int saved = canvas->save_count();
                canvas->save();
                canvas->translate(tx, ty);
                canvas->scale(sx, sy);
                root_.paint_all(*canvas);
                canvas->restore_to_count(saved);
            } else {
                root_.paint_all(*canvas);
            }
        }
        const auto outcome = skia_->end_frame();
        gpu_->end_frame();
        clear_pending_dirty();
        return render::frame_reached_output(outcome);
    }

    void resize_surfaces() {
        if (!gpu_) return;
        gpu_->resize(static_cast<uint32_t>(size_.width * scale_factor_),
                     static_cast<uint32_t>(size_.height * scale_factor_));
        if (skia_) skia_->resize(size_.width, size_.height, scale_factor_);
    }

    View& root_;
    Display* display_ = nullptr;
    Window window_ = 0;
    Size size_{};
    uint32_t background_rgb_ = kEditorHostClearRgb;
    bool attached_ = false;
    bool gpu_ready_ = false;
    float scale_factor_ = 1.0f;
    float design_width_ = 0.0f;
    float design_height_ = 0.0f;
    bool design_top_align_ = false;
    std::function<void(uint32_t, uint32_t)> resize_callback_;
    std::unique_ptr<render::GpuSurface> gpu_;
    std::unique_ptr<render::SkiaSurface> skia_;
};

}  // namespace

std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(
    View& root, const PluginViewHost::Options& options) {
    return std::make_unique<X11PluginViewHost>(root, options);
}

}  // namespace vellum::view::detail
