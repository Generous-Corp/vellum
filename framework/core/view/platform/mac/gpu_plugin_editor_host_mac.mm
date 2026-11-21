#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/input_events.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/view/window_host.hpp>
#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/canvas/canvas.hpp>
#include "mac_gpu_host_config.h"
#include "window_host_mac_metal_view.h"

#import <Cocoa/Cocoa.h>
#include <objc/runtime.h>
#include <cstdio>

namespace vellum::view::detail {
namespace {
class MacPluginViewHost;
Class host_class();

class MacPluginViewHost final : public PluginViewHost {
public:
    MacPluginViewHost(View& root, const Options& o)
        : root_(root), size_(o.size), background_rgb_(o.background_rgb) {
        mark_gpu_surface_pending();
        view_ = [[host_class() alloc] initWithFrame:NSMakeRect(0, 0, size_.width, size_.height)];
        view_.rootView = &root_;
        view_.backgroundRGB = background_rgb_;
        view_.hostOwnsRootLayout = YES;
        view_.pointTransform = ^(Point pt) { return this->window_to_root_point(pt); };
        view_.repaintBlock = ^{ this->paint(); };
        view_.backingChangedBlock = ^{ this->handle_backing_change(); };
        gpu_ = render::GpuSurface::create_dawn();
        if (gpu_) {
            render::GpuSurface::Config c;
            const CGFloat scale = view_.metalLayer.contentsScale;
            c.width = static_cast<uint32_t>(size_.width * scale);
            c.height = static_cast<uint32_t>(size_.height * scale);
            c.native_surface_handle = (__bridge void*)view_.metalLayer;
            if (gpu_->initialize(c)) {
                render::SkiaSurface::Config sc;
                sc.width = size_.width; sc.height = size_.height;
                sc.scale_factor = static_cast<float>(scale);
                skia_ = render::SkiaSurface::create(*gpu_, sc);
            }
        }
        if (gpu_ && skia_ && skia_->is_available()) {
            publish_gpu_surface(gpu_.get(), GpuSurfaceState::ready);
            gpu_ready_ = true;
        } else {
            gpu_.reset(); skia_.reset();
            publish_gpu_surface(nullptr, GpuSurfaceState::unavailable);
        }
    }
    ~MacPluginViewHost() override {
        view_.repaintBlock = nil;
        view_.backingChangedBlock = nil;
        view_.pointTransform = nil;
        [view_ prepareForTeardown];
        view_.rootView = nullptr;
        skia_.reset();
        gpu_.reset();
        [view_ removeFromSuperview];
        view_ = nil;
    }
    NativeViewHandle native_handle() override { return (__bridge void*)view_; }
    void attach_to_parent(NativeViewHandle parent) override {
        NSView* p = (__bridge NSView*)parent;
        if (p && view_ && view_.superview != p) { [view_ removeFromSuperview]; [p addSubview:view_]; }
        repaint();
    }
    bool is_attached() const noexcept override { return view_ && view_.superview != nil; }
    void detach() override { [view_ removeFromSuperview]; }
    void repaint() override { [view_ setNeedsDisplay:YES]; }
    void set_size(uint32_t w, uint32_t h) override {
        size_ = {w,h};
        [view_ setFrameSize:NSMakeSize(w,h)];
        resize_surfaces();
        repaint();
    }
    Size get_size() const override { return size_; }
    void set_fixed_aspect_ratio(float ratio) override {
        // The plug-in format negotiates the editor size with the DAW.
        fixed_aspect_ratio_ = ratio > 0.0f ? ratio : 0.0f;
    }
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
    Point window_to_root_point(Point pt) const override {
        return WindowHost::design_viewport_window_to_root(
            pt, size_.width, size_.height, design_width_, design_height_, design_top_align_);
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
        auto* c = skia_->begin_frame();
        if (!c) { gpu_->end_frame(); return {}; }
        paint_scene(*c);
        skia_->end_frame(); std::vector<uint8_t> px; uint32_t w=0,h=0;
        const bool ok = skia_->read_current_rgba(px,w,h); gpu_->end_frame();
        if (!ok) return {};
        render::HeadlessSurface::Rgba rgba{std::move(px),w,h};
        return render::HeadlessSurface::encode_png(rgba);
    }
    bool paint() {
        if (!gpu_ready_ || !gpu_->begin_frame()) return false;
        auto* c = skia_->begin_frame();
        if (!c) { gpu_->end_frame(); return false; }
        paint_scene(*c);
        const auto outcome = skia_->end_frame();
        gpu_->end_frame();
        clear_pending_dirty();
        return render::frame_reached_output(outcome);
    }
    void handle_backing_change() {
        resize_surfaces();
        repaint();
    }
    void resize_surfaces() {
        if (!gpu_) return;
        const CGFloat scale = view_.metalLayer.contentsScale;
        gpu_->resize(static_cast<uint32_t>(size_.width * scale),
                     static_cast<uint32_t>(size_.height * scale));
        if (skia_)
            skia_->resize(size_.width, size_.height, static_cast<float>(scale));
    }
private:
    bool has_design_viewport() const {
        return design_width_ > 0.0f && design_height_ > 0.0f;
    }
    void paint_scene(canvas::Canvas& canvas) {
        canvas.set_fill_color(canvas::Color::hex(background_rgb_));
        canvas.fill_rect(0, 0, size_.width, size_.height);

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

    View& root_; VellumMirrorMetalView* view_ = nil; Size size_{};
    uint32_t background_rgb_ = kEditorHostClearRgb; bool gpu_ready_ = false;
    float fixed_aspect_ratio_ = 0.0f;
    float design_width_ = 0.0f;
    float design_height_ = 0.0f;
    bool design_top_align_ = false;
    std::unique_ptr<render::GpuSurface> gpu_; std::unique_ptr<render::SkiaSurface> skia_;
};

Class host_class() {
    static Class c=[] { char n[96]; std::snprintf(n,sizeof n,"VellumPluginViewHost_%p",reinterpret_cast<void*>(&host_class));
        if (Class old=objc_getClass(n)) return old; Class x=objc_allocateClassPair([VellumMirrorMetalView class],n,0);
        objc_registerClassPair(x); return x; }(); return c;
}
}

std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(View& root, const PluginViewHost::Options& o) {
    return std::make_unique<MacPluginViewHost>(root,o);
}
} // namespace vellum::view::detail

