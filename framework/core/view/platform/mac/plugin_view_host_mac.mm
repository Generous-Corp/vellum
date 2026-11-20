#include <vellum/view/plugin_view_host.hpp>
#include <vellum/view/input_events.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/render/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>
#include <vellum/render/headless_surface.hpp>
#include <vellum/canvas/canvas.hpp>

#import <Cocoa/Cocoa.h>
#include <objc/runtime.h>
#include <cstdio>

namespace vellum::view::detail {
namespace {
class MacPluginViewHost;
Class host_class();
Ivar host_ivar();
MacPluginViewHost* host_of(id v) {
    Ivar iv = host_ivar();
    if (!v || !iv) return nullptr;
    return *reinterpret_cast<MacPluginViewHost**>(reinterpret_cast<char*>((__bridge void*)v) + ivar_getOffset(iv));
}
void set_host(id v, MacPluginViewHost* h) {
    Ivar iv = host_ivar();
    if (v && iv) *reinterpret_cast<MacPluginViewHost**>(reinterpret_cast<char*>((__bridge void*)v) + ivar_getOffset(iv)) = h;
}

class MacPluginViewHost final : public PluginViewHost {
public:
    MacPluginViewHost(View& root, const Options& o) : root_(root), size_(o.size) {
        mark_gpu_surface_pending();
        view_ = [[host_class() alloc] initWithFrame:NSMakeRect(0, 0, size_.width, size_.height)];
        view_.wantsLayer = YES;
        set_host(view_, this);
        gpu_ = render::GpuSurface::create_dawn();
        if (gpu_) {
            render::GpuSurface::Config c;
            c.width = size_.width; c.height = size_.height; c.native_surface_handle = nullptr;
            if (gpu_->initialize(c)) {
                render::SkiaSurface::Config sc;
                sc.width = size_.width; sc.height = size_.height;
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
    ~MacPluginViewHost() override { set_host(view_, nullptr); view_ = nil; }
    NativeViewHandle native_handle() override { return (__bridge void*)view_; }
    void attach_to_parent(NativeViewHandle parent) override {
        NSView* p = (__bridge NSView*)parent;
        if (p && view_ && view_.superview != p) { [view_ removeFromSuperview]; [p addSubview:view_]; }
        repaint();
    }
    bool is_attached() const noexcept override { return view_ && view_.superview != nil; }
    void detach() override { [view_ removeFromSuperview]; }
    void repaint() override { [view_ setNeedsDisplay:YES]; }
    void set_size(uint32_t w, uint32_t h) override { size_ = {w,h}; [view_ setFrameSize:NSMakeSize(w,h)]; if (gpu_) gpu_->resize(w,h); repaint(); }
    Size get_size() const override { return size_; }
    bool is_gpu_backed() const override { return gpu_ready_; }
    render::GpuSurface* gpu_surface() const override { return gpu_.get(); }
    bool present_first_frame() override { return paint(); }
    std::vector<uint8_t> capture_back_buffer_png() override {
        if (!gpu_ready_ || !gpu_->begin_frame()) return {};
        auto* c = skia_->begin_frame();
        if (!c) { gpu_->end_frame(); return {}; }
        c->set_fill_color(canvas::Color::rgba8(kEditorHostClearR,kEditorHostClearG,kEditorHostClearB,255));
        c->fill_rect(0,0,size_.width,size_.height); root_.paint_all(*c);
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
        c->set_fill_color(canvas::Color::rgba8(kEditorHostClearR,kEditorHostClearG,kEditorHostClearB,255));
        c->fill_rect(0,0,size_.width,size_.height); root_.paint_all(*c);
        skia_->end_frame(); gpu_->end_frame(); clear_pending_dirty(); return true;
    }
    void key(NSEvent* e) {
        KeyEvent k; k.key = KeyCode::unknown; k.is_down = true;
        if (auto* f = focused_input_under_root(root_)) deliver_key_event(*f,k);
        else if (root_.on_global_key) root_.on_global_key(k);
    }
private:
    View& root_; NSView* view_ = nil; Size size_{}; bool gpu_ready_ = false;
    std::unique_ptr<render::GpuSurface> gpu_; std::unique_ptr<render::SkiaSurface> skia_;
};

BOOL flipped(id,SEL){return YES;} BOOL wants_layer(id,SEL){return YES;}
void draw(id v,SEL){if(auto* h=host_of(v)) h->paint();}
void key_down(id v,SEL,NSEvent* e){if(auto* h=host_of(v)) h->key(e);}
Class host_class() {
    static Class c=[] { char n[96]; std::snprintf(n,sizeof n,"VellumPluginViewHost_%p",reinterpret_cast<void*>(&host_class));
        if (Class old=objc_getClass(n)) return old; Class x=objc_allocateClassPair([NSView class],n,0);
        class_addIvar(x,"vellum_host",sizeof(void*),3,"^v"); class_addMethod(x,@selector(isFlipped),(IMP)flipped,"c@:");
        class_addMethod(x,@selector(wantsLayer),(IMP)wants_layer,"c@:"); class_addMethod(x,@selector(drawRect:),(IMP)draw,"v@:");
        class_addMethod(x,@selector(keyDown:),(IMP)key_down,"v@:@"); objc_registerClassPair(x); return x; }(); return c;
}
Ivar host_ivar(){ static Ivar i=[] { Class c=host_class(); return c ? class_getInstanceVariable(c,"vellum_host") : nullptr; }(); return i; }
}

std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(View& root, const PluginViewHost::Options& o) {
    return std::make_unique<MacPluginViewHost>(root,o);
}
} // namespace vellum::view::detail

