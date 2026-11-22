#pragma once

#import <TargetConditionals.h>

#if TARGET_OS_IOS
#import <CoreAudioKit/CoreAudioKit.h>

typedef BOOL (^VellumAUv3ResizeRequest)(CGSize size);

// The audio adapter owns the editor contract. All session calls run on main.
@protocol VellumAUv3EditorSession <NSObject>
- (CGSize)editorSize;
- (BOOL)attachToEditorView:(UIView *)view;
- (void)editorHostResized:(CGSize)size;
- (void)editorScaleChanged:(CGFloat)scale;
- (void)editorVisibilityChanged:(BOOL)visible;
- (void)detachEditor;
@end

// Use a plug-in-specific subclass as the extension's principal class.
@interface VellumAUv3EditorController : AUViewController <AUAudioUnitFactory>

// Read on main. Assignments from the factory queue are applied on main.
@property(nonatomic, strong) AUAudioUnit *boundUnit;

// Override in the audio adapter. Return nil if the unit cannot be created.
- (AUAudioUnit *)makeAudioUnitWithComponentDescription:(AudioComponentDescription)description
                                                error:(NSError **)error;
- (BOOL)editorLaunchAllowed;

// Return a new, unattached session, or nil if the unit has no editor.
// A session that retains resizeRequest must copy it and drop it on detach.
- (id<VellumAUv3EditorSession>)editorSessionForAudioUnit:(AUAudioUnit *)unit
                                         resizeRequest:(VellumAUv3ResizeRequest)resizeRequest;
@end
#endif
