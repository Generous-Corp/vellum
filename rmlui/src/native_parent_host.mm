#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#include <objc/runtime.h>

#include <vellum/rmlui/native_parent_host.hpp>

#include <RmlUi/Core/Input.h>

#include <cstdio>
#include <cmath>
#include <limits>
#include <memory>
#include <string>
#include <utility>


namespace vellum::rmlui {
namespace {

class NullLua final : public LuaInterface {
  public:
    Status load(std::string_view, std::string_view) override { return Status::rejected; }
    Status dispatch(std::string_view) override { return Status::rejected; }
};

// The host a view forwards to, held as an associated pointer so it can be
// cleared when the host invalidates while the view is still retained.
char kOwnerKey;

NativeParentHost* owner_of(id view) {
    NSValue* value = objc_getAssociatedObject(view, &kOwnerKey);
    return value ? static_cast<NativeParentHost*>(value.pointerValue) : nullptr;
}

void set_owner(id view, NativeParentHost* host) {
    objc_setAssociatedObject(view, &kOwnerKey, host ? [NSValue valueWithPointer:host] : nil,
                             OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}

int rml_modifiers(NSEventModifierFlags flags) {
    int state = 0;
    if (flags & NSEventModifierFlagControl) state |= Rml::Input::KM_CTRL;
    if (flags & NSEventModifierFlagShift) state |= Rml::Input::KM_SHIFT;
    if (flags & NSEventModifierFlagOption) state |= Rml::Input::KM_ALT;
    if (flags & NSEventModifierFlagCommand) state |= Rml::Input::KM_META;
    if (flags & NSEventModifierFlagCapsLock) state |= Rml::Input::KM_CAPSLOCK;
    return state;
}

// macOS virtual key codes (the ANSI layout's physical keys) to RmlUi key
// identifiers. Keys without an entry map to KI_UNKNOWN; their text still
// arrives through insertText:.
int rml_key(unsigned short code) {
    using namespace Rml::Input;
    switch (code) {
    case 0x00: return KI_A; case 0x0B: return KI_B; case 0x08: return KI_C; case 0x02: return KI_D;
    case 0x0E: return KI_E; case 0x03: return KI_F; case 0x05: return KI_G; case 0x04: return KI_H;
    case 0x22: return KI_I; case 0x26: return KI_J; case 0x28: return KI_K; case 0x25: return KI_L;
    case 0x2E: return KI_M; case 0x2D: return KI_N; case 0x1F: return KI_O; case 0x23: return KI_P;
    case 0x0C: return KI_Q; case 0x0F: return KI_R; case 0x01: return KI_S; case 0x11: return KI_T;
    case 0x20: return KI_U; case 0x09: return KI_V; case 0x0D: return KI_W; case 0x07: return KI_X;
    case 0x10: return KI_Y; case 0x06: return KI_Z;
    case 0x1D: return KI_0; case 0x12: return KI_1; case 0x13: return KI_2; case 0x14: return KI_3;
    case 0x15: return KI_4; case 0x17: return KI_5; case 0x16: return KI_6; case 0x1A: return KI_7;
    case 0x1C: return KI_8; case 0x19: return KI_9;
    case 0x24: return KI_RETURN; case 0x4C: return KI_NUMPADENTER; case 0x30: return KI_TAB;
    case 0x31: return KI_SPACE; case 0x33: return KI_BACK; case 0x35: return KI_ESCAPE;
    case 0x75: return KI_DELETE; case 0x73: return KI_HOME; case 0x77: return KI_END;
    case 0x74: return KI_PRIOR; case 0x79: return KI_NEXT;
    case 0x7B: return KI_LEFT; case 0x7C: return KI_RIGHT; case 0x7D: return KI_DOWN; case 0x7E: return KI_UP;
    case 0x7A: return KI_F1; case 0x78: return KI_F2; case 0x63: return KI_F3; case 0x76: return KI_F4;
    case 0x60: return KI_F5; case 0x61: return KI_F6; case 0x62: return KI_F7; case 0x64: return KI_F8;
    case 0x65: return KI_F9; case 0x6D: return KI_F10; case 0x67: return KI_F11; case 0x6F: return KI_F12;
    case 0x52: return KI_NUMPAD0; case 0x53: return KI_NUMPAD1; case 0x54: return KI_NUMPAD2;
    case 0x55: return KI_NUMPAD3; case 0x56: return KI_NUMPAD4; case 0x57: return KI_NUMPAD5;
    case 0x58: return KI_NUMPAD6; case 0x59: return KI_NUMPAD7; case 0x5B: return KI_NUMPAD8;
    case 0x5C: return KI_NUMPAD9; case 0x43: return KI_MULTIPLY; case 0x45: return KI_ADD;
    case 0x4E: return KI_SUBTRACT; case 0x41: return KI_DECIMAL; case 0x4B: return KI_DIVIDE;
    case 0x51: return KI_OEM_NEC_EQUAL;
    case 0x29: return KI_OEM_1; case 0x18: return KI_OEM_PLUS; case 0x2B: return KI_OEM_COMMA;
    case 0x1B: return KI_OEM_MINUS; case 0x2F: return KI_OEM_PERIOD; case 0x2C: return KI_OEM_2;
    case 0x32: return KI_OEM_3; case 0x21: return KI_OEM_4; case 0x2A: return KI_OEM_5;
    case 0x1E: return KI_OEM_6; case 0x27: return KI_OEM_7;
    default: return KI_UNKNOWN;
    }
}

// The pointer position in the context's top-left coordinates.
// The context is sized in physical pixels, so a view point (logical) is
// scaled by the host's scale before it reaches RmlUi.
void move_to_event(NSView* view, NativeParentHost* host, NSEvent* event) {
    const NSPoint p = [view convertPoint:event.locationInWindow fromView:nil];
    const double scale = host->size().scale > 0 ? host->size().scale : 1.0;
    host->process_mouse_move(static_cast<int>(std::lround(p.x * scale)),
                             static_cast<int>(std::lround((view.bounds.size.height - p.y) * scale)),
                             rml_modifiers(event.modifierFlags));
}

template <typename Body> void add_event_method(Class klass, SEL selector, Body body) {
    id block = ^(id self, NSEvent* event) {
        if (NativeParentHost* host = owner_of(self)) body((NSView*)self, host, event);
    };
    class_addMethod(klass, selector, imp_implementationWithBlock(block), "v@:@");
}

// The view class, built at run time under a name unique to this image: many
// plug-ins share one Objective-C runtime, and two images defining the same
// class name would bind either one's methods.
Class host_view_class() {
    static Class klass = [] {
        char name[96];
        std::snprintf(name, sizeof(name), "VellumRmlUiNativeHostView_%p",
                      reinterpret_cast<void*>(&host_view_class));
        if (Class existing = objc_getClass(name)) return existing;
        Class created = objc_allocateClassPair([NSView class], name, 0);

        class_addMethod(created, @selector(acceptsFirstResponder),
                        imp_implementationWithBlock(^BOOL(id) { return YES; }), "c@:");
        class_addMethod(created, @selector(acceptsFirstMouse:),
                        imp_implementationWithBlock(^BOOL(id, NSEvent*) { return YES; }), "c@:@");

        auto button = [](int index, bool down) {
            return [index, down](NSView* view, NativeParentHost* host, NSEvent* event) {
                if (down) [view.window makeFirstResponder:view];
                move_to_event(view, host, event);
                const int mods = rml_modifiers(event.modifierFlags);
                if (down) host->process_mouse_button_down(index, mods);
                else host->process_mouse_button_up(index, mods);
            };
        };
        add_event_method(created, @selector(mouseDown:), button(0, true));
        add_event_method(created, @selector(mouseUp:), button(0, false));
        add_event_method(created, @selector(rightMouseDown:), button(1, true));
        add_event_method(created, @selector(rightMouseUp:), button(1, false));
        add_event_method(created, @selector(otherMouseDown:), button(2, true));
        add_event_method(created, @selector(otherMouseUp:), button(2, false));
        auto move = [](NSView* view, NativeParentHost* host, NSEvent* event) {
            move_to_event(view, host, event);
        };
        add_event_method(created, @selector(mouseMoved:), move);
        add_event_method(created, @selector(mouseDragged:), move);
        add_event_method(created, @selector(rightMouseDragged:), move);
        add_event_method(created, @selector(otherMouseDragged:), move);
        add_event_method(created, @selector(mouseExited:), [](NSView*, NativeParentHost* host, NSEvent*) {
            host->process_mouse_leave();
        });
        add_event_method(created, @selector(scrollWheel:), [](NSView* view, NativeParentHost* host, NSEvent* event) {
            move_to_event(view, host, event);
            // RmlUi scrolls down for a positive delta; AppKit reports the opposite.
            const double scale = event.hasPreciseScrollingDeltas ? 1.0 / 10.0 : 1.0;
            host->process_mouse_wheel(static_cast<float>(-event.scrollingDeltaX * scale),
                                      static_cast<float>(-event.scrollingDeltaY * scale),
                                      rml_modifiers(event.modifierFlags));
        });
        add_event_method(created, @selector(keyDown:), [](NSView* view, NativeParentHost* host, NSEvent* event) {
            host->process_key_down(rml_key(event.keyCode), rml_modifiers(event.modifierFlags));
            // Text goes through the input system, which calls insertText:.
            if (!(event.modifierFlags & (NSEventModifierFlagCommand | NSEventModifierFlagControl)))
                [view interpretKeyEvents:@[event]];
        });
        add_event_method(created, @selector(keyUp:), [](NSView*, NativeParentHost* host, NSEvent* event) {
            host->process_key_up(rml_key(event.keyCode), rml_modifiers(event.modifierFlags));
        });
        class_addMethod(created, @selector(insertText:),
                        imp_implementationWithBlock(^(id self, id text) {
                            NativeParentHost* host = owner_of(self);
                            NSString* string = [text isKindOfClass:[NSAttributedString class]]
                                                   ? [text string] : text;
                            if (host && [string isKindOfClass:[NSString class]] && string.length)
                                host->process_text_input(string.UTF8String);
                        }),
                        "v@:@");
        // Editing commands (arrows, delete, return) arrive as key identifiers
        // through keyDown:, so the command selectors need no action here.
        class_addMethod(created, @selector(doCommandBySelector:),
                        imp_implementationWithBlock(^(id, SEL) {}), "v@::");
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
    NSView* view = nil;
    CGImageRef image = nil;
    bool visible = true;
    bool focused = false;

    void present(const SoftRenderer& soft, double scale) {
        if (!view || soft.width() == 0 || soft.height() == 0)
            return;
        // The image owns a copy of the frame: Core Animation reads it while
        // compositing, after the renderer may already have redrawn or resized
        // its own buffer.
        CFDataRef pixels = CFDataCreate(nullptr, soft.pixels().data(),
                                        static_cast<CFIndex>(soft.pixels().size()));
        CGDataProviderRef provider = CGDataProviderCreateWithCFData(pixels);
        CFRelease(pixels);
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGImageRef next = CGImageCreate(soft.width(), soft.height(), 8, 32, soft.width() * 4,
                                        space, kCGImageAlphaPremultipliedLast | kCGBitmapByteOrderDefault,
                                        provider, nullptr, false, kCGRenderingIntentDefault);
        CGColorSpaceRelease(space);
        CGDataProviderRelease(provider);
        if (image)
            CGImageRelease(image);
        image = next;
        view.layer.contentsScale = scale;
        view.layer.contents = (__bridge id)image;
    }
};

NativeParentHost::NativeParentHost(AssetProvider& assets, RenderInterface* renderer)
    : impl_(std::make_unique<Impl>(assets, renderer)) {
    soft_ = impl_->owned_soft.get();
}

NativeParentHost::~NativeParentHost() { invalidate(); }

Status NativeParentHost::open(std::uint32_t width, std::uint32_t height) {
    size_.width = width; size_.height = height; size_.scale = 1.0;
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
        set_owner(impl_->view, this);
        impl_->view.wantsLayer = YES;
        impl_->view.layer.opaque = NO;
        // Hover needs mouseMoved:, which AppKit sends only through a tracking area.
        [impl_->view addTrackingArea:[[NSTrackingArea alloc]
            initWithRect:NSZeroRect
                 options:NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited |
                         NSTrackingActiveAlways | NSTrackingInVisibleRect
                   owner:impl_->view
                userInfo:nil]];
    }
    if (impl_->view.superview != p) { [impl_->view removeFromSuperview]; [p addSubview:impl_->view]; }
    impl_->backend.on_attach();
    return true;
}
void NativeParentHost::detach() {
    impl_->backend.on_detach();
    [impl_->view removeFromSuperview];
}
void NativeParentHost::invalidate() {
    if (!impl_) return;
    detach();
    if (impl_->image) { CGImageRelease(impl_->image); impl_->image = nil; }
    if (impl_->view) set_owner(impl_->view, nullptr);
    impl_->view = nil;
    impl_->backend.close();
}
bool NativeParentHost::set_size(const NativeHostSize& s) {
    if (!s.width || !s.height || !std::isfinite(s.scale) || s.scale <= 0) return false;
    const auto physical = [](std::uint32_t value, double scale) {
        const double result = std::round(static_cast<double>(value) * scale);
        return result <= static_cast<double>(std::numeric_limits<std::uint32_t>::max())
                   ? static_cast<std::uint32_t>(result) : 0u;
    };
    const auto physical_width = physical(s.width, s.scale);
    const auto physical_height = physical(s.height, s.scale);
    if (!physical_width || !physical_height) return false;
    size_ = s;
    impl_->backend.set_density_ratio(static_cast<float>(s.scale));
    impl_->backend.set_dimensions(physical_width, physical_height);
    if (impl_->view) [impl_->view setFrameSize:NSMakeSize(s.width, s.height)];
    return true;
}
void NativeParentHost::idle() {
    impl_->backend.on_idle();
    if (!impl_->backend.is_open() || !impl_->visible) return;
    if (!impl_->backend.needs_render()) return;
    impl_->backend.render();
    if (soft_) impl_->present(*soft_, size_.scale);
}
void NativeParentHost::visibility_changed(bool visible) {
    impl_->visible = visible;
    impl_->backend.on_visibility_changed(visible);
    if (impl_->view) impl_->view.hidden = !visible;
}
void NativeParentHost::focus_changed(bool focused) { impl_->focused = focused; }
Status NativeParentHost::process_mouse_move(int x, int y, int modifiers) { return impl_->backend.process_mouse_move(x, y, modifiers); }
Status NativeParentHost::process_mouse_button_down(int button, int modifiers) { return impl_->backend.process_mouse_button_down(button, modifiers); }
Status NativeParentHost::process_mouse_button_up(int button, int modifiers) { return impl_->backend.process_mouse_button_up(button, modifiers); }
Status NativeParentHost::process_mouse_wheel(float x, float y, int modifiers) { return impl_->backend.process_mouse_wheel(x, y, modifiers); }
Status NativeParentHost::process_mouse_leave() { return impl_->backend.process_mouse_leave(); }
Status NativeParentHost::process_key_down(int key, int modifiers) { return impl_->backend.process_key_down(key, modifiers); }
Status NativeParentHost::process_key_up(int key, int modifiers) { return impl_->backend.process_key_up(key, modifiers); }
Status NativeParentHost::process_text_input(std::string_view text) { return impl_->backend.process_text_input(text); }
bool NativeParentHost::is_open() const noexcept { return impl_ && impl_->backend.is_open(); }
void* NativeParentHost::native_view() const noexcept { return (__bridge void*)impl_->view; }

} // namespace vellum::rmlui
