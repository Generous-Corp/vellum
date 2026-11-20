#import <Cocoa/Cocoa.h>

#include <vellum/rmlui/native_parent_host.hpp>
#include "test_support.hpp"

#include <cstdint>
#include <memory>
#include <string>

#include <RmlUi/Core/Input.h>

using namespace vellum::rmlui;

namespace {

class Assets final : public AssetProvider {
  public:
    Status load(std::string_view path, Asset& out) override {
        if (path != "editor.rml") return Status::missing_asset;
        out.path = "editor.rml";
        // One 20x20 box bound to the model's `on` field: red when off, blue
        // when on. A click toggles the field through the data model.
        out.bytes = "<rml><head><style>"
                    "#box { display:block; width:20px; height:20px; background-color:#ff0000; }"
                    "#box.on { background-color:#0000ff; }"
                    "</style></head><body data-model='part' "
                    "data-event-keydown='key = ev.key_identifier; shift = ev.shift_key' "
                    "data-event-textinput='text = ev.text'>"
                    "<div id='box' data-class-on='on' data-event-click='on = !on'/>"
                    "</body></rml>";
        return Status::ok;
    }
};

struct Toggle final : ModelHost {
    Status read(std::string_view field, ModelValue& out) override {
        if (field == "on") out = ModelValue::boolean_value(on);
        else if (field == "shift") out = ModelValue::number_value(shift);
        else if (field == "key") out = ModelValue::number_value(key);
        else if (field == "text") out = ModelValue::string_value(text);
        else return Status::rejected;
        return Status::ok;
    }
    Status write(std::string_view field, const ModelValue& value) override {
        if (field == "on" && value.kind == ModelValue::Kind::boolean) on = value.boolean;
        // RmlUi reports modifier flags as 0 or 1.
        else if (field == "shift" && value.kind == ModelValue::Kind::number) shift = value.number;
        else if (field == "key" && value.kind == ModelValue::Kind::number) key = value.number;
        else if (field == "text" && value.kind == ModelValue::Kind::string) text = value.string;
        else return Status::rejected;
        ++writes;
        return Status::ok;
    }
    bool on = false;
    double shift = 0;
    double key = 0;
    std::string text;
    int writes = 0;
};

// A mouse event at a top-left context point of a view whose window is nil,
// so window coordinates are the view's own (bottom-left) coordinates.
NSEvent* mouse(NSEventType type, NSView* view, double x, double y) {
    return [NSEvent mouseEventWithType:type
                              location:NSMakePoint(x, view.bounds.size.height - y)
                         modifierFlags:0
                             timestamp:0
                          windowNumber:0
                               context:nil
                           eventNumber:0
                            clickCount:1
                              pressure:1];
}

NSEvent* key(NSEventType type, unsigned short code, NSString* text, NSEventModifierFlags flags) {
    return [NSEvent keyEventWithType:type
                            location:NSZeroPoint
                       modifierFlags:flags
                           timestamp:0
                        windowNumber:0
                             context:nil
                          characters:text
         charactersIgnoringModifiers:text
                           isARepeat:NO
                             keyCode:code];
}

// The RGB of one pixel of the default renderer's premultiplied RGBA frame.
std::uint32_t rgb_at(const NativeParentHost& host, std::uint32_t x, std::uint32_t y) {
    const auto* soft = host.default_renderer();
    const auto& px = soft->pixels();
    const std::size_t i = (static_cast<std::size_t>(y) * soft->width() + x) * 4;
    return (std::uint32_t(px[i]) << 16) | (std::uint32_t(px[i + 1]) << 8) | px[i + 2];
}

} // namespace

int main() {
    @autoreleasepool {
        Assets assets;
        auto model = std::make_shared<Toggle>();
        NativeParentHost host(assets);
        VELLUM_RMLUI_CHECK(host.open(32, 32) == Status::ok);
        VELLUM_RMLUI_CHECK(host.bind_model("part", model, {"on", "shift", "key", "text"}) == Status::ok);
        VELLUM_RMLUI_CHECK(host.load_document("editor.rml") == Status::ok);

        // A parent view that belongs to no window: nothing is ordered on screen.
        NSView* parent = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 32, 32)];
        VELLUM_RMLUI_CHECK(host.attach({NativeParentKind::cocoa_view, (__bridge void*)parent}));
        NSView* child = (__bridge NSView*)host.native_view();
        VELLUM_RMLUI_CHECK(child != nil);
        VELLUM_RMLUI_CHECK(child.superview == parent);

        host.idle();
        VELLUM_RMLUI_CHECK(host.default_renderer() != nullptr);
        VELLUM_RMLUI_CHECK(host.default_renderer()->pixels().size() == 32u * 32u * 4u);
        VELLUM_RMLUI_CHECK(child.layer.contents != nil);
        VELLUM_RMLUI_CHECK(rgb_at(host, 5, 5) == 0xff0000u);

        // A model change repaints the bound element.
        model->on = true;
        VELLUM_RMLUI_CHECK(host.dirty_model("part", "on") == Status::ok);
        host.idle();
        VELLUM_RMLUI_CHECK(rgb_at(host, 5, 5) == 0x0000ffu);

        // A click reaches the document and writes the model back.
        VELLUM_RMLUI_CHECK(host.process_mouse_move(5, 5) == Status::ok);
        VELLUM_RMLUI_CHECK(host.process_mouse_button_down(0) == Status::ok);
        VELLUM_RMLUI_CHECK(host.process_mouse_button_up(0) == Status::ok);
        host.idle();
        VELLUM_RMLUI_CHECK(model->writes >= 1);
        VELLUM_RMLUI_CHECK(!model->on);
        VELLUM_RMLUI_CHECK(rgb_at(host, 5, 5) == 0xff0000u);

        // The same input through the view's own AppKit handlers. The pointer
        // is first moved away, so a click must carry its own position.
        VELLUM_RMLUI_CHECK(host.process_mouse_move(30, 30) == Status::ok);
        [child mouseDown:mouse(NSEventTypeLeftMouseDown, child, 5, 5)];
        [child mouseUp:mouse(NSEventTypeLeftMouseUp, child, 5, 5)];
        host.idle();
        VELLUM_RMLUI_CHECK(model->on);
        VELLUM_RMLUI_CHECK(rgb_at(host, 5, 5) == 0x0000ffu);

        // A shifted key reaches RmlUi as its key identifier and modifier, and
        // its text through the input system.
        [child keyDown:key(NSEventTypeKeyDown, 0x00, @"A", NSEventModifierFlagShift)];
        [child keyUp:key(NSEventTypeKeyUp, 0x00, @"A", NSEventModifierFlagShift)];
        host.idle();
        VELLUM_RMLUI_CHECK(static_cast<int>(model->key) == Rml::Input::KI_A);
        VELLUM_RMLUI_CHECK(model->shift == 1.0);
        VELLUM_RMLUI_CHECK(model->text == "A");

        // The presented layer owns its frame and carries the host's scale.
        VELLUM_RMLUI_CHECK(host.set_size({32, 32, 2.0}));
        host.idle();
        VELLUM_RMLUI_CHECK(child.layer.contentsScale == 2.0);

        host.detach();
        VELLUM_RMLUI_CHECK(child.superview == nil);
        host.invalidate();
    }
    return 0;
}
