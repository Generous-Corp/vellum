#import <AppKit/AppKit.h>
#include <pulp/view/appearance_tracker.hpp>

namespace pulp::view::platform {

Appearance detect_mac_appearance() {
    if (@available(macOS 10.14, *)) {
        NSAppearance* appearance = [NSApp effectiveAppearance];
        if (!appearance) appearance = [NSAppearance currentDrawingAppearance];
        if (!appearance) return Appearance::dark;

        NSAppearanceName best = [appearance bestMatchFromAppearancesWithNames:@[
            NSAppearanceNameAqua,
            NSAppearanceNameDarkAqua
        ]];

        if ([best isEqualToString:NSAppearanceNameAqua])
            return Appearance::light;
    }
    return Appearance::dark;
}

} // namespace pulp::view::platform
