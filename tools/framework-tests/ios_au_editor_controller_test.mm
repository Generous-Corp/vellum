#import <integrations/vellum-auv3-ios/ios_au_editor_controller.h>

#include <vellum/view/plugin_view_host.hpp>

#include <cstdio>
#include <cstdlib>

namespace {
using vellum::view::PluginViewHost;
using vellum::view::View;

void check(bool condition, const char *expression, int line) {
    if (!condition) {
        std::fprintf(stderr, "line %d: %s\n", line, expression);
        std::abort();
    }
}

#define CHECK(...) check((__VA_ARGS__), #__VA_ARGS__, __LINE__)

struct Calls {
    unsigned opened = 0;
    unsigned detached = 0;
    CGSize size = CGSizeZero;
    UIView *parent = nil;
    VellumAUv3ResizeRequest resize = nil;
    unsigned live_units = 0;
};
}

@interface IOSAUProofUnit : AUAudioUnit
@end

@implementation IOSAUProofUnit {
    Calls *_calls;
}

- (instancetype)initWithCalls:(Calls *)calls {
    AudioComponentDescription desc{};
    desc.componentType = kAudioUnitType_Effect;
    desc.componentSubType = 'TstE';
    desc.componentManufacturer = 'Vlum';
    if ((self = [super initWithComponentDescription:desc options:0 error:nil])) {
        _calls = calls;
        ++calls->live_units;
    }
    return self;
}

- (void)dealloc {
    --_calls->live_units;
    [super dealloc];
}
@end

@interface IOSAUProofSession : NSObject <VellumAUv3EditorSession>
- (instancetype)initWithCalls:(Calls *)calls resize:(VellumAUv3ResizeRequest)resize;
@end

@implementation IOSAUProofSession {
    Calls *_calls;
    std::unique_ptr<View> _root;
    std::unique_ptr<PluginViewHost> _host;
}

- (instancetype)initWithCalls:(Calls *)calls resize:(VellumAUv3ResizeRequest)resize {
    if ((self = [super init])) {
        _calls = calls;
        _calls->resize = [resize copy];
        _root = std::make_unique<View>();
        _host = PluginViewHost::create(*_root, PluginViewHost::Size{400, 300});
    }
    return self;
}

- (CGSize)editorSize { return CGSizeMake(400, 300); }

- (BOOL)attachToEditorView:(UIView *)view {
    if (!_host || !_host->try_attach_to_parent((__bridge void *)view))
        return NO;
    CHECK([NSThread isMainThread]);
    _calls->parent = view;
    ++_calls->opened;
    return YES;
}

- (void)editorHostResized:(CGSize)size {
    _calls->size = size;
    _host->set_size(static_cast<unsigned>(size.width), static_cast<unsigned>(size.height));
}

- (void)editorScaleChanged:(CGFloat)scale {}
- (void)editorVisibilityChanged:(BOOL)visible {}

- (void)detachEditor {
    CHECK([NSThread isMainThread]);
    CHECK(_calls->live_units > 0);
    [_calls->resize release];
    _calls->resize = nil;
    _host->detach();
    _host.reset();
    _calls->parent = nil;
    ++_calls->detached;
}
@end

@interface IOSAUProofController : VellumAUv3EditorController
@property(nonatomic) Calls *calls;
@property(nonatomic) BOOL hasEditor;
@end

@implementation IOSAUProofController

- (AUAudioUnit *)makeAudioUnitWithComponentDescription:(AudioComponentDescription)desc
                                                error:(NSError **)error {
    return [[[IOSAUProofUnit alloc] initWithCalls:self.calls] autorelease];
}

- (id<VellumAUv3EditorSession>)editorSessionForAudioUnit:(AUAudioUnit *)unit
                                         resizeRequest:(VellumAUv3ResizeRequest)resizeRequest {
    if (!self.hasEditor)
        return nil;
    return [[[IOSAUProofSession alloc] initWithCalls:self.calls resize:resizeRequest] autorelease];
}

@end

namespace {
void exercise_controller(bool view_first, bool clear_unit) {
    Calls calls;
    @autoreleasepool {
        IOSAUProofController *controller = [[IOSAUProofController alloc] init];
        controller.calls = &calls;
        controller.hasEditor = YES;
        if (view_first)
            [controller loadViewIfNeeded];

        AudioComponentDescription desc{};
        CHECK([controller createAudioUnitWithComponentDescription:desc error:nullptr] != nil);
        CHECK(calls.opened == (view_first ? 1u : 0u));
        [controller loadViewIfNeeded];
        CHECK(calls.opened == 1);
        CHECK(calls.parent.superview == controller.view);
        CHECK(calls.parent.subviews.count == 1);
        CHECK(CGSizeEqualToSize(controller.preferredContentSize, CGSizeMake(400, 300)));

        controller.view.frame = CGRectMake(0, 0, 640, 480);
        [calls.parent layoutIfNeeded];
        CHECK(CGSizeEqualToSize(calls.size, CGSizeMake(640, 480)));
        CHECK(calls.resize(CGSizeMake(720, 540)));
        CHECK(CGSizeEqualToSize(controller.preferredContentSize, CGSizeMake(720, 540)));
        CHECK(CGSizeEqualToSize(calls.parent.frame.size, CGSizeMake(640, 480)));
        controller.view.frame = CGRectMake(0, 0, 720, 540);
        [calls.parent layoutIfNeeded];
        CHECK(CGSizeEqualToSize(calls.size, CGSizeMake(720, 540)));

        if (clear_unit) {
            controller.boundUnit = nil;
            CHECK(calls.detached == 1);
            CHECK(controller.view.subviews.count == 0);

            controller.hasEditor = NO;
            CHECK([controller createAudioUnitWithComponentDescription:desc error:nullptr] != nil);
            CHECK(calls.opened == 1);
            CHECK(controller.view.subviews.count == 0);
        }
        [controller release];
    }
    CHECK(calls.detached == 1);
    CHECK(calls.resize == nil);
    CHECK(calls.live_units == 0);
}
}

// The factory can run before the main queue applies the binding.
void exercise_factory_queue() {
    Calls calls;
    @autoreleasepool {
        IOSAUProofController *controller = [[IOSAUProofController alloc] init];
        controller.calls = &calls;
        controller.hasEditor = YES;
        [controller loadViewIfNeeded];

        auto group = dispatch_group_create();
        dispatch_group_async(group, dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            @autoreleasepool {
                AudioComponentDescription desc{};
                CHECK([controller createAudioUnitWithComponentDescription:desc error:nullptr] != nil);
            }
        });
        CHECK(dispatch_group_wait(group, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        dispatch_release(group);

        CHECK(calls.opened == 0);
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:1];
        while (calls.opened == 0 && deadline.timeIntervalSinceNow > 0)
            [NSRunLoop.mainRunLoop runMode:NSDefaultRunLoopMode beforeDate:deadline];
        CHECK(calls.opened == 1);
        CHECK(controller.boundUnit != nil);
        [controller release];
    }
    CHECK(calls.detached == 1);
    CHECK(calls.live_units == 0);
}

int main() {
    exercise_controller(true, true);
    exercise_controller(false, false);
    exercise_factory_queue();
}
