#include "rendered_tree_materializer.hpp"

#import <Foundation/Foundation.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <set>
#include <utility>

namespace vellum::authoring {
namespace {

constexpr std::size_t kMaximumJsonBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumNodes = 100000U;
constexpr std::size_t kMaximumDepth = 256U;
constexpr NSUInteger kMaximumTextInputBytes = 64U * 1024U;
constexpr NSUInteger kMaximumPlaceholderBytes = 4U * 1024U;
// Retained-tree consumers share these defaults across native and web. Controls
// retain the established 44-point density; zero keeps an unspecified generic
// child eligible for the parent stack's remaining-axis fill behavior.
constexpr float kDefaultButtonHeight = 44.0F;
constexpr float kDefaultTextInputHeight = 44.0F;
constexpr float kDefaultGenericHeight = 0.0F;
constexpr float kTextLineHeightMultiplier = 1.4F;

void set_error(std::string* destination, std::string value) {
    if (destination != nullptr) *destination = std::move(value);
}

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    if (utf8 == nullptr) return {};
    return std::string{
        utf8,
        static_cast<std::size_t>(
            [value lengthOfBytesUsingEncoding:NSUTF8StringEncoding])};
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
        return number_or(style, @"fontSize", 14.0F) * kTextLineHeightMultiplier;
    }
    if ([type isEqualToString:@"button"]) return kDefaultButtonHeight;
    if ([type isEqualToString:@"text-input"]) return kDefaultTextInputHeight;
    return kDefaultGenericHeight;
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

}  // namespace

bool materialize_rendered_tree(
    std::string_view rendered_json,
    RenderedApplication& output,
    std::string* error) {
    if (rendered_json.size() > kMaximumJsonBytes) {
        set_error(error, "JavaScript authoring bridge JSON is invalid or too large");
        return false;
    }
    NSData* data = [NSData dataWithBytes:rendered_json.data()
                                  length:rendered_json.size()];
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

}  // namespace vellum::authoring
