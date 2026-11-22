#import <integrations/vellum-auv3-ios/ios_au_editor_controller.h>

#import "au_audio_unit.h"

#include <pulp/format/contract_editor_session.hpp>
#include <pulp/format/detail/editor_environment.hpp>
#include <pulp/format/processor.hpp>

#include <utility>

@interface VellumPulpAUv3Session : NSObject <VellumAUv3EditorSession>
- (instancetype)initWithUnit:(PulpAudioUnit *)unit
              resizeRequest:(VellumAUv3ResizeRequest)resizeRequest;
@end

@implementation VellumPulpAUv3Session {
    std::shared_ptr<pulp::format::ContractEditorSession> _session;
    pulp::format::ContractSessionRegistry *_registry;
    VellumAUv3ResizeRequest _resizeRequest;
}

- (instancetype)initWithUnit:(PulpAudioUnit *)unit
              resizeRequest:(VellumAUv3ResizeRequest)resizeRequest {
    if (!(self = [super init]))
        return nil;
    auto *processor = [unit pulpProcessor];
    auto *store = [unit pulpStore];
    auto *factory = processor ? processor->editor_factory() : nullptr;
    if (factory && store) {
        _registry = [unit pulpContractSessions];
        _resizeRequest = [resizeRequest copy];
        __unsafe_unretained VellumPulpAUv3Session *owner = self;
        pulp::format::ContractEditorSession::HostHooks hooks;
        hooks.request_resize = [owner](const pulp::format::EditorSize& size) {
            return owner->_resizeRequest &&
                owner->_resizeRequest(CGSizeMake(size.width, size.height));
        };
        _session = pulp::format::ContractEditorSession::create(
            *processor, *store, *factory, std::move(hooks));
    }
    if (!_session) {
        [self release];
        return nil;
    }
    return self;
}

- (CGSize)editorSize {
    const auto size = _session->size();
    return CGSizeMake(size.width, size.height);
}

- (BOOL)attachToEditorView:(UIView *)view {
    pulp::format::NativeParent parent;
    parent.kind = pulp::format::NativeParentKind::uikit_view;
    parent.handle = (__bridge void *)view;
    if (!_session->attach(parent))
        return NO;
    if (_registry)
        _session->register_with(*_registry);
    return YES;
}

- (void)editorHostResized:(CGSize)size {
    auto width = static_cast<std::uint32_t>(size.width);
    auto height = static_cast<std::uint32_t>(size.height);
    if (_session->editor() == nullptr) {
        _session->set_size(width, height);
        return;
    }
    bool snapped = false;
    _session->set_host_size(width, height, snapped);
}

- (void)editorScaleChanged:(CGFloat)scale {
    _session->set_scale(scale);
}

- (void)editorVisibilityChanged:(BOOL)visible {
    _session->visibility_changed(visible);
}

- (void)detachEditor {
    [_resizeRequest release];
    _resizeRequest = nil;
    if (_session) {
        _session->detach();
        _session.reset();
    }
}

- (void)dealloc {
    [self detachEditor];
    [super dealloc];
}

@end

// Matches the extension's existing principal class.
@interface PulpAUViewController : VellumAUv3EditorController
@property(nonatomic, strong) AUAudioUnit *audioUnit;
@end

@implementation PulpAUViewController

- (AUAudioUnit *)audioUnit {
    return self.boundUnit;
}

- (void)setAudioUnit:(AUAudioUnit *)unit {
    self.boundUnit = unit;
}

- (AUAudioUnit *)makeAudioUnitWithComponentDescription:(AudioComponentDescription)description
                                                error:(NSError **)error {
    return [[[PulpAudioUnit alloc] initWithComponentDescription:description error:error] autorelease];
}

- (BOOL)editorLaunchAllowed {
    return !pulp::format::detail::editor_launch_blocked_by_environment();
}

- (id<VellumAUv3EditorSession>)editorSessionForAudioUnit:(AUAudioUnit *)unit
                                         resizeRequest:(VellumAUv3ResizeRequest)resizeRequest {
    if (![unit isKindOfClass:[PulpAudioUnit class]])
        return nil;
    return [[[VellumPulpAUv3Session alloc] initWithUnit:(PulpAudioUnit *)unit
                                       resizeRequest:resizeRequest] autorelease];
}

@end
