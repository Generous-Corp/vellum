#import <Cocoa/Cocoa.h>

#include <vellum/rmlui/native_parent_host.hpp>
#include "test_support.hpp"

#include <map>
#include <memory>

using namespace vellum::rmlui;

namespace {
class Assets final : public AssetProvider {
  public:
    Status load(std::string_view path, Asset& out) override {
        if (path != "editor.rml") return Status::missing_asset;
        out.path = "editor.rml";
        out.bytes = "<rml><head><style>#box { display:block; width:20px; height:20px; background-color:#ff0000; }</style></head><body><div id='box'/></body></rml>";
        return Status::ok;
    }
};
}

int main() {
    @autoreleasepool {
        Assets assets;
        NativeParentHost host(assets);
        VELLUM_RMLUI_CHECK(host.open(32, 32) == Status::ok);
        VELLUM_RMLUI_CHECK(host.load_document("editor.rml") == Status::ok);
        NSView* parent = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 32, 32)];
        VELLUM_RMLUI_CHECK(host.attach({NativeParentKind::cocoa_view, (__bridge void*)parent}));
        host.idle();
        VELLUM_RMLUI_CHECK(host.default_renderer() != nullptr);
        VELLUM_RMLUI_CHECK(host.default_renderer()->pixels().size() == 32u * 32u * 4u);
        host.detach();
        host.invalidate();
    }
    return 0;
}
