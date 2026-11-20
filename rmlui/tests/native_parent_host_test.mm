#import <Cocoa/Cocoa.h>

#include <vellum/rmlui/native_parent_host.hpp>
#include "test_support.hpp"

#include <cstdint>
#include <memory>

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
                    "</style></head><body data-model='part'>"
                    "<div id='box' data-class-on='on' data-event-click='on = !on'/>"
                    "</body></rml>";
        return Status::ok;
    }
};

struct Toggle final : ModelHost {
    Status read(std::string_view field, ModelValue& out) override {
        if (field != "on") return Status::rejected;
        out = ModelValue::boolean_value(on);
        return Status::ok;
    }
    Status write(std::string_view field, const ModelValue& value) override {
        if (field != "on" || value.kind != ModelValue::Kind::boolean) return Status::rejected;
        on = value.boolean;
        ++writes;
        return Status::ok;
    }
    bool on = false;
    int writes = 0;
};

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
        VELLUM_RMLUI_CHECK(host.bind_model("part", model, {"on"}) == Status::ok);
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

        host.detach();
        VELLUM_RMLUI_CHECK(child.superview == nil);
        host.invalidate();
    }
    return 0;
}
