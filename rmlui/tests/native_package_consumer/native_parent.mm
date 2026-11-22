#import <Cocoa/Cocoa.h>
#include <vellum/rmlui/native_parent_host.hpp>
#include <map>
using namespace vellum::rmlui;
struct Assets final : AssetProvider { Status load(std::string_view p, Asset& a) override { if (p != "x.rml") return Status::missing_asset; a.path = "x.rml"; a.bytes = "<rml><body><div/></body></rml>"; return Status::ok; } };
int main() { @autoreleasepool { Assets a; NativeParentHost h(a); if (h.open(16, 16) != Status::ok || h.load_document("x.rml") != Status::ok) return 1; NSView* p = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 16, 16)]; if (!h.attach({NativeParentKind::cocoa_view, (__bridge void*)p})) return 2; h.idle(); if (!h.default_renderer() || h.default_renderer()->pixels().empty()) return 3; h.invalidate(); } return 0; }
