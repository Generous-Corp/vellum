#pragma once

#import <CoreAudioKit/CoreAudioKit.h>
#import <vellum/view/platform/ios_au_editor_controller.h>

#if TARGET_OS_IOS
// AUv3 extension controller supplied by the Vellum integration.
@interface VellumAUv3EditorController : AUViewController <AUAudioUnitFactory, VellumAUv3EditorControllerContract>
@property(nonatomic, strong) AUAudioUnit *boundUnit;
- (AUAudioUnit *)makeAudioUnitWithComponentDescription:(AudioComponentDescription)description
                                                error:(NSError **)error;
- (BOOL)editorLaunchAllowed;
@end
#endif
