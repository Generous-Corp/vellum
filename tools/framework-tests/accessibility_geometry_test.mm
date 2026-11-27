#include "accessibility_mac_geometry.hpp"
#include <cstdio>

@interface VellumGeometryTestHost : NSView
@property(nonatomic, copy) vellum::view::Point (^pointTransform)(vellum::view::Point);
@property(nonatomic) BOOL testFlipped;
@end
@implementation VellumGeometryTestHost
- (BOOL)isFlipped { return self.testFlipped; }
@end

int main() {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSWindow* window = [[NSWindow alloc]
            initWithContentRect:NSMakeRect(100, 150, 600, 400)
            styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
        [window setReleasedWhenClosed:NO];
        auto* host = [[VellumGeometryTestHost alloc] initWithFrame:NSMakeRect(11, 13, 400, 400)];
        [window.contentView addSubview:host];
        auto check = [&](NSRect expected_host_rect) {
            NSRect expected = [window convertRectToScreen:[host convertRect:expected_host_rect toView:nil]];
            NSRect actual = vellum::view::accessibility_rect_to_screen(host, {10, 20, 30, 40});
            if (!NSEqualRects(actual, expected)) {
                std::fprintf(stderr, "frame mismatch: actual %s expected %s\n",
                    NSStringFromRect(actual).UTF8String, NSStringFromRect(expected).UTF8String);
                return false;
            }
            return true;
        };
        if (!check(NSMakeRect(10, 340, 30, 40))) return 1;
        host.pointTransform = ^vellum::view::Point(vellum::view::Point p) {
            return {p.x / 2, (p.y - 100) / 2};
        };
        if (!check(NSMakeRect(20, 180, 60, 80))) return 1;
        host.pointTransform = ^vellum::view::Point(vellum::view::Point p) { return {p.x / 2, p.y / 2}; };
        if (!check(NSMakeRect(20, 280, 60, 80))) return 1;
        [host setFrameSize:NSMakeSize(600, 200)];
        host.pointTransform = ^vellum::view::Point(vellum::view::Point p) {
            return {(p.x - 100) / 2, p.y / 2};
        };
        if (!check(NSMakeRect(120, 80, 60, 80))) return 1;
        host.testFlipped = YES;
        if (!check(NSMakeRect(120, 40, 60, 80))) return 1;
        host.pointTransform = nil;
        if (!check(NSMakeRect(10, 20, 30, 40))) return 1;
        [host removeFromSuperview];
        [host release];
        [window close];
        [window release];
    }
    std::puts("PASS accessibility geometry: identity, scale, both letterbox axes, top alignment, resize, flipped host");
}
