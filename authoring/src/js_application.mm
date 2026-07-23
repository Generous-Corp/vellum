#include <vellum/authoring/js_application.hpp>

#import <Foundation/Foundation.h>
#import <JavaScriptCore/JavaScriptCore.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <set>
#include <utility>

namespace vellum::authoring {
namespace {

constexpr std::size_t kMaximumBundleBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumJsonBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumNodes = 100000U;
constexpr std::size_t kMaximumDepth = 256U;

void set_error(std::string* destination, std::string value) {
    if (destination != nullptr) *destination = std::move(value);
}

NSString* ns_string(std::string_view value) {
    return [[NSString alloc]
        initWithBytes:value.data()
               length:value.size()
             encoding:NSUTF8StringEncoding];
}

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    return utf8 == nullptr ? std::string{} : std::string{utf8};
}

bool finite_number(id value, float& output) {
    if (![value isKindOfClass:NSNumber.class]) return false;
    const double number = [static_cast<NSNumber*>(value) doubleValue];
    if (!std::isfinite(number) ||
        number < -static_cast<double>(std::numeric_limits<float>::max()) ||
        number > static_cast<double>(std::numeric_limits<float>::max())) {
        return false;
    }
    output = static_cast<float>(number);
    return true;
}

float number_or(NSDictionary* style, NSString* key, float fallback) {
    float value = 0.0F;
    return finite_number(style[key], value) ? value : fallback;
}

std::optional<vellum::graphics::Color> parse_color(id value) {
    if (![value isKindOfClass:NSString.class]) return std::nullopt;
    NSString* text = static_cast<NSString*>(value);
    if (![text hasPrefix:@"#"] || (text.length != 7 && text.length != 9)) {
        return std::nullopt;
    }
    unsigned long long encoded = 0;
    NSScanner* scanner = [NSScanner scannerWithString:[text substringFromIndex:1]];
    if (![scanner scanHexLongLong:&encoded] || !scanner.isAtEnd) return std::nullopt;
    if (text.length == 7) {
        return vellum::graphics::Color::hex(static_cast<std::uint32_t>(encoded));
    }
    return vellum::graphics::Color::hex(
        static_cast<std::uint32_t>(encoded >> 8U),
        static_cast<float>(encoded & 0xFFU) / 255.0F);
}

NSDictionary* dictionary_or_empty(id value) {
    return [value isKindOfClass:NSDictionary.class]
        ? static_cast<NSDictionary*>(value)
        : @{};
}

NSArray* array_or_empty(id value) {
    return [value isKindOfClass:NSArray.class]
        ? static_cast<NSArray*>(value)
        : @[];
}

struct MaterializeContext final {
    std::set<std::string> identities;
    std::size_t nodes = 0;
    std::vector<Interaction>* interactions = nullptr;
};

float default_height(NSString* type, NSDictionary* style) {
    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        return number_or(style, @"fontSize", 14.0F) * 1.4F;
    }
    if ([type isEqualToString:@"button"]) return 44.0F;
    return 0.0F;
}

std::string direct_text(NSDictionary* source) {
    if ([source[@"text"] isKindOfClass:NSString.class]) {
        return cpp_string(source[@"text"]);
    }
    std::string result;
    for (id child in array_or_empty(source[@"children"])) {
        if (![child isKindOfClass:NSDictionary.class]) continue;
        NSDictionary* child_dictionary = static_cast<NSDictionary*>(child);
        if ([child_dictionary[@"type"] isEqual:@"text-run"] &&
            [child_dictionary[@"text"] isKindOfClass:NSString.class]) {
            result += cpp_string(child_dictionary[@"text"]);
        }
    }
    return result;
}

bool materialize_node(
    NSDictionary* source,
    const vellum::graphics::Rect& proposed,
    float absolute_x,
    float absolute_y,
    std::size_t depth,
    MaterializeContext& context,
    vellum::graphics::SceneNode& output,
    std::string* error) {
    if (depth > kMaximumDepth || ++context.nodes > kMaximumNodes) {
        set_error(error, "authoring tree exceeds the node or depth limit");
        return false;
    }
    if (![source[@"type"] isKindOfClass:NSString.class] ||
        ![source[@"id"] isKindOfClass:NSString.class]) {
        set_error(error, "each authoring node requires string type and id fields");
        return false;
    }
    NSString* type = source[@"type"];
    const std::string identity = cpp_string(source[@"id"]);
    if (identity.empty() || !context.identities.insert(identity).second) {
        set_error(error, "authoring tree contains an empty or duplicate node id: " + identity);
        return false;
    }
    NSDictionary* style = dictionary_or_empty(source[@"style"]);
    output.id = identity;
    output.bounds = proposed;
    output.corner_radius = std::max(0.0F, number_or(style, @"borderRadius", 0.0F));
    output.fill = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F));

    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        output.kind = vellum::graphics::SceneNode::Kind::text;
        output.text = direct_text(source);
        output.font_size = std::max(1.0F, number_or(style, @"fontSize", 14.0F));
        output.fill = parse_color(style[@"color"])
            .value_or(vellum::graphics::Color::hex(0x111827));
    } else if ([type isEqualToString:@"button"] ||
               parse_color(style[@"backgroundColor"]).has_value()) {
        output.kind = vellum::graphics::SceneNode::Kind::rectangle;
        if ([type isEqualToString:@"button"] &&
            !parse_color(style[@"backgroundColor"]).has_value()) {
            output.fill = vellum::graphics::Color::hex(0x14B8A6);
            output.corner_radius = std::max(output.corner_radius, 10.0F);
        }
    } else {
        output.kind = vellum::graphics::SceneNode::Kind::group;
    }

    NSDictionary* events = dictionary_or_empty(source[@"events"]);
    for (NSString* event in events) {
        if (![event isKindOfClass:NSString.class] ||
            ![events[event] isKindOfClass:NSString.class]) {
            set_error(error, "authoring node events must map strings to action strings");
            return false;
        }
        context.interactions->push_back({
            .node_id = identity,
            .event = cpp_string(event),
            .action = cpp_string(events[event]),
            .bounds = {
                absolute_x + proposed.x,
                absolute_y + proposed.y,
                proposed.width,
                proposed.height,
            },
        });
    }

    NSArray* children = array_or_empty(source[@"children"]);
    const bool is_stack = [type isEqualToString:@"stack"];
    const bool horizontal = [style[@"direction"] isEqual:@"horizontal"];
    const float padding = std::max(0.0F, number_or(style, @"padding", 0.0F));
    const float gap = std::max(0.0F, number_or(style, @"gap", 0.0F));
    if ([type isEqualToString:@"button"]) {
        const std::string label_text = direct_text(source);
        if (!label_text.empty()) {
            vellum::graphics::SceneNode label;
            const float font_size = 14.0F;
            label.id = identity + "/label";
            if (!context.identities.insert(label.id).second) {
                set_error(error, "generated button label id collides: " + label.id);
                return false;
            }
            if (++context.nodes > kMaximumNodes) {
                set_error(error, "authoring tree exceeds the node limit");
                return false;
            }
            label.kind = vellum::graphics::SceneNode::Kind::text;
            label.bounds = {16.0F, (proposed.height - font_size * 1.4F) * 0.5F,
                            std::max(0.0F, proposed.width - 32.0F), font_size * 1.4F};
            label.fill = parse_color(style[@"color"])
                .value_or(vellum::graphics::Color::hex(0x041412));
            label.font_size = font_size;
            label.text = label_text;
            output.children.push_back(std::move(label));
        }
    }
    float cursor = padding;
    for (id child_value in children) {
        if (![child_value isKindOfClass:NSDictionary.class]) {
            set_error(error, "authoring node children must be retained-tree objects");
            return false;
        }
        NSDictionary* child = static_cast<NSDictionary*>(child_value);
        if ([child[@"type"] isEqual:@"text-run"] &&
            ([type isEqualToString:@"text"] || [type isEqualToString:@"button"])) {
            continue;
        }
        NSDictionary* child_style = dictionary_or_empty(child[@"style"]);
        NSString* child_type = [child[@"type"] isKindOfClass:NSString.class]
            ? child[@"type"] : @"view";
        float width = number_or(
            child_style, @"width",
            horizontal ? 0.0F : std::max(0.0F, proposed.width - padding * 2.0F));
        float height = number_or(child_style, @"height", default_height(child_type, child_style));
        float x = number_or(child_style, @"x", is_stack && horizontal ? cursor : padding);
        float y = number_or(child_style, @"y", is_stack && !horizontal ? cursor : padding);
        if (width <= 0.0F && horizontal) width = std::max(0.0F, proposed.width - cursor - padding);
        if (height <= 0.0F && !horizontal) height = std::max(0.0F, proposed.height - cursor - padding);
        vellum::graphics::SceneNode child_output;
        if (!materialize_node(
                child, {x, y, width, height},
                absolute_x + proposed.x, absolute_y + proposed.y,
                depth + 1U, context, child_output, error)) {
            return false;
        }
        output.children.push_back(std::move(child_output));
        if (is_stack) cursor += (horizontal ? width : height) + gap;
    }
    return true;
}

bool parse_rendered_json(
    NSString* text, RenderedApplication& output, std::string* error) {
    if (text == nil) {
        set_error(error, "JavaScript authoring bridge returned no JSON");
        return false;
    }
    NSData* data = [text dataUsingEncoding:NSUTF8StringEncoding];
    if (data == nil || data.length > kMaximumJsonBytes) {
        set_error(error, "JavaScript authoring bridge JSON is invalid or too large");
        return false;
    }
    NSError* json_error = nil;
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:&json_error];
    if (![value isKindOfClass:NSDictionary.class]) {
        set_error(error, "JavaScript authoring bridge returned an invalid envelope: " +
                         cpp_string(json_error.localizedDescription));
        return false;
    }
    NSDictionary* envelope = static_cast<NSDictionary*>(value);
    if (![envelope[@"protocol"] isEqual:@"vellum.authoring-host.v1"] ||
        ![envelope[@"tree"] isKindOfClass:NSDictionary.class]) {
        set_error(error, "JavaScript authoring bridge protocol mismatch");
        return false;
    }
    NSDictionary* tree = envelope[@"tree"];
    NSDictionary* style = dictionary_or_empty(tree[@"style"]);
    const float width = number_or(style, @"width", 0.0F);
    const float height = number_or(style, @"height", 0.0F);
    if (width <= 0.0F || height <= 0.0F) {
        set_error(error, "root authoring node requires positive numeric width and height");
        return false;
    }
    RenderedApplication candidate;
    candidate.scene.width = width;
    candidate.scene.height = height;
    candidate.scene.background = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::hex(0xF8FAFC));
    MaterializeContext context{.interactions = &candidate.interactions};
    if (!materialize_node(
            tree, {0.0F, 0.0F, width, height}, 0.0F, 0.0F, 0U,
            context, candidate.scene.root, error)) {
        return false;
    }
    output = std::move(candidate);
    return true;
}

}  // namespace

class JsApplication::Impl final {
public:
    bool initialize(std::string_view bundle, std::string* error) {
        if (bundle.empty() || bundle.size() > kMaximumBundleBytes) {
            set_error(error, "JavaScript bundle is empty or exceeds 16 MiB");
            return false;
        }
        NSString* source = ns_string(bundle);
        if (source == nil) {
            set_error(error, "JavaScript bundle is not valid UTF-8");
            return false;
        }
        context_ = [[JSContext alloc] init];
        [context_ evaluateScript:source withSourceURL:[NSURL URLWithString:@"vellum-app.js"]];
        if (!consume_exception(error)) return false;
        JSValue* bridge = context_[@"__vellum"];
        if (bridge == nil || bridge.isUndefined || bridge.isNull ||
            ![[bridge valueForProperty:@"protocol"].toString
                isEqualToString:@"vellum.authoring-host.v1"]) {
            set_error(error, "bundle did not mount the vellum.authoring-host.v1 bridge");
            return false;
        }
        return true;
    }

    bool render(RenderedApplication& output, std::string* error) {
        return call_tree(@"renderJSON", @[], output, error);
    }

    bool dispatch(std::string_view action, std::string_view payload_json,
                  RenderedApplication& output, std::string* error) {
        if (payload_json.size() > kMaximumJsonBytes) {
            set_error(error, "event payload exceeds 16 MiB");
            return false;
        }
        NSString* action_value = ns_string(action);
        if (action_value == nil || action_value.length == 0) {
            set_error(error, "event action must be non-empty valid UTF-8");
            return false;
        }
        NSString* payload = ns_string(payload_json.empty() ? "null" : payload_json);
        if (payload == nil) {
            set_error(error, "event payload is not valid UTF-8");
            return false;
        }
        NSError* payload_error = nil;
        id payload_value = [NSJSONSerialization
            JSONObjectWithData:[payload dataUsingEncoding:NSUTF8StringEncoding]
                       options:NSJSONReadingFragmentsAllowed
                         error:&payload_error];
        if (payload_error != nil) {
            set_error(error, "event payload is not valid JSON");
            return false;
        }
        NSDictionary* request = @{
            @"protocol": @"vellum.authoring-host.v1",
            @"action": action_value,
            @"payload": payload_value ?: NSNull.null,
        };
        NSError* request_error = nil;
        NSData* request_data = [NSJSONSerialization
            dataWithJSONObject:request options:0 error:&request_error];
        if (request_data == nil || request_error != nil) {
            set_error(error, "event request could not be serialized");
            return false;
        }
        NSString* request_json = [[NSString alloc]
            initWithData:request_data encoding:NSUTF8StringEncoding];
        return call_tree(@"dispatchJSON", @[request_json], output, error);
    }

    bool snapshot(std::string& output, std::string* error) {
        NSString* result = call_string(@"snapshotStateJSON", @[], error);
        if (result == nil) return false;
        if ([result lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > kMaximumJsonBytes) {
            set_error(error, "state snapshot exceeds 16 MiB");
            return false;
        }
        output = cpp_string(result);
        return true;
    }

    bool restore(std::string_view snapshot, RenderedApplication& output,
                 std::string* error) {
        if (snapshot.size() > kMaximumJsonBytes) {
            set_error(error, "state snapshot exceeds 16 MiB");
            return false;
        }
        NSString* value = ns_string(snapshot);
        if (value == nil) {
            set_error(error, "state snapshot is not valid UTF-8");
            return false;
        }
        return call_tree(@"restoreStateJSON", @[value], output, error);
    }

private:
    bool consume_exception(std::string* error) {
        JSValue* exception = context_.exception;
        if (exception == nil || exception.isUndefined || exception.isNull) return true;
        set_error(error, "JavaScript exception: " + cpp_string(exception.toString));
        context_.exception = nil;
        return false;
    }

    NSString* call_string(NSString* method, NSArray* arguments, std::string* error) {
        context_.exception = nil;
        JSValue* bridge = context_[@"__vellum"];
        JSValue* result = [bridge invokeMethod:method withArguments:arguments];
        if (!consume_exception(error) || result == nil || !result.isString) {
            if (result == nil || !result.isString) {
                set_error(error, "JavaScript authoring bridge method did not return a string: " +
                                 cpp_string(method));
            }
            return nil;
        }
        return result.toString;
    }

    bool call_tree(NSString* method, NSArray* arguments,
                   RenderedApplication& output, std::string* error) {
        NSString* result = call_string(method, arguments, error);
        return result != nil && parse_rendered_json(result, output, error);
    }

    __strong JSContext* context_ = nil;
};

JsApplication::JsApplication(std::unique_ptr<Impl> impl) noexcept
    : impl_(std::move(impl)) {}

JsApplication::~JsApplication() = default;
JsApplication::JsApplication(JsApplication&&) noexcept = default;
JsApplication& JsApplication::operator=(JsApplication&&) noexcept = default;

std::unique_ptr<JsApplication> JsApplication::create(
    std::string_view bundle, std::string* error) {
    @autoreleasepool {
        auto implementation = std::make_unique<Impl>();
        if (!implementation->initialize(bundle, error)) return nullptr;
        return std::unique_ptr<JsApplication>(
            new JsApplication(std::move(implementation)));
    }
}

bool JsApplication::render(RenderedApplication& output, std::string* error) {
    @autoreleasepool { return impl_->render(output, error); }
}

bool JsApplication::dispatch(
    std::string_view action, std::string_view payload_json,
    RenderedApplication& output, std::string* error) {
    @autoreleasepool { return impl_->dispatch(action, payload_json, output, error); }
}

bool JsApplication::snapshot_state(std::string& output_json, std::string* error) {
    @autoreleasepool { return impl_->snapshot(output_json, error); }
}

bool JsApplication::restore_state(
    std::string_view snapshot_json, RenderedApplication& output,
    std::string* error) {
    @autoreleasepool { return impl_->restore(snapshot_json, output, error); }
}

}  // namespace vellum::authoring
