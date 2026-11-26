#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/input_events.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/view/accessibility.hpp>
#include <vellum/view/text_accessibility.hpp>
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
#include <vector>

// Provided by the accessibility units compiled into this image: the window-host
// element tree and the painted-text element lookup.
namespace vellum::view {
NSArray* build_accessibility_elements(View& root, NSView* host);
}
extern "C" void* vellum_text_accessibility_lookup_macos(const char* id_utf8);

namespace vellum::view::detail {
namespace {
class MacPluginViewHost;
Class host_class();

// Mirrors the order build_accessibility_elements() walks: every descendant of
// the root, parents before children, keeping the views that pass the shared
// exposure gate. Each entry records whether the view and all its ancestors are
// visible, so a hidden panel's contents stay out of the tree a screen reader
// reads.
void collect_exposure_flags(const View& parent, bool parent_visible, std::vector<bool>& out) {
    for (std::size_t i = 0; i < parent.child_count(); ++i) {
        const View* child = parent.child_at(i);
        const bool visible = parent_visible && child->visible();
        if (is_accessibility_element(*child)) out.push_back(visible);
        collect_exposure_flags(*child, visible, out);
    }
}

NSArray* editor_accessibility_children(VellumMirrorMetalView* host) {
    NSMutableArray* children = [NSMutableArray array];
    if (View* root = host.rootView) {
        NSArray* elements = build_accessibility_elements(*root, host);
        std::vector<bool> shown;
        collect_exposure_flags(*root, root->visible(), shown);
        const bool aligned = shown.size() == elements.count;
        for (NSUInteger i = 0; i < elements.count; ++i) {
            if (aligned && !shown[i]) continue;
            [elements[i] setAccessibilityParent:host];
            [children addObject:elements[i]];
        }
    }
    // Painted text is registered apart from the view tree; without this merge a
    // screen reader never reaches it.
    for (const auto& node : snapshot_accessibility_nodes()) {
        auto* element = (__bridge NSAccessibilityElement*)
            vellum_text_accessibility_lookup_macos(node.id.c_str());
        if (!element) continue;
        [element setAccessibilityParent:host];
        [children addObject:element];
    }
    return children;
}

// Every Metal view the framework creates, the window host's and the plug-in
// editor's, presents as one group whose children are the view tree's accessible
// elements and the registered text elements. The methods are added to the shared
// class once, when this image loads, so the window host needs no accessibility
// code of its own.
void add_method(Class cls, SEL selector, const char* return_type, id block) {
    char types[8];
    std::snprintf(types, sizeof types, "%s@:", return_type);
    class_addMethod(cls, selector, imp_implementationWithBlock(block), types);
}

void install_view_accessibility() {
    Class cls = [VellumMirrorMetalView class];
    add_method(cls, @selector(isAccessibilityElement), @encode(BOOL), ^BOOL(id) { return YES; });
    add_method(cls, @selector(accessibilityRole), @encode(id), ^NSAccessibilityRole(id) {
        return NSAccessibilityGroupRole;
    });
    add_method(cls, @selector(accessibilityLabel), @encode(id), ^NSString*(NSView* host) {
        NSString* title = host.window.title;
        return title.length > 0 ? title : @"UI";
    });
    add_method(cls, @selector(accessibilityChildren), @encode(id), ^NSArray*(VellumMirrorMetalView* host) {
        return editor_accessibility_children(host);
    });
}

__attribute__((constructor)) void install_view_accessibility_at_load() {
    install_view_accessibility();
}

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
            invalidate_gpu_surface();
            skia_.reset();
            gpu_.reset();
        }
    }
    ~MacPluginViewHost() override {
        view_.repaintBlock = nil;
        view_.backingChangedBlock = nil;
        view_.pointTransform = nil;
        [view_ prepareForTeardown];
        view_.rootView = nullptr;
        invalidate_gpu_surface();
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
    std::size_t presented_frame_count() const override { return presented_frame_count_; }
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
        if (!root_presentation_allowed()) return false;
        if (!gpu_ready_ || !gpu_->begin_frame()) return false;
        auto* c = skia_->begin_frame();
        if (!c) { gpu_->end_frame(); return false; }
        paint_scene(*c);
        const auto outcome = skia_->end_frame();
        gpu_->end_frame();
        const bool reached = render::frame_reached_output(outcome);
        if (reached) ++presented_frame_count_;
        clear_pending_dirty();
        return reached;
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

    View& root_; VellumMirrorMetalView* view_ = nil; Size size_{};
    uint32_t background_rgb_ = kEditorHostClearRgb; bool gpu_ready_ = false;
    float fixed_aspect_ratio_ = 0.0f;
    float design_width_ = 0.0f;
    float design_height_ = 0.0f;
    bool design_top_align_ = false;
    std::unique_ptr<render::GpuSurface> gpu_; std::unique_ptr<render::SkiaSurface> skia_;
    std::size_t presented_frame_count_ = 0;
};

Class host_class() {
    static Class c=[] { char n[96]; std::snprintf(n,sizeof n,"VellumPluginViewHost_%p",reinterpret_cast<void*>(&host_class));
        if (Class old=objc_getClass(n)) return old; Class x=objc_allocateClassPair([VellumMirrorMetalView class],n,0);
        add_method(x, @selector(accessibilityLabel), @encode(id), ^NSString*(id) { return @"Plugin UI"; });
        objc_registerClassPair(x); return x; }(); return c;
}
}

std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(View& root, const PluginViewHost::Options& o) {
    return std::make_unique<MacPluginViewHost>(root,o);
}
} // namespace vellum::view::detail

