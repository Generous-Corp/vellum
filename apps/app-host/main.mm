#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <vellum/authoring/js_application.hpp>
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include "component_registry.hpp"
#include "macos_accessibility.hpp"
#include "options.hpp"
#include "text_semantics.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace {

using vellum::authoring::Interaction;
using vellum::authoring::JsApplication;
using vellum::authoring::RenderedApplication;
using vellum::authoring::TextInputControl;
using vellum::graphics::SkiaDawnSurface;
using vellum::app_host::ComponentModuleSpec;
using vellum::app_host::ComponentRegistry;
using vellum::app_host::MacAccessibilityBridge;
using vellum::app_host::AutomationStep;
using vellum::app_host::Options;
using vellum::app_host::parse_options;
using namespace vellum::app_host::text_semantics;

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    return utf8 == nullptr ? std::string{} : std::string{utf8};
}

constexpr std::size_t kMaximumStateBytes = 16U * 1024U * 1024U;
std::filesystem::path bundled_application_script() {
    NSString* resource = [NSBundle.mainBundle pathForResource:@"app" ofType:@"js"];
    return resource == nil ? std::filesystem::path{} :
                             std::filesystem::path(resource.UTF8String);
}

std::vector<ComponentModuleSpec> bundled_component_modules() {
    NSString* plugins = NSBundle.mainBundle.builtInPlugInsPath;
    if (plugins == nil) return {};
    const std::filesystem::path directory =
        std::filesystem::path(plugins.UTF8String) / "VellumComponents";
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error)) return {};
    std::vector<ComponentModuleSpec> output;
    for (const auto& entry : std::filesystem::directory_iterator(directory, error)) {
        if (error) return {};
        if (entry.is_regular_file() && entry.path().extension() == ".dylib") {
            output.push_back({.component_id = entry.path().stem().string(),
                              .path = entry.path()});
        }
    }
    std::sort(output.begin(), output.end(), [](const auto& left, const auto& right) {
        return left.component_id < right.component_id;
    });
    return output;
}

bool read_file(const std::filesystem::path& path, std::string& output) {
    std::ifstream input(path, std::ios::binary);
    std::ostringstream contents;
    contents << input.rdbuf();
    if (!input || contents.str().empty()) return false;
    output = contents.str();
    return true;
}

CAMetalLayer* make_metal_layer(std::uint32_t width, std::uint32_t height, float scale) {
    CAMetalLayer* layer = [CAMetalLayer layer];
    layer.device = MTLCreateSystemDefaultDevice();
    layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    layer.framebufferOnly = NO;
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(width * scale, height * scale);
    return layer;
}

bool validate_gpu(const SkiaDawnSurface& surface, bool native, std::string* error) {
    const auto& evidence = surface.evidence();
    if (evidence.available && evidence.native_surface == native && !evidence.fallback &&
        evidence.renderer == "Skia Graphite" && evidence.backend == "Metal") {
        return true;
    }
    if (error) *error = "renderer is not native Skia Graphite on Dawn/Metal";
    return false;
}

bool load_components(const std::vector<ComponentModuleSpec>& specs,
                     ComponentRegistry& registry, std::string* error) {
    for (const auto& spec : specs) {
        if (!registry.load(spec, error)) return false;
    }
    return true;
}

bool read_state_file(const std::filesystem::path& path, std::string& output,
                     std::string* error) {
    if (path.empty() || !std::filesystem::exists(path)) return true;
    std::error_code filesystem_error;
    if (!std::filesystem::is_regular_file(path, filesystem_error) || filesystem_error ||
        std::filesystem::file_size(path, filesystem_error) > kMaximumStateBytes ||
        filesystem_error) {
        if (error) *error = "persisted state is not a regular file within 16 MiB";
        return false;
    }
    std::ifstream input(path, std::ios::binary);
    std::ostringstream contents;
    contents << input.rdbuf();
    if (!input || contents.str().empty()) {
        if (error) *error = "could not read persisted state";
        return false;
    }
    output = contents.str();
    return true;
}

bool persist_state(JsApplication& application, const std::filesystem::path& path,
                   std::string* error) {
    if (path.empty()) return true;
    std::string snapshot;
    if (!application.snapshot_state(snapshot, error) || snapshot.size() > kMaximumStateBytes) {
        if (error && error->empty()) *error = "state snapshot exceeds 16 MiB";
        return false;
    }
    std::error_code filesystem_error;
    if (!path.parent_path().empty()) {
        std::filesystem::create_directories(path.parent_path(), filesystem_error);
    }
    if (filesystem_error) {
        if (error) *error = "could not create persisted state directory";
        return false;
    }
    NSData* data = [NSData dataWithBytes:snapshot.data() length:snapshot.size()];
    NSURL* url = [NSURL fileURLWithPath:
        [[NSString alloc] initWithUTF8String:path.string().c_str()]];
    NSError* write_error = nil;
    if (![data writeToURL:url options:NSDataWritingAtomic error:&write_error]) {
        if (error) *error = "could not atomically write persisted state";
        return false;
    }
    return true;
}

bool restore_persisted_state(JsApplication& application, RenderedApplication& rendered,
                             const std::filesystem::path& path, std::string* error) {
    std::string snapshot;
    if (!read_state_file(path, snapshot, error)) return false;
    return snapshot.empty() || application.restore_state(snapshot, rendered, error);
}

bool write_png(SkiaDawnSurface& surface, const std::filesystem::path& output,
               std::size_t& byte_count, std::string* error) {
    std::vector<std::uint8_t> png;
    if (!surface.capture_png(png, error) || png.size() < 8U ||
        png[0] != 0x89U || png[1] != 0x50U || png[2] != 0x4EU || png[3] != 0x47U) {
        if (error && error->empty()) *error = "GPU capture did not produce a PNG";
        return false;
    }
    std::error_code filesystem_error;
    std::filesystem::create_directories(output.parent_path(), filesystem_error);
    if (filesystem_error) {
        if (error) *error = "could not create capture directory";
        return false;
    }
    std::ofstream file(output, std::ios::binary);
    file.write(reinterpret_cast<const char*>(png.data()),
               static_cast<std::streamsize>(png.size()));
    if (!file) {
        if (error) *error = "could not write capture";
        return false;
    }
    byte_count = png.size();
    return true;
}

int run_headless(const Options& options, std::string_view bundle) {
    @autoreleasepool {
        std::string error;
        ComponentRegistry components;
        if (!load_components(options.components, components, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        auto application = JsApplication::create(bundle, &error);
        RenderedApplication rendered;
        if (!application || !application->render(rendered, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        if (!restore_persisted_state(*application, rendered, options.state_file, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        vellum::authoring::PumpResult idle;
        if (!application->wait_for_idle(4096, rendered, idle, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        if (!components.expand(rendered.scene, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        for (const auto& step : options.steps) {
            bool succeeded = false;
            switch (step.kind) {
                case AutomationStep::Kind::press:
                    succeeded = press_node(*application, rendered, step.node_id, &error);
                    break;
                case AutomationStep::Kind::input:
                    succeeded = input_node(
                        *application, rendered, step.node_id, step.value,
                        @"scenario", &error);
                    break;
                case AutomationStep::Kind::key:
                    succeeded = key_node(
                        *application, rendered, step.node_id, step.value,
                        @"scenario", &error);
                    break;
                case AutomationStep::Kind::focus:
                    succeeded = focus_node(rendered, step.node_id, &error);
                    break;
                case AutomationStep::Kind::compose:
                    succeeded = compose_node(
                        *application, rendered, step.node_id, step.value, &error);
                    break;
                case AutomationStep::Kind::assert_accessibility:
                    succeeded = assert_accessibility_node(
                        rendered, step.node_id, step.value, &error);
                    break;
            }
            if (!succeeded ||
                !application->wait_for_idle(4096, rendered, idle, &error) ||
                !components.expand(rendered.scene, &error) ||
                !persist_state(*application, options.state_file, &error)) {
                std::cerr << error << '\n';
                return 1;
            }
        }
        const auto width = static_cast<std::uint32_t>(rendered.scene.width);
        const auto height = static_cast<std::uint32_t>(rendered.scene.height);
        if ((options.expected_width && *options.expected_width != width) ||
            (options.expected_height && *options.expected_height != height)) {
            std::cerr << "rendered viewport " << width << 'x' << height
                      << " does not match scenario viewport\n";
            return 1;
        }
        auto surface = SkiaDawnSurface::create(
            {.width = width, .height = height, .scale = 1.0F}, &error);
        if (!surface || !validate_gpu(*surface, false, &error) ||
            !surface->render(rendered.scene, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        std::vector<std::uint8_t> rgba;
        std::uint32_t pixel_width = 0;
        std::uint32_t pixel_height = 0;
        if (!surface->capture_rgba(rgba, pixel_width, pixel_height, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        const auto stats = vellum::graphics::analyze_capture_rgba(
            rgba, pixel_width, pixel_height);
        if (!vellum::graphics::passes_content_floor(stats)) {
            std::cerr << "rendered frame failed content floor: colors="
                      << stats.unique_colors << " stddev="
                      << stats.luminance_standard_deviation << " content="
                      << stats.non_background_pixels << '\n';
            return 1;
        }
        std::size_t png_bytes = 0;
        if (!options.capture.empty() &&
            !write_png(*surface, options.capture, png_bytes, &error)) {
            std::cerr << error << '\n';
            return 1;
        }
        std::cout << "vellum-app-host: renderer=Skia Graphite backend=Metal "
                  << "fallback=false width=" << pixel_width
                  << " height=" << pixel_height
                  << " interactions=" << rendered.interactions.size()
                  << " text_inputs=" << rendered.text_inputs.size()
                  << " components=" << components.size()
                  << " png_bytes=" << png_bytes << '\n';
    }
    return 0;
}

std::filesystem::path packaged_state_path() {
    id capability = NSBundle.mainBundle.infoDictionary[@"VellumPersistence"];
    if (![capability isKindOfClass:NSString.class] ||
        ![static_cast<NSString*>(capability) isEqualToString:@"state-v1"]) {
        return {};
    }
    NSString* identifier = NSBundle.mainBundle.bundleIdentifier;
    if (identifier.length == 0U) return {};
    NSURL* root = [NSFileManager.defaultManager
        URLForDirectory:NSApplicationSupportDirectory
               inDomain:NSUserDomainMask
      appropriateForURL:nil
                 create:NO
                  error:nil];
    if (root == nil) return {};
    NSURL* state = [[root URLByAppendingPathComponent:identifier isDirectory:YES]
        URLByAppendingPathComponent:@"vellum-state-v1.json" isDirectory:NO];
    return std::filesystem::path(state.path.UTF8String);
}

std::vector<ComponentModuleSpec> interactive_component_specs;

}  // namespace

@interface VellumApplicationView : NSView <NSTextInputClient> {
@private
    std::unique_ptr<JsApplication> _application;
    std::unique_ptr<ComponentRegistry> _components;
    RenderedApplication _rendered;
    std::unique_ptr<SkiaDawnSurface> _surface;
    std::filesystem::path _persistencePath;
    std::string _focusedInputId;
    NSRange _selectedTextRange;
    NSRange _markedTextRange;
    NSMutableAttributedString* _markedText;
    std::unique_ptr<MacAccessibilityBridge> _accessibility;
    NSTimer* _asyncTimer;
}
- (instancetype)initWithFrame:(NSRect)frame
                       bundle:(const std::string&)bundle
                   components:(const std::vector<ComponentModuleSpec>&)components
              persistencePath:(const std::filesystem::path&)persistencePath;
@end

@implementation VellumApplicationView
- (instancetype)initWithFrame:(NSRect)frame
                       bundle:(const std::string&)bundle
                   components:(const std::vector<ComponentModuleSpec>&)components
              persistencePath:(const std::filesystem::path&)persistencePath {
    self = [super initWithFrame:frame];
    if (self) {
        std::string error;
        _components = std::make_unique<ComponentRegistry>();
        _application = JsApplication::create(bundle, &error);
        if (!load_components(components, *_components, &error) || !_application ||
            !_application->render(_rendered, &error)) {
            NSLog(@"Vellum application initialization failed: %s", error.c_str());
            return nil;
        }
        _persistencePath = persistencePath;
        _selectedTextRange = NSMakeRange(NSNotFound, 0);
        _markedTextRange = NSMakeRange(NSNotFound, 0);
        _markedText = [[NSMutableAttributedString alloc] init];
        __weak VellumApplicationView* weakOwner = self;
        _accessibility = std::make_unique<MacAccessibilityBridge>(
            self,
            [weakOwner](std::string_view nodeId) {
                VellumApplicationView* owner = weakOwner;
                if (owner == nil) return false;
                return [owner performSemanticPress:std::string(nodeId)] == YES;
            },
            [weakOwner](std::string_view nodeId) {
                VellumApplicationView* owner = weakOwner;
                if (owner == nil) return false;
                return [owner focusSemanticInput:std::string(nodeId)] == YES;
            },
            [weakOwner](std::string_view nodeId, std::string_view value) {
                VellumApplicationView* owner = weakOwner;
                if (owner == nil) return false;
                return [owner setSemanticInput:std::string(nodeId)
                                         value:std::string(value)] == YES;
            });
        if (!restore_persisted_state(*_application, _rendered, _persistencePath, &error)) {
            NSLog(@"Vellum persisted state restore failed: %s", error.c_str());
            return nil;
        }
        if (!_components->expand(_rendered.scene, &error)) {
            NSLog(@"Vellum custom component expansion failed: %s", error.c_str());
            return nil;
        }
        self.wantsLayer = YES;
        self.layer = make_metal_layer(
            static_cast<std::uint32_t>(frame.size.width),
            static_cast<std::uint32_t>(frame.size.height), 1.0F);
        _accessibility->sync(_rendered.accessibility_nodes);
        __weak VellumApplicationView* weakSelf = self;
        _asyncTimer = [NSTimer scheduledTimerWithTimeInterval:0.016
                                                       repeats:YES
                                                         block:^(NSTimer* timer) {
            [weakSelf pumpAsyncWork:timer];
        }];
    }
    return self;
}

- (BOOL)isFlipped { return YES; }
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)isAccessibilityElement { return NO; }
- (NSArray*)accessibilityChildren {
    return _accessibility == nullptr ? @[] : _accessibility->children();
}

- (void)dealloc {
    [_asyncTimer invalidate];
}

- (void)pumpAsyncWork:(NSTimer*)timer {
    (void)timer;
    if (!_application) return;
    std::string error;
    vellum::authoring::PumpResult result;
    if (!_application->pump(16, 1024, _rendered, result, &error)) {
        [_asyncTimer invalidate];
        NSLog(@"Vellum asynchronous work failed: %s", error.c_str());
        return;
    }
    if (result.rendered && ![self finishMutation:&error]) {
        NSLog(@"Vellum asynchronous render failed: %s", error.c_str());
    }
}

- (BOOL)finishMutation:(std::string*)error {
    if (!_components->expand(_rendered.scene, error)) return NO;
    if (!persist_state(*_application, _persistencePath, error)) return NO;
    if (_surface && !_surface->render(_rendered.scene, error)) return NO;
    _accessibility->sync(_rendered.accessibility_nodes);
    return YES;
}

- (BOOL)performSemanticPress:(const std::string&)nodeId {
    std::string error;
    if (!press_node(*_application, _rendered, nodeId, &error) ||
        ![self finishMutation:&error]) {
        NSLog(@"Vellum accessibility press failed: %s", error.c_str());
        return NO;
    }
    return YES;
}

- (BOOL)focusSemanticInput:(const std::string&)nodeId {
    const TextInputControl* input = find_text_input(_rendered, nodeId);
    if (input == nullptr) return NO;
    _focusedInputId = nodeId;
    _selectedTextRange = NSMakeRange(
        input->selection_start, input->selection_end - input->selection_start);
    _markedTextRange = NSMakeRange(NSNotFound, 0);
    return [self.window makeFirstResponder:self];
}

- (BOOL)setSemanticInput:(const std::string&)nodeId
                   value:(const std::string&)value {
    std::string error;
    if (!input_node(
            *_application, _rendered, nodeId, value, @"accessibility", &error) ||
        ![self finishMutation:&error]) {
        NSLog(@"Vellum accessibility value change failed: %s", error.c_str());
        return NO;
    }
    return YES;
}

- (void)viewDidMoveToWindow {
    [super viewDidMoveToWindow];
    if (self.window == nil || _surface) return;
    CAMetalLayer* layer = static_cast<CAMetalLayer*>(self.layer);
    const float scale = static_cast<float>(self.window.backingScaleFactor);
    layer.contentsScale = scale;
    layer.drawableSize = CGSizeMake(self.bounds.size.width * scale,
                                    self.bounds.size.height * scale);
    std::string error;
    _surface = SkiaDawnSurface::create(
        {.width = static_cast<std::uint32_t>(self.bounds.size.width),
         .height = static_cast<std::uint32_t>(self.bounds.size.height),
         .scale = scale,
         .native_surface_handle = (__bridge void*)layer},
        &error);
    if (!_surface || !validate_gpu(*_surface, true, &error) ||
        !_surface->render(_rendered.scene, &error)) {
        NSLog(@"Vellum GPU initialization failed: %s", error.c_str());
    }
}

- (void)mouseDown:(NSEvent*)event {
    const NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    const auto input = std::find_if(
        _rendered.text_inputs.begin(), _rendered.text_inputs.end(),
        [point](const TextInputControl& item) {
            return point.x >= item.bounds.x && point.y >= item.bounds.y &&
                   point.x <= item.bounds.x + item.bounds.width &&
                   point.y <= item.bounds.y + item.bounds.height;
        });
    if (input != _rendered.text_inputs.end()) {
        if (_focusedInputId != input->node_id) {
            _selectedTextRange = NSMakeRange(
                input->selection_start, input->selection_end - input->selection_start);
            _markedTextRange = NSMakeRange(NSNotFound, 0);
            [_markedText setAttributedString:
                [[NSAttributedString alloc] initWithString:@""]];
        }
        _focusedInputId = input->node_id;
        [self.window makeFirstResponder:self];
        NSAccessibilityPostNotification(self, NSAccessibilityFocusedUIElementChangedNotification);
        return;
    }
    _focusedInputId.clear();
    const auto interaction = std::find_if(
        _rendered.interactions.begin(), _rendered.interactions.end(),
        [point](const Interaction& item) {
            return item.event == "press" && point.x >= item.bounds.x &&
                   point.y >= item.bounds.y &&
                   point.x <= item.bounds.x + item.bounds.width &&
                   point.y <= item.bounds.y + item.bounds.height;
        });
    if (interaction == _rendered.interactions.end()) return;
    std::string error;
    if (!_application->dispatch(
            interaction->action, R"({"pointerType":"mouse"})", _rendered, &error) ||
        ![self finishMutation:&error]) {
        NSLog(@"Vellum interaction failed: %s", error.c_str());
    }
}

- (void)keyDown:(NSEvent*)event {
    if (_focusedInputId.empty()) {
        [super keyDown:event];
        return;
    }
    [self interpretKeyEvents:@[event]];
}

- (const TextInputControl*)focusedTextInput {
    return _focusedInputId.empty() ? nullptr :
        find_text_input(_rendered, _focusedInputId);
}

- (BOOL)dispatchTextAction:(const std::string&)action payload:(NSDictionary*)payload {
    if (action.empty()) return YES;
    std::string error;
    if (!dispatch_payload(*_application, _rendered, action, payload, &error) ||
        ![self finishMutation:&error]) {
        NSLog(@"Vellum text interaction failed: %s", error.c_str());
        return NO;
    }
    return YES;
}

- (void)publishSelection {
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr || _selectedTextRange.location == NSNotFound) return;
    [self dispatchTextAction:input->selection_change_action payload:@{
        @"selection": @{
            @"start": @(_selectedTextRange.location),
            @"end": @(NSMaxRange(_selectedTextRange)),
        },
    }];
    NSAccessibilityPostNotification(self, NSAccessibilitySelectedTextChangedNotification);
}

- (void)insertText:(id)value replacementRange:(NSRange)replacementRange {
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr) return;
    NSString* inserted = [value isKindOfClass:NSAttributedString.class]
        ? [static_cast<NSAttributedString*>(value) string]
        : ([value isKindOfClass:NSString.class] ? value : [value description]);
    NSString* existing = [NSString stringWithUTF8String:input->value.c_str()] ?: @"";
    NSRange target = replacementRange.location != NSNotFound
        ? replacementRange
        : (_markedTextRange.location != NSNotFound ? _markedTextRange : _selectedTextRange);
    if (target.location == NSNotFound || NSMaxRange(target) > existing.length) {
        target = NSMakeRange(existing.length, 0);
    }
    NSString* next = [existing stringByReplacingCharactersInRange:target withString:inserted];
    _selectedTextRange = NSMakeRange(target.location + inserted.length, 0);
    const bool endingComposition = _markedTextRange.location != NSNotFound;
    _markedTextRange = NSMakeRange(NSNotFound, 0);
    [_markedText setAttributedString:[[NSAttributedString alloc] initWithString:@""]];
    const std::string changeAction = input->change_action;
    if (![self dispatchTextAction:changeAction payload:@{
            @"value": next,
            @"inputType": endingComposition ? @"insertCompositionText" : @"insertText",
            @"selection": @{
                @"start": @(_selectedTextRange.location),
                @"end": @(NSMaxRange(_selectedTextRange)),
            },
        }]) return;
    input = [self focusedTextInput];
    if (endingComposition && input != nullptr) {
        [self dispatchTextAction:input->composition_end_action payload:@{
            @"text": inserted,
            @"value": next,
            @"selection": @{
                @"start": @(_selectedTextRange.location),
                @"end": @(NSMaxRange(_selectedTextRange)),
            },
        }];
    }
    [self publishSelection];
}

- (void)setMarkedText:(id)value
        selectedRange:(NSRange)selectedRange
      replacementRange:(NSRange)replacementRange {
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr) return;
    NSAttributedString* attributed = [value isKindOfClass:NSAttributedString.class]
        ? value : [[NSAttributedString alloc] initWithString:
            ([value isKindOfClass:NSString.class] ? value : [value description])];
    NSString* text = attributed.string;
    NSString* existing = [NSString stringWithUTF8String:input->value.c_str()] ?: @"";
    const bool starting = _markedTextRange.location == NSNotFound;
    NSRange target = replacementRange.location != NSNotFound
        ? replacementRange
        : (starting ? _selectedTextRange : _markedTextRange);
    if (target.location == NSNotFound || NSMaxRange(target) > existing.length) {
        target = NSMakeRange(existing.length, 0);
    }
    NSString* next = [existing stringByReplacingCharactersInRange:target withString:text];
    _markedTextRange = NSMakeRange(target.location, text.length);
    const NSUInteger selectionOffset =
        std::min(selectedRange.location, text.length);
    const NSUInteger selectionLength =
        std::min(selectedRange.length, text.length - selectionOffset);
    _selectedTextRange = NSMakeRange(
        target.location + selectionOffset, selectionLength);
    [_markedText setAttributedString:attributed];
    const std::string startAction = input->composition_start_action;
    const std::string updateAction = input->composition_update_action;
    const std::string changeAction = input->change_action;
    if (starting && ![self dispatchTextAction:startAction payload:@{
            @"text": @"", @"value": existing,
            @"selection": @{
                @"start": @(target.location), @"end": @(NSMaxRange(target)),
            },
        }]) return;
    if (![self dispatchTextAction:changeAction payload:@{
            @"value": next, @"inputType": @"insertCompositionText",
            @"selection": @{
                @"start": @(_selectedTextRange.location),
                @"end": @(NSMaxRange(_selectedTextRange)),
            },
        }]) return;
    input = [self focusedTextInput];
    if (input != nullptr) {
        [self dispatchTextAction:updateAction payload:@{
            @"text": text, @"value": next,
            @"selection": @{
                @"start": @(_selectedTextRange.location),
                @"end": @(NSMaxRange(_selectedTextRange)),
            },
        }];
    }
}

- (void)unmarkText {
    if (_markedTextRange.location == NSNotFound) return;
    [self insertText:_markedText replacementRange:_markedTextRange];
}

- (NSRange)selectedRange { return _selectedTextRange; }
- (NSRange)markedRange { return _markedTextRange; }
- (BOOL)hasMarkedText { return _markedTextRange.location != NSNotFound; }
- (NSArray<NSAttributedStringKey>*)validAttributesForMarkedText { return @[]; }

- (NSAttributedString*)attributedSubstringForProposedRange:(NSRange)range
                                               actualRange:(NSRangePointer)actualRange {
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr) return nil;
    NSString* value = [NSString stringWithUTF8String:input->value.c_str()] ?: @"";
    const NSRange bounded = NSIntersectionRange(range, NSMakeRange(0, value.length));
    if (actualRange != nullptr) *actualRange = bounded;
    return [[NSAttributedString alloc] initWithString:
        [value substringWithRange:bounded]];
}

- (NSUInteger)characterIndexForPoint:(NSPoint)point {
    (void)point;
    return _selectedTextRange.location == NSNotFound ? 0 : _selectedTextRange.location;
}

- (NSRect)firstRectForCharacterRange:(NSRange)range actualRange:(NSRangePointer)actualRange {
    (void)range;
    if (actualRange != nullptr) *actualRange = NSMakeRange(
        _selectedTextRange.location == NSNotFound ? 0 : _selectedTextRange.location, 0);
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr || self.window == nil) return NSZeroRect;
    const NSRect local = NSMakeRect(
        input->bounds.x, input->bounds.y,
        input->bounds.width, input->bounds.height);
    return [self.window convertRectToScreen:[self convertRect:local toView:nil]];
}

- (void)doCommandBySelector:(SEL)selector {
    const TextInputControl* input = [self focusedTextInput];
    if (input == nullptr) return;
    NSString* key = nil;
    if (selector == @selector(insertNewline:)) key = @"Enter";
    else if (selector == @selector(deleteBackward:)) key = @"Backspace";
    else if (selector == @selector(deleteForward:)) key = @"Delete";
    else if (selector == @selector(moveLeft:)) key = @"ArrowLeft";
    else if (selector == @selector(moveRight:)) key = @"ArrowRight";
    else if (selector == @selector(moveUp:)) key = @"ArrowUp";
    else if (selector == @selector(moveDown:)) key = @"ArrowDown";
    else if (selector == @selector(moveToBeginningOfLine:)) key = @"Home";
    else if (selector == @selector(moveToEndOfLine:)) key = @"End";
    else if (selector == @selector(cancelOperation:)) key = @"Escape";
    if (key == nil) return;

    NSString* value = [NSString stringWithUTF8String:input->value.c_str()] ?: @"";
    if ([key isEqualToString:@"Backspace"]) {
        NSRange removal = _selectedTextRange;
        if (removal.length == 0 && removal.location > 0) {
            removal = [value rangeOfComposedCharacterSequenceAtIndex:removal.location - 1];
        }
        if (removal.length > 0) [self insertText:@"" replacementRange:removal];
    } else if ([key isEqualToString:@"Delete"]) {
        NSRange removal = _selectedTextRange;
        if (removal.length == 0 && removal.location < value.length) {
            removal = [value rangeOfComposedCharacterSequenceAtIndex:removal.location];
        }
        if (removal.length > 0) [self insertText:@"" replacementRange:removal];
    } else if ([key isEqualToString:@"ArrowLeft"] && _selectedTextRange.location > 0) {
        const NSRange previous =
            [value rangeOfComposedCharacterSequenceAtIndex:_selectedTextRange.location - 1];
        _selectedTextRange = NSMakeRange(previous.location, 0);
        [self publishSelection];
    } else if ([key isEqualToString:@"ArrowRight"]) {
        NSUInteger next = NSMaxRange(_selectedTextRange);
        if (next < value.length) {
            next = NSMaxRange([value rangeOfComposedCharacterSequenceAtIndex:next]);
        }
        _selectedTextRange = NSMakeRange(next, 0);
        [self publishSelection];
    } else if ([key isEqualToString:@"Home"]) {
        _selectedTextRange = NSMakeRange(0, 0);
        [self publishSelection];
    } else if ([key isEqualToString:@"End"]) {
        _selectedTextRange = NSMakeRange(value.length, 0);
        [self publishSelection];
    } else {
        std::string error;
        if (key_node(*_application, _rendered, _focusedInputId,
                     cpp_string(key), @"keyboard", &error) &&
            [self finishMutation:&error]) {
            const TextInputControl* current = [self focusedTextInput];
            if (current != nullptr) {
                NSString* currentValue =
                    [NSString stringWithUTF8String:current->value.c_str()] ?: @"";
                _selectedTextRange = NSMakeRange(
                    currentValue.length, 0);
            }
        } else if (!error.empty()) {
            NSLog(@"Vellum key command failed: %s", error.c_str());
        }
    }
}
@end

@interface VellumApplicationDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow* window;
@property(nonatomic, copy) NSData* bundleData;
@end

@implementation VellumApplicationDelegate
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
    (void)notification;
    const std::string bundle(
        static_cast<const char*>(self.bundleData.bytes), self.bundleData.length);
    std::string error;
    ComponentRegistry components;
    auto probe = JsApplication::create(bundle, &error);
    RenderedApplication rendered;
    if (!load_components(interactive_component_specs, components, &error) ||
        !probe || !probe->render(rendered, &error) ||
        !components.expand(rendered.scene, &error)) {
        NSLog(@"Vellum application failed: %s", error.c_str());
        [NSApp terminate:nil];
        return;
    }
    const NSRect frame = NSMakeRect(0, 0, rendered.scene.width, rendered.scene.height);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable)
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = NSBundle.mainBundle.infoDictionary[@"CFBundleDisplayName"]
        ?: @"Vellum Application";
    const std::filesystem::path persistence_path = packaged_state_path();
    self.window.contentView = [[VellumApplicationView alloc]
        initWithFrame:frame
               bundle:bundle
           components:interactive_component_specs
      persistencePath:persistence_path];
    if (self.window.contentView == nil) {
        [NSApp terminate:nil];
        return;
    }
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)sender {
    (void)sender;
    return YES;
}
@end

int main(int argc, const char* argv[]) {
    const auto parsed = parse_options(argc, argv);
    if (!parsed) {
        vellum::app_host::print_usage();
        return 2;
    }
    Options options = *parsed;
    if (options.bundle.empty()) options.bundle = bundled_application_script();
    if (options.components.empty()) options.components = bundled_component_modules();
    std::string bundle;
    if (options.bundle.empty() || !read_file(options.bundle, bundle)) {
        std::cerr << "could not read application bundle: " << options.bundle << '\n';
        return 2;
    }
    if (options.no_window || !options.capture.empty() || !options.steps.empty() ||
        !options.state_file.empty()) {
        return run_headless(options, bundle);
    }
    interactive_component_specs = options.components;
    @autoreleasepool {
        NSApplication* application = NSApplication.sharedApplication;
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        VellumApplicationDelegate* delegate = [[VellumApplicationDelegate alloc] init];
        delegate.bundleData = [NSData dataWithBytes:bundle.data() length:bundle.size()];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
