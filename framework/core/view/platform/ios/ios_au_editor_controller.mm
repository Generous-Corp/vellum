#import <vellum/view/platform/ios_au_editor_controller.h>
#if TARGET_OS_IOS
@interface VellumAUv3EditorContainer ()
- (void)setFrameWithoutEcho:(CGSize)size;
- (BOOL)requestEditorSize:(CGSize)size controller:(id<VellumAUv3EditorControllerContract>)controller;
- (void)pushScale;
@end
@implementation VellumAUv3EditorContainer {
    id<VellumAUv3EditorSession> _session;
    BOOL _applyingEditorSize;
    CGFloat _pushedScale;
}
- (BOOL)openEditorForController:(id<VellumAUv3EditorControllerContract>)controller {
    __unsafe_unretained VellumAUv3EditorContainer *view = self;
    __unsafe_unretained id<VellumAUv3EditorControllerContract> owner = controller;
    _session = [[controller editorSessionForAudioUnit:controller.boundUnit resizeRequest:^BOOL(CGSize size) {
        if (![NSThread isMainThread] || size.width < 1 || size.height < 1 || !owner.isViewLoaded) return NO;
        return [view requestEditorSize:size controller:owner];
    }] retain];
    if (!_session) return NO;
    [self setFrameWithoutEcho:_session.editorSize];
    if (![_session attachToEditorView:self]) { [self closeEditor]; return NO; }
    [self setFrameWithoutEcho:_session.editorSize];
    return YES;
}
- (void)closeEditor { id<VellumAUv3EditorSession> session = _session; _session = nil; [session detachEditor]; [session release]; }
- (void)dealloc { [self closeEditor]; [super dealloc]; }
- (void)setFrameWithoutEcho:(CGSize)size { const BOOL previous = _applyingEditorSize; _applyingEditorSize = YES; CGRect frame = self.frame; frame.size = size; self.frame = frame; _applyingEditorSize = previous; }
- (BOOL)requestEditorSize:(CGSize)size controller:(id<VellumAUv3EditorControllerContract>)controller { const BOOL previous = _applyingEditorSize; _applyingEditorSize = YES; controller.preferredContentSize = size; _applyingEditorSize = previous; [self setFrameWithoutEcho:self.superview ? self.superview.bounds.size : size]; if (CGSizeEqualToSize(self.frame.size, size)) [_session editorHostResized:size]; return YES; }
- (void)pushScale { const CGFloat scale = self.window.screen.scale; if (scale > 0 && scale != _pushedScale) { _pushedScale = scale; [_session editorScaleChanged:scale]; } }
- (void)layoutSubviews { [super layoutSubviews]; const CGSize size = self.bounds.size; if (!_applyingEditorSize && size.width >= 1 && size.height >= 1) [_session editorHostResized:size]; [self pushScale]; }
- (void)willMoveToWindow:(UIWindow *)window { [super willMoveToWindow:window]; [_session editorVisibilityChanged:window != nil]; }
- (void)didMoveToWindow { [super didMoveToWindow]; [self pushScale]; }
@end
#endif
