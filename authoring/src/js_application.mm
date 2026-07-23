#include <vellum/authoring/js_application.hpp>

#import <Foundation/Foundation.h>
#import <JavaScriptCore/JavaScriptCore.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <set>
#include <tuple>
#include <utility>

namespace vellum::authoring {
namespace {

constexpr std::size_t kMaximumBundleBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumJsonBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumNodes = 100000U;
constexpr std::size_t kMaximumDepth = 256U;
constexpr NSUInteger kMaximumTextInputBytes = 64U * 1024U;
constexpr NSUInteger kMaximumPlaceholderBytes = 4U * 1024U;
constexpr std::uint64_t kMaximumTimerDelayMilliseconds = 24ULL * 60ULL * 60ULL * 1000ULL;

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
    std::vector<TextInputControl>* text_inputs = nullptr;
    std::vector<AccessibilityNode>* accessibility_nodes = nullptr;
};

std::string inferred_accessibility_role(NSString* type, NSDictionary* source) {
    if ([source[@"accessibilityRole"] isKindOfClass:NSString.class]) {
        return cpp_string(source[@"accessibilityRole"]);
    }
    if ([type isEqualToString:@"text-input"]) return "text-field";
    if ([type isEqualToString:@"button"]) return "button";
    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) return "text";
    if ([type isEqualToString:@"image"]) return "image";
    return "group";
}

float default_height(NSString* type, NSDictionary* style) {
    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        return number_or(style, @"fontSize", 14.0F) * 1.4F;
    }
    if ([type isEqualToString:@"button"] || [type isEqualToString:@"text-input"]) {
        return 44.0F;
    }
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

std::optional<std::string> json_object(id value) {
    if (![value isKindOfClass:NSDictionary.class] ||
        ![NSJSONSerialization isValidJSONObject:value]) {
        return std::nullopt;
    }
    NSError* error = nil;
    NSData* data = [NSJSONSerialization dataWithJSONObject:value
                                                   options:NSJSONWritingSortedKeys
                                                     error:&error];
    if (data == nil || error != nil || data.length > kMaximumJsonBytes) {
        return std::nullopt;
    }
    NSString* text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    return text == nil ? std::nullopt : std::optional<std::string>{cpp_string(text)};
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
    NSDictionary* events = dictionary_or_empty(source[@"events"]);
    const bool is_text_input = [type isEqualToString:@"text-input"];
    NSString* input_value = nil;
    NSString* input_placeholder = nil;
    NSUInteger selection_start = 0U;
    NSUInteger selection_end = 0U;
    if (is_text_input) {
        NSNumber* primitive_version = [source[@"primitiveVersion"]
            isKindOfClass:NSNumber.class] ? source[@"primitiveVersion"] : nil;
        input_value = [source[@"value"] isKindOfClass:NSString.class]
            ? source[@"value"] : nil;
        input_placeholder = [source[@"placeholder"] isKindOfClass:NSString.class]
            ? source[@"placeholder"] : nil;
        if (primitive_version == nil || primitive_version.integerValue != 1 ||
            input_value == nil ||
            [input_value lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                kMaximumTextInputBytes ||
            (source[@"placeholder"] != nil && input_placeholder == nil) ||
            [input_placeholder lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                kMaximumPlaceholderBytes ||
            ![events[@"change"] isKindOfClass:NSString.class]) {
            set_error(error,
                "text-input requires primitiveVersion 1, bounded string value/placeholder, "
                "and a change action");
            return false;
        }
        if (array_or_empty(source[@"children"]).count != 0U) {
            set_error(error, "text-input v1 does not accept retained-tree children");
            return false;
        }
        selection_start = input_value.length;
        selection_end = input_value.length;
        if (source[@"selection"] != nil) {
            if (![source[@"selection"] isKindOfClass:NSDictionary.class]) {
                set_error(error, "text-input selection must be an object");
                return false;
            }
            NSDictionary* selection = source[@"selection"];
            NSNumber* start = [selection[@"start"] isKindOfClass:NSNumber.class]
                ? selection[@"start"] : nil;
            NSNumber* end = [selection[@"end"] isKindOfClass:NSNumber.class]
                ? selection[@"end"] : nil;
            if (start == nil || end == nil || start.doubleValue != start.unsignedLongLongValue ||
                end.doubleValue != end.unsignedLongLongValue ||
                start.unsignedLongLongValue > end.unsignedLongLongValue ||
                end.unsignedLongLongValue > input_value.length) {
                set_error(error,
                    "text-input selection must be an ordered UTF-16 range within value");
                return false;
            }
            selection_start = start.unsignedIntegerValue;
            selection_end = end.unsignedIntegerValue;
        }
    }
    output.id = identity;
    output.bounds = proposed;
    output.corner_radius = std::max(0.0F, number_or(style, @"borderRadius", 0.0F));
    output.fill = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F));

    if ([type isEqualToString:@"custom"]) {
        if (![source[@"component"] isKindOfClass:NSString.class]) {
            set_error(error, "custom authoring node requires a component identifier");
            return false;
        }
        const auto properties = json_object(source[@"properties"]);
        if (!properties) {
            set_error(error, "custom authoring node properties must be a bounded JSON object");
            return false;
        }
        output.kind = vellum::graphics::SceneNode::Kind::custom;
        output.custom_component = cpp_string(source[@"component"]);
        output.custom_properties_json = *properties;
    } else if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        output.kind = vellum::graphics::SceneNode::Kind::text;
        output.text = direct_text(source);
        output.font_size = std::max(1.0F, number_or(style, @"fontSize", 14.0F));
        output.fill = parse_color(style[@"color"])
            .value_or(vellum::graphics::Color::hex(0x111827));
    } else if (is_text_input) {
        output.kind = vellum::graphics::SceneNode::Kind::rectangle;
        output.fill = parse_color(style[@"backgroundColor"])
            .value_or(vellum::graphics::Color::hex(0xFFFFFF));
        output.corner_radius = std::max(output.corner_radius, 6.0F);
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

    if (is_text_input) {
        const auto action = [&](NSString* name) -> std::string {
            return [events[name] isKindOfClass:NSString.class]
                ? cpp_string(events[name]) : std::string{};
        };
        context.text_inputs->push_back({
            .node_id = identity,
            .value = cpp_string(input_value),
            .placeholder = cpp_string(input_placeholder),
            .change_action = action(@"change"),
            .submit_action = action(@"submit"),
            .key_down_action = action(@"keyDown"),
            .selection_change_action = action(@"selectionChange"),
            .composition_start_action = action(@"compositionStart"),
            .composition_update_action = action(@"compositionUpdate"),
            .composition_end_action = action(@"compositionEnd"),
            .selection_start = selection_start,
            .selection_end = selection_end,
            .bounds = {
                absolute_x + proposed.x,
                absolute_y + proposed.y,
                proposed.width,
                proposed.height,
            },
        });
    }

    const bool semantic = is_text_input || [type isEqualToString:@"button"] ||
        [source[@"accessibilityLabel"] isKindOfClass:NSString.class] ||
        [source[@"accessibilityValue"] isKindOfClass:NSString.class] ||
        source[@"accessibilityRole"] != nil || source[@"accessibilityState"] != nil;
    if (semantic) {
        if ((source[@"accessibilityLabel"] != nil &&
             ![source[@"accessibilityLabel"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityValue"] != nil &&
             ![source[@"accessibilityValue"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityRole"] != nil &&
             ![source[@"accessibilityRole"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityState"] != nil &&
             ![source[@"accessibilityState"] isKindOfClass:NSDictionary.class])) {
            set_error(error, "accessibility label/value/role/state has an invalid type");
            return false;
        }
        NSDictionary* semantic_state = dictionary_or_empty(source[@"accessibilityState"]);
        AccessibilityNode node{
            .node_id = identity,
            .role = inferred_accessibility_role(type, source),
            .label = [source[@"accessibilityLabel"] isKindOfClass:NSString.class]
                ? cpp_string(source[@"accessibilityLabel"]) : direct_text(source),
            .value = [source[@"accessibilityValue"] isKindOfClass:NSString.class]
                ? cpp_string(source[@"accessibilityValue"])
                : (is_text_input ? cpp_string(input_value) : std::string{}),
            .bounds = {
                absolute_x + proposed.x,
                absolute_y + proposed.y,
                proposed.width,
                proposed.height,
            },
        };
        const auto boolean = [&](NSString* name, bool& destination, bool* present = nullptr) {
            id raw = semantic_state[name];
            if (raw == nil) return true;
            if (![raw isKindOfClass:NSNumber.class]) return false;
            destination = [raw boolValue];
            if (present != nullptr) *present = true;
            return true;
        };
        if (!boolean(@"disabled", node.state.disabled) ||
            !boolean(@"selected", node.state.selected) ||
            !boolean(@"expanded", node.state.expanded, &node.state.has_expanded)) {
            set_error(error, "accessibility state values must be booleans");
            return false;
        }
        if (semantic_state[@"checked"] != nil) {
            node.state.has_checked = true;
            if ([semantic_state[@"checked"] isKindOfClass:NSString.class] &&
                [semantic_state[@"checked"] isEqualToString:@"mixed"]) {
                node.state.mixed = true;
            } else if ([semantic_state[@"checked"] isKindOfClass:NSNumber.class]) {
                node.state.checked = [semantic_state[@"checked"] boolValue];
            } else {
                set_error(error, "accessibility checked state must be boolean or mixed");
                return false;
            }
        }
        if ([events[@"press"] isKindOfClass:NSString.class]) {
            node.actions.push_back("press");
        }
        if (is_text_input) {
            node.actions.push_back("focus");
            node.actions.push_back("set-value");
        }
        context.accessibility_nodes->push_back(std::move(node));
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
    if (is_text_input) {
        const bool showing_placeholder = input_value.length == 0U;
        NSString* display = showing_placeholder ? input_placeholder : input_value;
        if (display.length > 0U) {
            vellum::graphics::SceneNode label;
            const float font_size = std::max(1.0F, number_or(style, @"fontSize", 14.0F));
            label.id = identity + "/value";
            if (!context.identities.insert(label.id).second) {
                set_error(error, "generated text-input value id collides: " + label.id);
                return false;
            }
            if (++context.nodes > kMaximumNodes) {
                set_error(error, "authoring tree exceeds the node limit");
                return false;
            }
            label.kind = vellum::graphics::SceneNode::Kind::text;
            label.bounds = {12.0F, (proposed.height - font_size * 1.4F) * 0.5F,
                            std::max(0.0F, proposed.width - 24.0F), font_size * 1.4F};
            label.fill = parse_color(style[@"color"]).value_or(
                vellum::graphics::Color::hex(showing_placeholder ? 0x94A3B8 : 0x111827));
            label.font_size = font_size;
            label.text = cpp_string(display);
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
    const bool legacy = [envelope[@"protocol"] isEqual:@"vellum.authoring-host.v1"];
    const bool asynchronous =
        [envelope[@"protocol"] isEqual:@"vellum.authoring-host.v2"] &&
        [envelope[@"kind"] isEqual:@"render-result"];
    if ((!legacy && !asynchronous) ||
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
    MaterializeContext context{
        .interactions = &candidate.interactions,
        .text_inputs = &candidate.text_inputs,
        .accessibility_nodes = &candidate.accessibility_nodes,
    };
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
        context_[@"setTimeout"] = ^NSNumber*(JSValue* callback, double delay) {
            return @(schedule_timer(callback, delay));
        };
        context_[@"clearTimeout"] = ^(double identifier) {
            cancel_timer(identifier);
        };
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

    bool pump(std::uint64_t advance_milliseconds, std::size_t maximum_tasks,
              RenderedApplication& output, PumpResult& result, std::string* error) {
        result = {};
        if (maximum_tasks == 0U) {
            set_error(error, "JavaScript pump maximum_tasks must be positive");
            return false;
        }
        if (advance_milliseconds >
            std::numeric_limits<std::uint64_t>::max() - clock_milliseconds_) {
            set_error(error, "JavaScript timer clock overflow");
            return false;
        }
        clock_milliseconds_ += advance_milliseconds;
        if (!run_ready_tasks(maximum_tasks, result.tasks_executed, error)) return false;
        if (has_ready_timer()) {
            set_error(error, "JavaScript pump task limit exceeded");
            return false;
        }
        if (!materialize_if_dirty(output, result.rendered, error)) return false;
        bool dirty = false;
        if (!bridge_dirty(dirty, error)) return false;
        result.idle = timers_.empty() && !dirty;
        return consume_exception(error);
    }

    bool wait_for_idle(std::size_t maximum_tasks, RenderedApplication& output,
                       PumpResult& result, std::string* error) {
        result = {};
        if (maximum_tasks == 0U) {
            set_error(error, "JavaScript wait_for_idle maximum_tasks must be positive");
            return false;
        }
        while (!timers_.empty()) {
            const auto next = std::min_element(
                timers_.begin(), timers_.end(),
                [](const Timer& left, const Timer& right) {
                    return std::tie(left.due, left.order) < std::tie(right.due, right.order);
                });
            clock_milliseconds_ = std::max(clock_milliseconds_, next->due);
            std::size_t executed = 0U;
            if (!run_ready_tasks(maximum_tasks - result.tasks_executed, executed, error)) {
                return false;
            }
            result.tasks_executed += executed;
            if (result.tasks_executed >= maximum_tasks && !timers_.empty()) {
                set_error(error, "JavaScript wait_for_idle task limit exceeded");
                return false;
            }
        }
        if (!materialize_if_dirty(output, result.rendered, error)) return false;
        bool dirty = false;
        if (!bridge_dirty(dirty, error)) return false;
        result.idle = !dirty;
        return consume_exception(error);
    }

    std::string last_diagnostic_json() const { return last_diagnostic_json_; }

private:
    struct Timer final {
        std::uint64_t id = 0;
        std::uint64_t due = 0;
        std::uint64_t order = 0;
        __strong JSValue* callback = nil;
    };

    std::uint64_t schedule_timer(JSValue* callback, double delay) {
        if (callback == nil || !callback.isObject) {
            context_.exception = [JSValue valueWithNewErrorFromMessage:
                @"setTimeout callback must be callable" inContext:context_];
            return 0;
        }
        const double bounded = std::isfinite(delay)
            ? std::clamp(delay, 0.0, static_cast<double>(kMaximumTimerDelayMilliseconds))
            : 0.0;
        const auto milliseconds = static_cast<std::uint64_t>(std::ceil(bounded));
        const std::uint64_t identifier = next_timer_id_++;
        timers_.push_back({
            .id = identifier,
            .due = clock_milliseconds_ + milliseconds,
            .order = next_timer_order_++,
            .callback = callback,
        });
        return identifier;
    }

    void cancel_timer(double identifier) {
        if (!std::isfinite(identifier) || identifier < 1.0) return;
        const auto value = static_cast<std::uint64_t>(identifier);
        std::erase_if(timers_, [value](const Timer& timer) { return timer.id == value; });
    }

    bool has_ready_timer() const {
        return std::any_of(timers_.begin(), timers_.end(), [&](const Timer& timer) {
            return timer.due <= clock_milliseconds_;
        });
    }

    bool run_ready_tasks(
        std::size_t maximum_tasks, std::size_t& executed, std::string* error) {
        executed = 0U;
        while (executed < maximum_tasks) {
            const auto next = std::min_element(
                timers_.begin(), timers_.end(),
                [](const Timer& left, const Timer& right) {
                    return std::tie(left.due, left.order) < std::tie(right.due, right.order);
                });
            if (next == timers_.end() || next->due > clock_milliseconds_) break;
            JSValue* callback = next->callback;
            timers_.erase(next);
            context_.exception = nil;
            [callback callWithArguments:@[]];
            if (!consume_exception(error)) return false;
            // Returning through the JavaScriptCore API establishes a Promise
            // job checkpoint; a no-op evaluation makes that boundary explicit.
            [context_ evaluateScript:@"void 0"];
            if (!consume_exception(error)) return false;
            ++executed;
        }
        return true;
    }

    bool bridge_dirty(bool& dirty, std::string* error) {
        dirty = false;
        JSValue* bridge = context_[@"__vellum"];
        JSValue* method = [bridge valueForProperty:@"isDirty"];
        if (method == nil || method.isUndefined || !method.isObject) {
            return consume_exception(error);
        }
        JSValue* result = [bridge invokeMethod:@"isDirty" withArguments:@[]];
        if (!consume_exception(error)) return false;
        dirty = result.toBool;
        return true;
    }

    bool materialize_if_dirty(
        RenderedApplication& output, bool& rendered, std::string* error) {
        rendered = false;
        JSValue* bridge = context_[@"__vellum"];
        JSValue* method = [bridge valueForProperty:@"pumpJSON"];
        bool dirty = false;
        if (!bridge_dirty(dirty, error)) return false;
        if (method == nil || method.isUndefined || !method.isObject || !dirty) {
            return consume_exception(error);
        }
        if (!call_tree(@"pumpJSON", @[], output, error)) return false;
        rendered = true;
        return true;
    }

    bool consume_exception(std::string* error) {
        JSValue* exception = context_.exception;
        if (exception == nil || exception.isUndefined || exception.isNull) return true;
        context_.exception = nil;
        NSString* code = @"VELLUM_SOURCE_MAP_MISSING";
        NSString* message = exception.toString ?: @"JavaScript exception";
        JSValue* mapper = context_[@"__vellumMapExceptionJSON"];
        if (mapper != nil && !mapper.isUndefined && mapper.isObject) {
            JSValue* mapped = [mapper callWithArguments:@[exception]];
            JSValue* mapping_exception = context_.exception;
            context_.exception = nil;
            if (mapping_exception == nil || mapping_exception.isUndefined ||
                mapping_exception.isNull) {
                NSString* encoded = mapped != nil && mapped.isString
                    ? mapped.toString : nil;
                NSData* data = [encoded dataUsingEncoding:NSUTF8StringEncoding];
                NSError* parse_error = nil;
                id value = data == nil ? nil : [NSJSONSerialization
                    JSONObjectWithData:data options:0 error:&parse_error];
                NSDictionary* diagnostic = [value isKindOfClass:NSDictionary.class]
                    ? static_cast<NSDictionary*>(value) : nil;
                if (parse_error == nil && diagnostic != nil &&
                    [diagnostic[@"protocol"] isEqual:@"vellum.authoring-host.v2"] &&
                    [diagnostic[@"kind"] isEqual:@"diagnostic"] &&
                    [diagnostic[@"code"] isEqual:@"VELLUM_RUNTIME_EXCEPTION"] &&
                    [diagnostic[@"message"] isKindOfClass:NSString.class] &&
                    [diagnostic[@"source"] isKindOfClass:NSDictionary.class] &&
                    [diagnostic[@"stack"] isKindOfClass:NSArray.class]) {
                    const auto canonical = json_object(diagnostic);
                    if (canonical.has_value()) {
                        last_diagnostic_json_ = *canonical;
                        set_error(error, last_diagnostic_json_);
                        return false;
                    }
                }
            }
            code = @"VELLUM_SOURCE_MAP_INVALID";
        }
        NSDictionary* fallback = @{
            @"protocol": @"vellum.authoring-host.v2",
            @"kind": @"diagnostic",
            @"severity": @"error",
            @"code": code,
            @"message": message,
        };
        last_diagnostic_json_ = json_object(fallback).value_or(
            R"({"code":"VELLUM_SOURCE_MAP_INVALID","kind":"diagnostic","message":"could not encode JavaScript exception","protocol":"vellum.authoring-host.v2","severity":"error"})");
        set_error(error, last_diagnostic_json_);
        return false;
    }

    NSString* call_string(NSString* method, NSArray* arguments, std::string* error) {
        context_.exception = nil;
        JSValue* bridge = context_[@"__vellum"];
        JSValue* result = [bridge invokeMethod:method withArguments:arguments];
        if (!consume_exception(error)) return nil;
        if (result == nil || !result.isString) {
            set_error(error, "JavaScript authoring bridge method did not return a string: " +
                             cpp_string(method));
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
    std::vector<Timer> timers_;
    std::uint64_t clock_milliseconds_ = 0;
    std::uint64_t next_timer_id_ = 1;
    std::uint64_t next_timer_order_ = 1;
    std::string last_diagnostic_json_;
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

bool JsApplication::pump(
    std::uint64_t advance_milliseconds, std::size_t maximum_tasks,
    RenderedApplication& output, PumpResult& result, std::string* error) {
    @autoreleasepool {
        return impl_->pump(
            advance_milliseconds, maximum_tasks, output, result, error);
    }
}

bool JsApplication::wait_for_idle(
    std::size_t maximum_tasks, RenderedApplication& output,
    PumpResult& result, std::string* error) {
    @autoreleasepool {
        return impl_->wait_for_idle(maximum_tasks, output, result, error);
    }
}

std::string JsApplication::last_diagnostic_json() const {
    return impl_->last_diagnostic_json();
}

}  // namespace vellum::authoring
