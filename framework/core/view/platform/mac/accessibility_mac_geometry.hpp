#pragma once

#include <vellum/view/geometry.hpp>
#import <Cocoa/Cocoa.h>
#include <cmath>

// Hosts publish their inverse viewport map for input. Reuse it for the
// forward accessibility map so resize and top alignment cannot drift.
@protocol VellumAccessibilityPointTransform
- (vellum::view::Point (^)(vellum::view::Point))pointTransform;
@end

namespace vellum::view {

inline NSRect accessibility_rect_to_screen(NSView* host, Rect bounds) {
    // The host input map is affine and axis aligned: root = (host - offset) / scale.
    // Sample it at two points to recover the same scale and letterbox offset
    // used for painting. Ordinary window hosts without a map keep identity.
    float sx = 1, sy = 1, tx = 0, ty = 0;
    if ([host respondsToSelector:@selector(pointTransform)]) {
        auto inverse = [(id<VellumAccessibilityPointTransform>)host pointTransform];
        if (inverse) {
            const auto origin = inverse({0, 0});
            const auto unit = inverse({1, 1});
            const float dx = unit.x - origin.x, dy = unit.y - origin.y;
            if (!std::isfinite(dx) || !std::isfinite(dy) || dx <= 0 || dy <= 0)
                return NSZeroRect;
            sx = 1 / dx; sy = 1 / dy;
            tx = -origin.x * sx; ty = -origin.y * sy;
        }
    }
    const CGFloat x = bounds.x * sx + tx, y = bounds.y * sy + ty;
    const CGFloat width = bounds.width * sx, height = bounds.height * sy;
    const CGFloat cocoaY = host.isFlipped ? y : NSHeight(host.bounds) - y - height;
    NSRect viewRect = NSMakeRect(x, cocoaY, width, height);
    NSRect windowRect = [host convertRect:viewRect toView:nil];
    NSRect screenRect = [host.window convertRectToScreen:windowRect];
    return screenRect;
}

}  // namespace vellum::view
