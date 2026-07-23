#import "text_semantics.hpp"

#import <Foundation/Foundation.h>

#include <algorithm>
#include <iterator>

namespace vellum::app_host::text_semantics {
namespace {

constexpr std::size_t kMaximumInputBytes = 64U * 1024U;

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    return utf8 == nullptr ? std::string{} : std::string{utf8};
}

bool valid_key(std::string_view value) {
    constexpr std::string_view supported[] = {
        "Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End", "Delete",
    };
    return std::find(std::begin(supported), std::end(supported), value) !=
           std::end(supported);
}

bool serialize_payload(
    NSDictionary* value, std::string& output, std::string* error) {
    NSError* json_error = nil;
    NSData* data =
        [NSJSONSerialization dataWithJSONObject:value options:0 error:&json_error];
    if (data == nil || data.length > kMaximumInputBytes + 4096U) {
        if (error) *error = "event payload could not be serialized within its bound";
        return false;
    }
    NSString* text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (text == nil) {
        if (error) *error = "event payload did not encode as UTF-8";
        return false;
    }
    output.assign(
        text.UTF8String, [text lengthOfBytesUsingEncoding:NSUTF8StringEncoding]);
    return true;
}

std::string without_last_grapheme(std::string_view value) {
    NSString* text = [[NSString alloc]
        initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];
    if (text == nil || text.length == 0U) return {};
    const NSRange range =
        [text rangeOfComposedCharacterSequenceAtIndex:text.length - 1U];
    return cpp_string([text substringToIndex:range.location]);
}

}  // namespace

const authoring::TextInputControl* find_text_input(
    const authoring::RenderedApplication& rendered, std::string_view node_id) {
    const auto input = std::find_if(
        rendered.text_inputs.begin(), rendered.text_inputs.end(),
        [node_id](const authoring::TextInputControl& item) {
            return item.node_id == node_id;
        });
    return input == rendered.text_inputs.end() ? nullptr : &*input;
}

bool dispatch_payload(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view action,
    NSDictionary* payload,
    std::string* error) {
    std::string encoded;
    return serialize_payload(payload, encoded, error) &&
           application.dispatch(action, encoded, rendered, error);
}

bool press_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string* error) {
    const auto interaction = std::find_if(
        rendered.interactions.begin(), rendered.interactions.end(),
        [node_id](const authoring::Interaction& item) {
            return item.node_id == node_id && item.event == "press";
        });
    if (interaction == rendered.interactions.end()) {
        if (error) {
            *error = "scenario press target is missing or not pressable: " +
                     std::string(node_id);
        }
        return false;
    }
    return application.dispatch(
        interaction->action, R"({"pointerType":"automation"})", rendered, error);
}

bool input_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view value,
    NSString* source,
    std::string* error) {
    const authoring::TextInputControl* input = find_text_input(rendered, node_id);
    if (input == nullptr || input->change_action.empty()) {
        if (error) {
            *error = "scenario input target is missing or not editable: " +
                     std::string(node_id);
        }
        return false;
    }
    NSString* text = [[NSString alloc]
        initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];
    if (text == nil || value.size() > kMaximumInputBytes) {
        if (error) *error = "text input value is invalid UTF-8 or exceeds 64 KiB";
        return false;
    }
    const std::string action = input->change_action;
    return dispatch_payload(
        application, rendered, action,
        @{@"value": text, @"inputType": source}, error);
}

bool key_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view key,
    NSString* source,
    std::string* error) {
    const authoring::TextInputControl* input = find_text_input(rendered, node_id);
    if (input == nullptr) {
        if (error) {
            *error = "scenario key target is missing or not a text input: " +
                     std::string(node_id);
        }
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
        if (!dispatch_payload(
                application, rendered, action,
                @{@"key": key_text, @"repeat": @NO, @"source": source}, error)) {
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
        NSString* value =
            [[NSString alloc] initWithUTF8String:input->value.c_str()];
        if (!dispatch_payload(
                application, rendered, action,
                @{@"value": value ?: @"", @"source": source}, error)) {
            return false;
        }
        dispatched = true;
    }
    if (key == "Backspace" && !input->change_action.empty()) {
        const std::string next_value = without_last_grapheme(input->value);
        if (!input_node(
                application, rendered, node_id, next_value, source, error)) {
            return false;
        }
        dispatched = true;
    }
    if (!dispatched &&
        (key == "ArrowLeft" || key == "ArrowRight" || key == "ArrowUp" ||
         key == "ArrowDown" || key == "Home" || key == "End" ||
         key == "Escape" || key == "Tab" || key == "Delete")) {
        return true;
    }
    if (!dispatched) {
        if (error) {
            *error =
                "text input has no handler for semantic key: " + std::string(key);
        }
        return false;
    }
    return true;
}

bool focus_node(
    const authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string* error) {
    if (find_text_input(rendered, node_id) != nullptr) return true;
    if (error) {
        *error =
            "scenario focus target is missing or not editable: " + std::string(node_id);
    }
    return false;
}

bool compose_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view composition,
    std::string* error) {
    const authoring::TextInputControl* input = find_text_input(rendered, node_id);
    if (input == nullptr || input->change_action.empty() ||
        input->composition_start_action.empty() ||
        input->composition_update_action.empty() ||
        input->composition_end_action.empty()) {
        if (error) {
            *error = "scenario composition target lacks the complete IME contract: " +
                     std::string(node_id);
        }
        return false;
    }
    NSString* current = [NSString stringWithUTF8String:input->value.c_str()];
    NSString* text = [[NSString alloc]
        initWithBytes:composition.data()
               length:composition.size()
             encoding:NSUTF8StringEncoding];
    if (current == nil || text == nil ||
        input->selection_start > input->selection_end ||
        input->selection_end > current.length) {
        if (error) *error = "scenario composition contains invalid UTF-8 or selection";
        return false;
    }
    const NSRange selection = NSMakeRange(
        input->selection_start, input->selection_end - input->selection_start);
    NSString* next =
        [current stringByReplacingCharactersInRange:selection withString:text];
    const NSUInteger caret = selection.location + text.length;
    const std::string start_action = input->composition_start_action;
    const std::string update_action = input->composition_update_action;
    const std::string change_action = input->change_action;
    const std::string end_action = input->composition_end_action;
    NSDictionary* prior_payload = @{
        @"text": @"",
        @"value": current,
        @"selection": @{
            @"start": @(selection.location),
            @"end": @(NSMaxRange(selection)),
        },
    };
    NSDictionary* composed_payload = @{
        @"text": text,
        @"value": next,
        @"selection": @{@"start": @(caret), @"end": @(caret)},
    };
    return dispatch_payload(
               application, rendered, start_action, prior_payload, error) &&
           dispatch_payload(
               application, rendered, update_action, composed_payload, error) &&
           dispatch_payload(
               application, rendered, change_action,
               @{
                   @"value": next,
                   @"inputType": @"insertCompositionText",
                   @"selection": @{@"start": @(caret), @"end": @(caret)},
               },
               error) &&
           dispatch_payload(
               application, rendered, end_action, composed_payload, error);
}

bool assert_accessibility_node(
    const authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view expected_json,
    std::string* error) {
    NSString* encoded = [[NSString alloc]
        initWithBytes:expected_json.data()
               length:expected_json.size()
             encoding:NSUTF8StringEncoding];
    NSData* data = [encoded dataUsingEncoding:NSUTF8StringEncoding];
    NSError* json_error = nil;
    id parsed = data == nil
        ? nil
        : [NSJSONSerialization JSONObjectWithData:data
                                           options:0
                                             error:&json_error];
    if (![parsed isKindOfClass:NSDictionary.class]) {
        if (error) *error = "accessibility assertion must be a JSON object";
        return false;
    }
    const auto found = std::find_if(
        rendered.accessibility_nodes.begin(), rendered.accessibility_nodes.end(),
        [node_id](const authoring::AccessibilityNode& node) {
            return node.node_id == node_id;
        });
    if (found == rendered.accessibility_nodes.end()) {
        if (error) {
            *error = "accessibility target is missing: " + std::string(node_id);
        }
        return false;
    }
    NSDictionary* expected = parsed;
    const auto matches = [&](NSString* key, const std::string& actual) {
        id value = expected[key];
        return value == nil ||
               ([value isKindOfClass:NSString.class] &&
                cpp_string(static_cast<NSString*>(value)) == actual);
    };
    if (!matches(@"label", found->label) || !matches(@"role", found->role) ||
        !matches(@"value", found->value)) {
        if (error) {
            *error = "accessibility assertion mismatch for " +
                     std::string(node_id) + ": role=" + found->role +
                     " label=" + found->label + " value=" + found->value;
        }
        return false;
    }
    return true;
}

}  // namespace vellum::app_host::text_semantics
