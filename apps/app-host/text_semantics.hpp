#pragma once

#include <vellum/authoring/js_application.hpp>

#include <string>
#include <string_view>

#ifdef __OBJC__
@class NSDictionary;
@class NSString;
#else
class NSDictionary;
class NSString;
#endif

namespace vellum::app_host::text_semantics {

const authoring::TextInputControl* find_text_input(
    const authoring::RenderedApplication& rendered, std::string_view node_id);

bool dispatch_payload(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view action,
    NSDictionary* payload,
    std::string* error);

bool press_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string* error);

bool input_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view value,
    NSString* source,
    std::string* error);

bool key_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view key,
    NSString* source,
    std::string* error);

bool focus_node(
    const authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string* error);

bool compose_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view composition,
    std::string* error);

bool assert_accessibility_node(
    const authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view expected_json,
    std::string* error);

bool assert_node_text(
    const authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view expected,
    std::string* error);

bool touch_node(
    authoring::JsApplication& application,
    authoring::RenderedApplication& rendered,
    std::string_view node_id,
    std::string_view event_json,
    std::string* error);

}  // namespace vellum::app_host::text_semantics
