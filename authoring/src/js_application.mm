#include <vellum/authoring/js_application.hpp>

#include "rendered_tree_materializer.hpp"

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
    if (utf8 == nullptr) return {};
    return std::string{
        utf8,
        static_cast<std::size_t>(
            [value lengthOfBytesUsingEncoding:NSUTF8StringEncoding])};
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

    bool configure_service_host(
        std::string_view capabilities_json, std::string* error) {
        NSString* encoded = ns_string(capabilities_json);
        NSData* data = [encoded dataUsingEncoding:NSUTF8StringEncoding];
        NSError* parse_error = nil;
        id capabilities = data == nil ? nil : [NSJSONSerialization
            JSONObjectWithData:data options:0 error:&parse_error];
        if (![capabilities isKindOfClass:NSDictionary.class] ||
            ![NSJSONSerialization isValidJSONObject:capabilities]) {
            set_error(error, "service capabilities must be a JSON object");
            return false;
        }
        context_.exception = nil;
        context_[@"__vellumNativeServiceCapabilities"] = capabilities;
        [context_ evaluateScript:
            @"globalThis.__vellumServiceHost={"
             "capabilities:globalThis.__vellumNativeServiceCapabilities,"
             "responses:[],requests:[],"
             "request(request){"
               "this.requests.push(request);"
               "const response=this.responses.shift();"
               "if(!response)return Promise.reject(new Error('no queued service response'));"
               "return Promise.resolve({...response,id:request.id});"
             "}"
             "};"
             "delete globalThis.__vellumNativeServiceCapabilities;"];
        return consume_exception(error);
    }

    bool enqueue_service_response(
        std::string_view response_json, std::string* error) {
        NSString* encoded = ns_string(response_json);
        NSData* data = [encoded dataUsingEncoding:NSUTF8StringEncoding];
        NSError* parse_error = nil;
        id response = data == nil ? nil : [NSJSONSerialization
            JSONObjectWithData:data options:0 error:&parse_error];
        if (![response isKindOfClass:NSDictionary.class] ||
            ![NSJSONSerialization isValidJSONObject:response]) {
            set_error(error, "service response must be a JSON object");
            return false;
        }
        NSDictionary* envelope = static_cast<NSDictionary*>(response);
        NSDictionary* detail = [envelope[@"error"] isKindOfClass:NSDictionary.class]
            ? static_cast<NSDictionary*>(envelope[@"error"]) : nil;
        if ([envelope[@"ok"] isEqual:@NO] &&
            [detail[@"code"] isEqual:@"capability-denied"]) {
            // A denied capability rejects before a provider request exists.
            // Do not leave its illustrative response queued for the next
            // granted service operation.
            return true;
        }
        context_.exception = nil;
        JSValue* host = context_[@"__vellumServiceHost"];
        JSValue* responses = [host valueForProperty:@"responses"];
        JSValue* push = [responses valueForProperty:@"push"];
        if (host == nil || host.isUndefined || responses == nil ||
            responses.isUndefined || push == nil || !push.isObject) {
            set_error(error, "native service host is not configured");
            return false;
        }
        [responses invokeMethod:@"push" withArguments:@[response]];
        return consume_exception(error);
    }

    bool service_response_queue_empty(bool& empty, std::string* error) {
        empty = false;
        context_.exception = nil;
        JSValue* host = context_[@"__vellumServiceHost"];
        JSValue* responses = [host valueForProperty:@"responses"];
        JSValue* length = [responses valueForProperty:@"length"];
        if (host == nil || host.isUndefined || responses == nil ||
            responses.isUndefined || length == nil || !length.isNumber) {
            set_error(error, "native service host is not configured");
            return false;
        }
        empty = length.toInt32 == 0;
        return consume_exception(error);
    }

    bool has_command(
        std::string_view command, bool& present, std::string* error) {
        present = false;
        NSString* value = ns_string(command);
        if (value == nil || value.length == 0U) {
            set_error(error, "command must be non-empty valid UTF-8");
            return false;
        }
        context_.exception = nil;
        JSValue* bridge = context_[@"__vellum"];
        JSValue* method = [bridge valueForProperty:@"hasCommand"];
        if (method == nil || method.isUndefined || !method.isObject) {
            set_error(error, "application bridge does not expose the command registry");
            return false;
        }
        JSValue* result = [bridge invokeMethod:@"hasCommand" withArguments:@[value]];
        if (!consume_exception(error)) return false;
        if (result == nil || !result.isBoolean) {
            set_error(error, "application command registry returned a non-boolean result");
            return false;
        }
        present = result.toBool;
        return true;
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
        return result != nil &&
            materialize_rendered_tree(cpp_string(result), output, error);
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

bool JsApplication::configure_service_host(
    std::string_view capabilities_json, std::string* error) {
    @autoreleasepool {
        return impl_->configure_service_host(capabilities_json, error);
    }
}

bool JsApplication::enqueue_service_response(
    std::string_view response_json, std::string* error) {
    @autoreleasepool {
        return impl_->enqueue_service_response(response_json, error);
    }
}

bool JsApplication::service_response_queue_empty(
    bool& empty, std::string* error) {
    @autoreleasepool {
        return impl_->service_response_queue_empty(empty, error);
    }
}

bool JsApplication::has_command(
    std::string_view command, bool& present, std::string* error) {
    @autoreleasepool { return impl_->has_command(command, present, error); }
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
