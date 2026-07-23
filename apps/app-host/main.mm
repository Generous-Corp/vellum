#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>

#include <vellum/authoring/js_application.hpp>
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

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

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    return utf8 == nullptr ? std::string{} : std::string{utf8};
}

struct AutomationStep final {
    enum class Kind { press, input, key };
    Kind kind;
    std::string node_id;
    std::string value;
};

struct Options final {
    std::filesystem::path bundle;
    std::filesystem::path capture;
    std::filesystem::path state_file;
    std::vector<AutomationStep> steps;
    std::optional<std::uint32_t> expected_width;
    std::optional<std::uint32_t> expected_height;
    bool no_window = false;
};

void usage() {
    std::cerr << "usage: vellum-app-host [--bundle FILE] [--self-test|--no-window] "
                 "[--press NODE_ID] [--input NODE_ID TEXT] [--key NODE_ID KEY] "
                 "[--state-file FILE] [--expect-width N --expect-height N] [--capture PNG]\n";
}

constexpr std::size_t kMaximumAutomationSteps = 1000U;
constexpr std::size_t kMaximumNodeIdBytes = 1024U;
constexpr std::size_t kMaximumInputBytes = 64U * 1024U;
constexpr std::size_t kMaximumStateBytes = 16U * 1024U * 1024U;

bool valid_node_id(std::string_view value) {
    return !value.empty() && value.size() <= kMaximumNodeIdBytes &&
           value.find('\0') == std::string_view::npos;
}

bool valid_key(std::string_view value) {
    constexpr std::string_view supported[] = {
        "Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End", "Delete",
    };
    return std::find(std::begin(supported), std::end(supported), value) !=
           std::end(supported);
}

std::optional<std::uint32_t> positive_dimension(std::string_view text) {
    try {
        const auto value = std::stoul(std::string(text));
        if (value == 0U || value > 16384U) return std::nullopt;
        return static_cast<std::uint32_t>(value);
    } catch (...) {
        return std::nullopt;
    }
}

std::optional<Options> parse_options(int argc, const char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument = argv[index];
        if (argument == "--self-test" || argument == "--no-window") {
            options.no_window = true;
        } else if (argument == "--bundle" || argument == "--capture" ||
                   argument == "--state-file" || argument == "--press" ||
                   argument == "--expect-width" ||
                   argument == "--expect-height") {
            if (++index >= argc) return std::nullopt;
            if (argument == "--bundle") options.bundle = argv[index];
            if (argument == "--capture") options.capture = argv[index];
            if (argument == "--state-file") {
                if (argv[index][0] == '\0') return std::nullopt;
                options.state_file = argv[index];
            }
            if (argument == "--press") {
                const std::string node_id = argv[index];
                if (!valid_node_id(node_id)) return std::nullopt;
                options.steps.push_back({AutomationStep::Kind::press, node_id, {}});
            }
            if (argument == "--expect-width") {
                options.expected_width = positive_dimension(argv[index]);
                if (!options.expected_width) return std::nullopt;
            }
            if (argument == "--expect-height") {
                options.expected_height = positive_dimension(argv[index]);
                if (!options.expected_height) return std::nullopt;
            }
        } else if (argument == "--input" || argument == "--key") {
            if (index + 2 >= argc) return std::nullopt;
            const std::string node_id = argv[++index];
            const std::string value = argv[++index];
            if (!valid_node_id(node_id) ||
                (argument == "--input" && value.size() > kMaximumInputBytes) ||
                (argument == "--key" && !valid_key(value))) {
                return std::nullopt;
            }
            options.steps.push_back({
                argument == "--input" ? AutomationStep::Kind::input : AutomationStep::Kind::key,
                node_id,
                value,
            });
        } else {
            return std::nullopt;
        }
        if (options.steps.size() > kMaximumAutomationSteps) return std::nullopt;
    }
    return options;
}

std::filesystem::path bundled_application_script() {
    NSString* resource = [NSBundle.mainBundle pathForResource:@"app" ofType:@"js"];
    return resource == nil ? std::filesystem::path{} :
                             std::filesystem::path(resource.UTF8String);
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

bool press_node(JsApplication& application, RenderedApplication& rendered,
                std::string_view node_id, std::string* error) {
    const auto interaction = std::find_if(
        rendered.interactions.begin(), rendered.interactions.end(),
        [node_id](const Interaction& item) {
            return item.node_id == node_id && item.event == "press";
        });
    if (interaction == rendered.interactions.end()) {
        if (error) *error = "scenario press target is missing or not pressable: " +
                            std::string(node_id);
        return false;
    }
    return application.dispatch(
        interaction->action, R"({"pointerType":"automation"})", rendered, error);
}

const TextInputControl* find_text_input(
    const RenderedApplication& rendered, std::string_view node_id) {
    const auto input = std::find_if(
        rendered.text_inputs.begin(), rendered.text_inputs.end(),
        [node_id](const TextInputControl& item) { return item.node_id == node_id; });
    return input == rendered.text_inputs.end() ? nullptr : &*input;
}

bool serialize_payload(NSDictionary* value, std::string& output, std::string* error) {
    NSError* json_error = nil;
    NSData* data = [NSJSONSerialization dataWithJSONObject:value options:0 error:&json_error];
    if (data == nil || data.length > kMaximumInputBytes + 4096U) {
        if (error) *error = "event payload could not be serialized within its bound";
        return false;
    }
    NSString* text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (text == nil) {
        if (error) *error = "event payload did not encode as UTF-8";
        return false;
    }
    output.assign(text.UTF8String, [text lengthOfBytesUsingEncoding:NSUTF8StringEncoding]);
    return true;
}

bool dispatch_payload(JsApplication& application, RenderedApplication& rendered,
                      std::string_view action, NSDictionary* payload,
                      std::string* error) {
    std::string encoded;
    return serialize_payload(payload, encoded, error) &&
           application.dispatch(action, encoded, rendered, error);
}

bool input_node(JsApplication& application, RenderedApplication& rendered,
                std::string_view node_id, std::string_view value,
                NSString* source, std::string* error) {
    const TextInputControl* input = find_text_input(rendered, node_id);
    if (input == nullptr || input->change_action.empty()) {
        if (error) *error = "scenario input target is missing or not editable: " +
                            std::string(node_id);
        return false;
    }
    NSString* text = [[NSString alloc]
        initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];
    if (text == nil || value.size() > kMaximumInputBytes) {
        if (error) *error = "text input value is invalid UTF-8 or exceeds 64 KiB";
        return false;
    }
    const std::string action = input->change_action;
    return dispatch_payload(application, rendered, action,
        @{ @"value": text, @"inputType": source }, error);
}

std::string without_last_grapheme(std::string_view value) {
    NSString* text = [[NSString alloc]
        initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];
    if (text == nil || text.length == 0U) return {};
    const NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:text.length - 1U];
    return cpp_string([text substringToIndex:range.location]);
}

bool key_node(JsApplication& application, RenderedApplication& rendered,
              std::string_view node_id, std::string_view key,
              NSString* source, std::string* error) {
    const TextInputControl* input = find_text_input(rendered, node_id);
    if (input == nullptr) {
        if (error) *error = "scenario key target is missing or not a text input: " +
                            std::string(node_id);
        return false;
    }
    if (!valid_key(key)) {
        if (error) *error = "unsupported semantic key: " + std::string(key);
        return false;
    }
    NSString* key_text = [[NSString alloc]
        initWithBytes:key.data() length:key.size() encoding:NSUTF8StringEncoding];
    bool dispatched = false;
    if (!input->key_down_action.empty()) {
        const std::string action = input->key_down_action;
        if (!dispatch_payload(application, rendered, action,
                @{ @"key": key_text, @"repeat": @NO, @"source": source }, error)) {
            return false;
        }
        dispatched = true;
        input = find_text_input(rendered, node_id);
        if (input == nullptr) {
            if (error) *error = "key handler removed its text input target";
            return false;
        }
    }
    if (key == "Enter" && !input->submit_action.empty()) {
        const std::string action = input->submit_action;
        NSString* value = [[NSString alloc] initWithUTF8String:input->value.c_str()];
        if (!dispatch_payload(application, rendered, action,
                @{ @"value": value ?: @"", @"source": source }, error)) {
            return false;
        }
        dispatched = true;
    }
    if (key == "Backspace" && !input->change_action.empty()) {
        const std::string next_value = without_last_grapheme(input->value);
        if (!input_node(application, rendered, node_id, next_value, source, error)) return false;
        dispatched = true;
    }
    if (!dispatched) {
        if (error) *error = "text input has no handler for semantic key: " + std::string(key);
        return false;
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
        for (const auto& step : options.steps) {
            const bool succeeded = step.kind == AutomationStep::Kind::press
                ? press_node(*application, rendered, step.node_id, &error)
                : step.kind == AutomationStep::Kind::input
                    ? input_node(*application, rendered, step.node_id, step.value,
                                 @"scenario", &error)
                    : key_node(*application, rendered, step.node_id, step.value,
                               @"scenario", &error);
            if (!succeeded ||
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

}  // namespace

@interface VellumApplicationView : NSView {
@private
    std::unique_ptr<JsApplication> _application;
    RenderedApplication _rendered;
    std::unique_ptr<SkiaDawnSurface> _surface;
    std::filesystem::path _persistencePath;
    std::string _focusedInputId;
}
- (instancetype)initWithFrame:(NSRect)frame
                       bundle:(const std::string&)bundle
              persistencePath:(const std::filesystem::path&)persistencePath;
@end

@implementation VellumApplicationView
- (instancetype)initWithFrame:(NSRect)frame
                       bundle:(const std::string&)bundle
              persistencePath:(const std::filesystem::path&)persistencePath {
    self = [super initWithFrame:frame];
    if (self) {
        std::string error;
        _application = JsApplication::create(bundle, &error);
        if (!_application || !_application->render(_rendered, &error)) {
            NSLog(@"Vellum application initialization failed: %s", error.c_str());
            return nil;
        }
        _persistencePath = persistencePath;
        if (!restore_persisted_state(*_application, _rendered, _persistencePath, &error)) {
            NSLog(@"Vellum persisted state restore failed: %s", error.c_str());
            return nil;
        }
        self.wantsLayer = YES;
        self.layer = make_metal_layer(
            static_cast<std::uint32_t>(frame.size.width),
            static_cast<std::uint32_t>(frame.size.height), 1.0F);
    }
    return self;
}

- (BOOL)isFlipped { return YES; }
- (BOOL)acceptsFirstResponder { return YES; }

- (BOOL)finishMutation:(std::string*)error {
    if (!persist_state(*_application, _persistencePath, error)) return NO;
    if (_surface && !_surface->render(_rendered.scene, error)) return NO;
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
        _focusedInputId = input->node_id;
        [self.window makeFirstResponder:self];
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
    std::string error;
    std::string semantic_key;
    switch (event.keyCode) {
        case 36: case 76: semantic_key = "Enter"; break;
        case 53: semantic_key = "Escape"; break;
        case 51: semantic_key = "Backspace"; break;
        case 48: semantic_key = "Tab"; break;
        case 123: semantic_key = "ArrowLeft"; break;
        case 124: semantic_key = "ArrowRight"; break;
        case 125: semantic_key = "ArrowDown"; break;
        case 126: semantic_key = "ArrowUp"; break;
        case 115: semantic_key = "Home"; break;
        case 119: semantic_key = "End"; break;
        case 117: semantic_key = "Delete"; break;
        default: break;
    }
    bool changed = false;
    if (!semantic_key.empty()) {
        changed = key_node(
            *_application, _rendered, _focusedInputId, semantic_key, @"keyboard", &error);
    } else if ((event.modifierFlags &
                (NSEventModifierFlagCommand | NSEventModifierFlagControl)) == 0U) {
        NSString* characters = event.characters;
        const TextInputControl* input = find_text_input(_rendered, _focusedInputId);
        if (input != nullptr && characters.length > 0U) {
            const std::string appended = input->value + cpp_string(characters);
            changed = input_node(
                *_application, _rendered, _focusedInputId, appended, @"keyboard", &error);
        }
    }
    if (changed && ![self finishMutation:&error]) changed = false;
    if (!changed && !error.empty()) {
        NSLog(@"Vellum key interaction failed: %s", error.c_str());
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
    auto probe = JsApplication::create(bundle, &error);
    RenderedApplication rendered;
    if (!probe || !probe->render(rendered, &error)) {
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
        initWithFrame:frame bundle:bundle persistencePath:persistence_path];
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
        usage();
        return 2;
    }
    Options options = *parsed;
    if (options.bundle.empty()) options.bundle = bundled_application_script();
    std::string bundle;
    if (options.bundle.empty() || !read_file(options.bundle, bundle)) {
        std::cerr << "could not read application bundle: " << options.bundle << '\n';
        return 2;
    }
    if (options.no_window || !options.capture.empty() || !options.steps.empty() ||
        !options.state_file.empty()) {
        return run_headless(options, bundle);
    }
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
