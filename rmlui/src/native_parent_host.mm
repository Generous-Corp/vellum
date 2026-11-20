#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#include <objc/runtime.h>

#include <vellum/rmlui/native_parent_host.hpp>

#include <cstdio>
#include <memory>
#include <string>
#include <utility>

@interface VellumRmlUiHostView : NSView
@property(nonatomic, assign) void* owner;
@end

@implementation VellumRmlUiHostView
- (BOOL)acceptsFirstResponder { return YES; }
- (void)mouseDown:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); [self.window makeFirstResponder:self]; host->process_mouse_button_down(0); }
- (void)mouseUp:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_mouse_button_up(0); }
- (void)mouseMoved:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); NSPoint p = [self convertPoint:event.locationInWindow fromView:nil]; host->process_mouse_move((int)p.x, (int)(self.bounds.size.height - p.y)); }
- (void)mouseDragged:(NSEvent*)event { [self mouseMoved:event]; }
- (void)rightMouseDown:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_mouse_button_down(1); }
- (void)rightMouseUp:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_mouse_button_up(1); }
- (void)scrollWheel:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_mouse_wheel(event.scrollingDeltaX, event.scrollingDeltaY); }
- (void)keyDown:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_key_down((int)event.keyCode, 0); }
- (void)keyUp:(NSEvent*)event { auto* host = static_cast<vellum::rmlui::NativeParentHost*>(self.owner); host->process_key_up((int)event.keyCode, 0); }
- (void)insertText:(id)text { if ([text isKindOfClass:[NSString class]]) static_cast<vellum::rmlui::NativeParentHost*>(self.owner)->process_text_input([text UTF8String]); }
@end

namespace vellum::rmlui {
namespace {

class NullLua final : public LuaInterface {
  public:
    Status load(std::string_view, std::string_view) override { return Status::rejected; }
    Status dispatch(std::string_view) override { return Status::rejected; }
};

Class host_view_class() {
    static Class klass = [] {
        char name[96];
        std::snprintf(name, sizeof(name), "VellumRmlUiNativeHost_%p", reinterpret_cast<void*>(&host_view_class));
        if (Class existing = objc_getClass(name))
            return existing;
        Class created = objc_allocateClassPair([VellumRmlUiHostView class], name, 0);
        objc_registerClassPair(created);
        return created;
    }();
    return klass;
}

} // namespace

// The owned SoftRenderer is declared first so it outlives the Backend that
// draws through it.
struct NativeParentHost::Impl {
    Impl(AssetProvider& assets, RenderInterface* external)
        : owned_soft(external ? nullptr : std::make_unique<SoftRenderer>(0x00000000u)),
          lua(),
          backend(assets, external ? *external : *owned_soft, lua) {}
    std::unique_ptr<SoftRenderer> owned_soft;
    NullLua lua;
    Backend backend;
    VellumRmlUiHostView* view = nil;
    CGImageRef image = nil;
    bool visible = true;
    bool focused = false;

    void present(const SoftRenderer& soft) {
        if (!view || soft.width() == 0 || soft.height() == 0)
            return;
        CGDataProviderRef provider = CGDataProviderCreateWithData(nullptr, soft.pixels().data(),
                                                                  soft.pixels().size(), nullptr);
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGImageRef next = CGImageCreate(soft.width(), soft.height(), 8, 32, soft.width() * 4,
                                        space, kCGImageAlphaPremultipliedLast | kCGBitmapByteOrderDefault,
                                        provider, nullptr, false, kCGRenderingIntentDefault);
        if (image)
            CGImageRelease(image);
        image = next;
        view.layer.contents = (__bridge id)image;
        CGColorSpaceRelease(space);
        CGDataProviderRelease(provider);
    }
};

NativeParentHost::NativeParentHost(AssetProvider& assets, RenderInterface* renderer)
    : impl_(std::make_unique<Impl>(assets, renderer)) {
    soft_ = impl_->owned_soft.get();
}

NativeParentHost::~NativeParentHost() { invalidate(); }

Status NativeParentHost::open(std::uint32_t width, std::uint32_t height) {
    size_.width = width; size_.height = height;
    return impl_->backend.open(width, height);
}
Status NativeParentHost::load_document(std::string_view path) { return impl_->backend.load_document(path); }
Status NativeParentHost::bind_model(std::string_view n, std::shared_ptr<ModelHost> m, std::vector<std::string> f) {
    return impl_->backend.bind_model(n, std::move(m), std::move(f));
}
Status NativeParentHost::dirty_model(std::string_view n, std::string_view f) { return impl_->backend.dirty_model(n, f); }
bool NativeParentHost::supports_parent(NativeParentKind k) const noexcept { return k == NativeParentKind::none || k == NativeParentKind::cocoa_view; }
bool NativeParentHost::attach(const NativeParent& parent) {
    if (parent.kind == NativeParentKind::none) { detach(); return true; }
    if (parent.kind != NativeParentKind::cocoa_view || !parent.handle) return false;
    NSView* p = (__bridge NSView*)parent.handle;
    if (!impl_->view) {
        impl_->view = [[host_view_class() alloc] initWithFrame:NSMakeRect(0, 0, size_.width, size_.height)];
        [impl_->view setOwner:this];
        impl_->view.wantsLayer = YES;
        impl_->view.layer.opaque = NO;
    }
    if (impl_->view.superview != p) { [impl_->view removeFromSuperview]; [p addSubview:impl_->view]; }
    return true;
}
void NativeParentHost::detach() { [impl_->view removeFromSuperview]; }
void NativeParentHost::invalidate() {
    if (!impl_) return;
    detach();
    if (impl_->image) { CGImageRelease(impl_->image); impl_->image = nil; }
    impl_->view = nil;
    impl_->backend.close();
}
bool NativeParentHost::set_size(const NativeHostSize& s) {
    if (!s.width || !s.height || s.scale <= 0) return false;
    size_ = s; impl_->backend.set_dimensions(s.width, s.height); // logical host size is the context size
    if (impl_->view) [impl_->view setFrameSize:NSMakeSize(s.width, s.height)];
    return true;
}
void NativeParentHost::idle() {
    if (!impl_->backend.is_open() || !impl_->visible) return;
    impl_->backend.render();
    if (soft_) impl_->present(*soft_);
}
void NativeParentHost::visibility_changed(bool visible) { impl_->visible = visible; if (impl_->view) impl_->view.hidden = !visible; }
void NativeParentHost::focus_changed(bool focused) { impl_->focused = focused; }
Status NativeParentHost::process_mouse_move(int x, int y, int modifiers) { return impl_->backend.process_mouse_move(x, y, modifiers); }
Status NativeParentHost::process_mouse_button_down(int button, int modifiers) { return impl_->backend.process_mouse_button_down(button, modifiers); }
Status NativeParentHost::process_mouse_button_up(int button, int modifiers) { return impl_->backend.process_mouse_button_up(button, modifiers); }
Status NativeParentHost::process_mouse_wheel(float x, float y, int modifiers) { return impl_->backend.process_mouse_wheel(x, y, modifiers); }
Status NativeParentHost::process_key_down(int key, int modifiers) { return impl_->backend.process_key_down(key, modifiers); }
Status NativeParentHost::process_key_up(int key, int modifiers) { return impl_->backend.process_key_up(key, modifiers); }
Status NativeParentHost::process_text_input(std::string_view text) { return impl_->backend.process_text_input(text); }
bool NativeParentHost::is_open() const noexcept { return impl_ && impl_->backend.is_open(); }
void* NativeParentHost::native_view() const noexcept { return (__bridge void*)impl_->view; }

} // namespace vellum::rmlui
