#import <vellum/view/platform/ios_au_editor_controller.h>

#if TARGET_OS_IOS

@interface VellumAUv3EditorContainer : UIView
- (BOOL)openEditorForController:(VellumAUv3EditorController *)controller;
- (void)closeEditor;
@end

@implementation VellumAUv3EditorContainer {
    id<VellumAUv3EditorSession> _session;
    BOOL _applyingEditorSize;
    CGFloat _pushedScale;
}

- (BOOL)openEditorForController:(VellumAUv3EditorController *)controller {
    __unsafe_unretained VellumAUv3EditorContainer *view = self;
    __unsafe_unretained VellumAUv3EditorController *owner = controller;
    _session = [[controller editorSessionForAudioUnit:controller.boundUnit
        resizeRequest:^BOOL(CGSize size) {
            if (![NSThread isMainThread] || size.width < 1 || size.height < 1 || !owner.isViewLoaded)
                return NO;
            return [view requestEditorSize:size controller:owner];
        }] retain];
    if (!_session)
        return NO;

    [self setFrameWithoutEcho:_session.editorSize];
    if (![_session attachToEditorView:self]) {
        [self closeEditor];
        return NO;
    }
    [self setFrameWithoutEcho:_session.editorSize];
    return YES;
}

- (void)closeEditor {
    id<VellumAUv3EditorSession> session = _session;
    _session = nil;
    [session detachEditor];
    [session release];
}

- (void)dealloc {
    [self closeEditor];
    [super dealloc];
}

- (void)setFrameWithoutEcho:(CGSize)size {
    const BOOL previous = _applyingEditorSize;
    _applyingEditorSize = YES;
    CGRect frame = self.frame;
    frame.size = size;
    self.frame = frame;
    _applyingEditorSize = previous;
}

- (BOOL)requestEditorSize:(CGSize)size controller:(VellumAUv3EditorController *)controller {
    const BOOL previous = _applyingEditorSize;
    _applyingEditorSize = YES;
    controller.preferredContentSize = size;
    _applyingEditorSize = previous;

    // Follow the host's bounds. A later layout acknowledges a deferred resize.
    [self setFrameWithoutEcho:self.superview ? self.superview.bounds.size : size];
    if (CGSizeEqualToSize(self.frame.size, size))
        [_session editorHostResized:size];
    return YES;
}

- (void)pushScale {
    const CGFloat scale = self.window.screen.scale;
    if (scale > 0 && scale != _pushedScale) {
        _pushedScale = scale;
        [_session editorScaleChanged:scale];
    }
}

- (void)layoutSubviews {
    [super layoutSubviews];
    const CGSize size = self.bounds.size;
    if (!_applyingEditorSize && size.width >= 1 && size.height >= 1)
        [_session editorHostResized:size];
    [self pushScale];
}

- (void)willMoveToWindow:(UIWindow *)window {
    [super willMoveToWindow:window];
    [_session editorVisibilityChanged:window != nil];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self pushScale];
}

@end

@implementation VellumAUv3EditorController {
    VellumAUv3EditorContainer *_editorView;
}

@synthesize boundUnit = _boundUnit;

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor blackColor];
    self.preferredContentSize = CGSizeMake(400, 300);
    [self rebuildEditorIfReady];
}

- (void)setBoundUnit:(AUAudioUnit *)unit {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ self.boundUnit = unit; });
        return;
    }
    if (_boundUnit == unit)
        return;
    // Detach while the old unit still owns the session's audio state.
    [self closeEditor];
    [_boundUnit release];
    _boundUnit = [unit retain];
    [self rebuildEditorIfReady];
}

- (AUAudioUnit *)createAudioUnitWithComponentDescription:(AudioComponentDescription)description
                                                  error:(NSError **)error {
    AUAudioUnit *unit = [self makeAudioUnitWithComponentDescription:description error:error];
    if (unit)
        self.boundUnit = unit;
    return unit;
}

- (AUAudioUnit *)makeAudioUnitWithComponentDescription:(AudioComponentDescription)description
                                                error:(NSError **)error {
    return nil;
}

- (BOOL)editorLaunchAllowed {
    return YES;
}

- (id<VellumAUv3EditorSession>)editorSessionForAudioUnit:(AUAudioUnit *)unit
                                         resizeRequest:(VellumAUv3ResizeRequest)resizeRequest {
    return nil;
}

- (void)rebuildEditorIfReady {
    if (!_boundUnit || !self.isViewLoaded || ![self editorLaunchAllowed])
        return;

    VellumAUv3EditorContainer *view = [[VellumAUv3EditorContainer alloc] initWithFrame:CGRectZero];
    if (![view openEditorForController:self]) {
        [view release];
        return;
    }
    _editorView = view;
    self.preferredContentSize = view.frame.size;
    view.frame = self.view.bounds;
    view.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:view];
}

- (void)closeEditor {
    VellumAUv3EditorContainer *view = _editorView;
    _editorView = nil;
    [view closeEditor];
    [view removeFromSuperview];
    [view release];
}

- (void)dealloc {
    // The session and its display callbacks share the main queue.
    __unsafe_unretained VellumAUv3EditorController *controller = self;
    if ([NSThread isMainThread])
        [controller closeEditor];
    else
        dispatch_sync(dispatch_get_main_queue(), ^{ [controller closeEditor]; });
    [_boundUnit release];
    [super dealloc];
}

@end
#endif
