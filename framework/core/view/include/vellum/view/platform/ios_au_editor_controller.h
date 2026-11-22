#pragma once
#import <TargetConditionals.h>
#if TARGET_OS_IOS
#import <AudioToolbox/AudioToolbox.h>
#import <UIKit/UIKit.h>
typedef BOOL (^VellumAUv3ResizeRequest)(CGSize size);
@protocol VellumAUv3EditorSession <NSObject>
- (CGSize)editorSize;
- (BOOL)attachToEditorView:(UIView *)view;
- (void)editorHostResized:(CGSize)size;
- (void)editorScaleChanged:(CGFloat)scale;
- (void)editorVisibilityChanged:(BOOL)visible;
- (void)detachEditor;
@end
@protocol VellumAUv3EditorControllerContract <NSObject>
@property(nonatomic, strong) AUAudioUnit *boundUnit;
@property(nonatomic) CGSize preferredContentSize;
@property(nonatomic, readonly) UIView *view;
@property(nonatomic, readonly, getter=isViewLoaded) BOOL viewLoaded;
- (id<VellumAUv3EditorSession>)editorSessionForAudioUnit:(AUAudioUnit *)unit resizeRequest:(VellumAUv3ResizeRequest)resizeRequest;
- (void)closeEditor;
@end
@interface VellumAUv3EditorContainer : UIView
- (BOOL)openEditorForController:(id<VellumAUv3EditorControllerContract>)controller;
- (void)closeEditor;
@end
#endif
