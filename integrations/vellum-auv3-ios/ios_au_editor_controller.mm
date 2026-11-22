#import <integrations/vellum-auv3-ios/ios_au_editor_controller.h>

#if TARGET_OS_IOS

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
