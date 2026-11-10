#include "accessibility_mac_host_lifetime.hpp"

#import <Cocoa/Cocoa.h>
#import <objc/runtime.h>

#include "pulp_mac_objc_names.h"

@interface VellumMirrorAccessibilityHostLifetime : NSObject {
@public
    std::shared_ptr<const std::uint8_t> token;
}
@end

@implementation VellumMirrorAccessibilityHostLifetime
- (instancetype)init {
    self = [super init];
    if (self)
        token = std::make_shared<const std::uint8_t>(0);
    return self;
}
@end

namespace vellum::view {
namespace {
char g_accessibility_host_lifetime_key;
}

std::weak_ptr<const std::uint8_t>
capture_accessibility_host_lifetime(NSView* host) {
    if (!host) return {};
    auto* holder = static_cast<VellumMirrorAccessibilityHostLifetime*>(
        objc_getAssociatedObject(host, &g_accessibility_host_lifetime_key));
    if (!holder) {
        holder = [[VellumMirrorAccessibilityHostLifetime alloc] init];
        objc_setAssociatedObject(
            host, &g_accessibility_host_lifetime_key, holder,
            OBJC_ASSOCIATION_RETAIN_NONATOMIC);
#if !__has_feature(objc_arc)
        [holder release];
#endif
    }
    return holder->token;
}

} // namespace vellum::view
