#include <vellum/c/vellum_host.h>

#include <vellum/authoring/js_application.hpp>
#include <vellum/graphics/capture_stats.hpp>
#include <vellum/graphics/skia_dawn_surface.hpp>

#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <exception>
#include <memory>
#include <new>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

struct vellum_host_app final {
    std::unique_ptr<vellum::authoring::JsApplication> application;
    vellum::authoring::RenderedApplication rendered;
    std::string focused;
    std::thread::id owner;
};

struct vellum_host_surface final {
    std::unique_ptr<vellum::graphics::SkiaDawnSurface> surface;
    std::vector<std::uint8_t> capture;
    std::thread::id owner;
};

namespace {

using vellum::authoring::AccessibilityNode;
using vellum::authoring::Interaction;
using vellum::authoring::TextInputControl;

constexpr std::size_t kMaximumKeyBytes = 64U;
constexpr std::size_t kMaximumTextBytes = 64U * 1024U;
constexpr std::uint32_t kDefaultSettleTasks = 4096U;

thread_local std::string last_error;

vellum_host_status fail(vellum_host_status status, std::string message) {
    last_error = std::move(message);
    return status;
}

template <typename Body>
vellum_host_status guarded(Body&& body) noexcept {
    last_error.clear();
    try {
        return body();
    } catch (const std::bad_alloc&) {
        return fail(VELLUM_HOST_ERROR_FAILED, "out of memory");
    } catch (const std::exception& error) {
        return fail(VELLUM_HOST_ERROR_FAILED, error.what());
    } catch (...) {
        return fail(VELLUM_HOST_ERROR_FAILED, "unknown native exception");
    }
}

template <typename Struct>
bool sized(const Struct* value) {
    return value != nullptr && value->struct_size >= sizeof(Struct);
}

template <typename Struct>
vellum_host_status require_sized(const Struct* value, const char* name) {
    if (value == nullptr) {
        return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, std::string(name) + " is null");
    }
    if (!sized(value)) {
        return fail(VELLUM_HOST_ERROR_VERSION,
            std::string(name) + " struct_size is smaller than ABI v1 requires");
    }
    return VELLUM_HOST_OK;
}

template <typename Handle>
vellum_host_status require_owner(const Handle* handle, const char* name) {
    if (handle == nullptr) {
        return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, std::string(name) + " is null");
    }
    if (handle->owner != std::this_thread::get_id()) {
        return fail(VELLUM_HOST_ERROR_WRONG_THREAD,
            std::string(name) + " is confined to the thread that created it");
    }
    return VELLUM_HOST_OK;
}

vellum_host_status native_failure(vellum_host_status status, const std::string& error,
                                  const char* fallback) {
    return fail(status, error.empty() ? std::string(fallback) : error);
}

std::optional<std::u16string> utf16_from_utf8(std::string_view text) {
    std::u16string output;
    output.reserve(text.size());
    for (std::size_t index = 0; index < text.size();) {
        const auto lead = static_cast<unsigned char>(text[index]);
        std::uint32_t code = 0;
        std::size_t length = 0;
        if (lead < 0x80U) {
            code = lead;
            length = 1;
        } else if ((lead & 0xE0U) == 0xC0U) {
            code = lead & 0x1FU;
            length = 2;
        } else if ((lead & 0xF0U) == 0xE0U) {
            code = lead & 0x0FU;
            length = 3;
        } else if ((lead & 0xF8U) == 0xF0U) {
            code = lead & 0x07U;
            length = 4;
        } else {
            return std::nullopt;
        }
        if (index + length > text.size()) return std::nullopt;
        for (std::size_t offset = 1; offset < length; ++offset) {
            const auto next = static_cast<unsigned char>(text[index + offset]);
            if ((next & 0xC0U) != 0x80U) return std::nullopt;
            code = (code << 6U) | (next & 0x3FU);
        }
        const bool overlong = (length == 2 && code < 0x80U) ||
                              (length == 3 && code < 0x800U) ||
                              (length == 4 && code < 0x10000U);
        if (overlong || code > 0x10FFFFU || (code >= 0xD800U && code <= 0xDFFFU)) {
            return std::nullopt;
        }
        if (code >= 0x10000U) {
            code -= 0x10000U;
            output.push_back(static_cast<char16_t>(0xD800U + (code >> 10U)));
            output.push_back(static_cast<char16_t>(0xDC00U + (code & 0x3FFU)));
        } else {
            output.push_back(static_cast<char16_t>(code));
        }
        index += length;
    }
    return output;
}

std::string utf8_from_utf16(std::u16string_view text) {
    std::string output;
    output.reserve(text.size());
    for (std::size_t index = 0; index < text.size(); ++index) {
        std::uint32_t code = text[index];
        if (code >= 0xD800U && code <= 0xDBFFU && index + 1 < text.size() &&
            text[index + 1] >= 0xDC00U && text[index + 1] <= 0xDFFFU) {
            code = 0x10000U + ((code - 0xD800U) << 10U) + (text[index + 1] - 0xDC00U);
            ++index;
        } else if (code >= 0xD800U && code <= 0xDFFFU) {
            code = 0xFFFDU;
        }
        if (code < 0x80U) {
            output.push_back(static_cast<char>(code));
        } else if (code < 0x800U) {
            output.push_back(static_cast<char>(0xC0U | (code >> 6U)));
            output.push_back(static_cast<char>(0x80U | (code & 0x3FU)));
        } else if (code < 0x10000U) {
            output.push_back(static_cast<char>(0xE0U | (code >> 12U)));
            output.push_back(static_cast<char>(0x80U | ((code >> 6U) & 0x3FU)));
            output.push_back(static_cast<char>(0x80U | (code & 0x3FU)));
        } else {
            output.push_back(static_cast<char>(0xF0U | (code >> 18U)));
            output.push_back(static_cast<char>(0x80U | ((code >> 12U) & 0x3FU)));
            output.push_back(static_cast<char>(0x80U | ((code >> 6U) & 0x3FU)));
            output.push_back(static_cast<char>(0x80U | (code & 0x3FU)));
        }
    }
    return output;
}

std::string json_string(std::string_view text) {
    std::string output = "\"";
    for (const char raw : text) {
        const auto value = static_cast<unsigned char>(raw);
        switch (raw) {
            case '"': output += "\\\""; break;
            case '\\': output += "\\\\"; break;
            case '\n': output += "\\n"; break;
            case '\r': output += "\\r"; break;
            case '\t': output += "\\t"; break;
            default:
                if (value < 0x20U) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof escaped, "\\u%04x", value);
                    output += escaped;
                } else {
                    output.push_back(raw);
                }
        }
    }
    output.push_back('"');
    return output;
}

std::string selection_json(std::size_t start, std::size_t end) {
    return "{\"start\":" + std::to_string(start) + ",\"end\":" + std::to_string(end) + "}";
}

bool contains(const vellum::graphics::Rect& bounds, float x, float y) {
    return x >= bounds.x && y >= bounds.y && x <= bounds.x + bounds.width &&
           y <= bounds.y + bounds.height;
}

const TextInputControl* find_input(const vellum_host_app& app, std::string_view node_id) {
    const auto& inputs = app.rendered.text_inputs;
    const auto match = std::find_if(inputs.begin(), inputs.end(),
        [node_id](const TextInputControl& input) { return input.node_id == node_id; });
    return match == inputs.end() ? nullptr : &*match;
}

vellum_host_status dispatch(vellum_host_app& app, std::string_view action,
                            const std::string& payload) {
    std::string error;
    if (!app.application->dispatch(action, payload, app.rendered, &error)) {
        return native_failure(VELLUM_HOST_ERROR_FAILED, error, "dispatch failed");
    }
    if (!app.focused.empty() && find_input(app, app.focused) == nullptr) {
        app.focused.clear();
    }
    return VELLUM_HOST_OK;
}

vellum_host_status replace_text(vellum_host_app& app, const TextInputControl& input,
                                std::u16string_view replacement, bool backward_delete,
                                const char* input_type) {
    auto value = utf16_from_utf8(input.value);
    if (!value) return fail(VELLUM_HOST_ERROR_FAILED, "text input value is not valid UTF-8");
    std::size_t start = std::min(input.selection_start, value->size());
    std::size_t end = std::clamp(input.selection_end, start, value->size());
    if (backward_delete && start == end) {
        if (start == 0) return VELLUM_HOST_OK;
        start -= 1;
        if (start > 0 && (*value)[start] >= 0xDC00U && (*value)[start] <= 0xDFFFU &&
            (*value)[start - 1] >= 0xD800U && (*value)[start - 1] <= 0xDBFFU) {
            start -= 1;
        }
    }
    value->replace(start, end - start, replacement);
    const std::size_t caret = start + replacement.size();
    const std::string action = input.change_action;
    return dispatch(app, action,
        "{\"value\":" + json_string(utf8_from_utf16(*value)) +
        ",\"inputType\":" + json_string(input_type) +
        ",\"selection\":" + selection_json(caret, caret) + "}");
}

const char* pointer_type(std::uint32_t device) {
    switch (device) {
        case VELLUM_HOST_POINTER_TOUCH: return "touch";
        case VELLUM_HOST_POINTER_PEN: return "pen";
        default: return "mouse";
    }
}

vellum_host_rect rect(const vellum::graphics::Rect& bounds) {
    return {bounds.x, bounds.y, bounds.width, bounds.height};
}

std::uint32_t state_flags(const vellum::authoring::AccessibilityState& state) {
    std::uint32_t flags = 0;
    if (state.disabled) flags |= VELLUM_HOST_A11Y_DISABLED;
    if (state.selected) flags |= VELLUM_HOST_A11Y_SELECTED;
    if (state.checked) flags |= VELLUM_HOST_A11Y_CHECKED;
    if (state.mixed) flags |= VELLUM_HOST_A11Y_MIXED;
    if (state.expanded) flags |= VELLUM_HOST_A11Y_EXPANDED;
    if (state.has_checked) flags |= VELLUM_HOST_A11Y_HAS_CHECKED;
    if (state.has_expanded) flags |= VELLUM_HOST_A11Y_HAS_EXPANDED;
    return flags;
}

bool valid_scale(float scale) {
    return std::isfinite(scale) && scale > 0.0F;
}

}  // namespace

extern "C" {

uint32_t vellum_host_abi_version(void) {
    return VELLUM_HOST_ABI_VERSION;
}

const char* vellum_host_last_error(void) {
    return last_error.c_str();
}

vellum_host_status vellum_host_content_floor(
    const uint8_t* rgba, size_t byte_count, uint32_t width, uint32_t height,
    vellum_host_content_stats* stats) {
    return guarded([&] {
        if (const auto status = require_sized(stats, "stats")) return status;
        const auto expected = vellum::graphics::checked_image_byte_count(width, height, 4);
        if (rgba == nullptr || !expected || *expected == 0 || *expected != byte_count) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                "rgba must hold exactly width * height * 4 bytes");
        }
        const auto result = vellum::graphics::analyze_capture_rgba(
            std::span<const std::uint8_t>(rgba, byte_count), width, height);
        stats->passes_content_floor = vellum::graphics::passes_content_floor(result) ? 1U : 0U;
        stats->pixel_count = result.pixel_count;
        stats->unique_colors = result.unique_colors;
        stats->opaque_pixels = result.opaque_pixels;
        stats->non_background_pixels = result.non_background_pixels;
        stats->luminance_standard_deviation = result.luminance_standard_deviation;
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_surface_create(
    const vellum_host_surface_config* config, vellum_host_surface** surface) {
    return guarded([&] {
        if (surface == nullptr) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "surface output is null");
        }
        *surface = nullptr;
        if (const auto status = require_sized(config, "config")) return status;
        if (config->width == 0 || config->height == 0 || !valid_scale(config->scale)) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                "surface width, height and scale must be positive");
        }
        std::string error;
        if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
            return native_failure(VELLUM_HOST_ERROR_UNAVAILABLE, error,
                "Dawn bootstrap registration failed");
        }
        vellum::graphics::SkiaDawnSurface::Config native{};
        native.width = config->width;
        native.height = config->height;
        native.scale = config->scale;
        native.native_surface_handle = config->metal_layer;
        native.vsync = config->vsync != 0;
        if (config->font_directory != nullptr) native.font_directory = config->font_directory;
        auto created = vellum::graphics::SkiaDawnSurface::create(native, &error);
        if (!created) {
            return native_failure(VELLUM_HOST_ERROR_UNAVAILABLE, error,
                "GPU surface creation failed");
        }
        const auto& evidence = created->evidence();
        const bool native_requested = config->metal_layer != nullptr;
        if (!evidence.available || evidence.fallback ||
            evidence.native_surface != native_requested) {
            return fail(VELLUM_HOST_ERROR_UNAVAILABLE,
                "GPU surface does not match the requested target without fallback");
        }
        auto handle = std::make_unique<vellum_host_surface>();
        handle->surface = std::move(created);
        handle->owner = std::this_thread::get_id();
        *surface = handle.release();
        return VELLUM_HOST_OK;
    });
}

void vellum_host_surface_destroy(vellum_host_surface* surface) {
    delete surface;
}

vellum_host_status vellum_host_surface_info_get(
    const vellum_host_surface* surface, vellum_host_surface_info* info) {
    return guarded([&] {
        if (const auto status = require_owner(surface, "surface")) return status;
        if (const auto status = require_sized(info, "info")) return status;
        const auto& evidence = surface->surface->evidence();
        info->available = evidence.available ? 1U : 0U;
        info->native_surface = evidence.native_surface ? 1U : 0U;
        info->fallback = evidence.fallback ? 1U : 0U;
        info->renderer = evidence.renderer.c_str();
        info->backend = evidence.backend.c_str();
        info->adapter = evidence.adapter.c_str();
        info->texture_format = evidence.texture_format.c_str();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_surface_resize(
    vellum_host_surface* surface, uint32_t width, uint32_t height, float scale) {
    return guarded([&] {
        if (const auto status = require_owner(surface, "surface")) return status;
        if (width == 0 || height == 0 || !valid_scale(scale)) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                "surface width, height and scale must be positive");
        }
        std::string error;
        if (!surface->surface->resize(width, height, scale, &error)) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "resize failed");
        }
        surface->capture.clear();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_surface_render(
    vellum_host_surface* surface, const vellum_host_app* app) {
    return guarded([&] {
        if (const auto status = require_owner(surface, "surface")) return status;
        if (const auto status = require_owner(app, "app")) return status;
        std::string error;
        if (!surface->surface->render(app->rendered.scene, &error)) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "render failed");
        }
        surface->capture.clear();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_surface_capture_rgba(
    vellum_host_surface* surface, vellum_host_image* image) {
    return guarded([&] {
        if (const auto status = require_owner(surface, "surface")) return status;
        if (const auto status = require_sized(image, "image")) return status;
        std::uint32_t width = 0;
        std::uint32_t height = 0;
        std::string error;
        surface->capture.clear();
        if (!surface->surface->capture_rgba(surface->capture, width, height, &error)) {
            surface->capture.clear();
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "capture failed");
        }
        image->width = width;
        image->height = height;
        image->row_bytes = width * 4U;
        image->pixels = surface->capture.data();
        image->byte_count = surface->capture.size();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_create(
    const vellum_host_app_config* config, vellum_host_app** app) {
    return guarded([&] {
        if (app == nullptr) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "app output is null");
        }
        *app = nullptr;
        if (const auto status = require_sized(config, "config")) return status;
        if (config->bundle == nullptr || config->bundle_length == 0) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "bundle is empty");
        }
        auto handle = std::make_unique<vellum_host_app>();
        handle->owner = std::this_thread::get_id();
        std::string error;
        handle->application = vellum::authoring::JsApplication::create(
            std::string_view(config->bundle, config->bundle_length), &error);
        if (!handle->application) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "bundle failed to load");
        }
        if (!handle->application->render(handle->rendered, &error)) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "initial render failed");
        }
        vellum::authoring::PumpResult settled;
        const std::size_t tasks =
            config->settle_max_tasks == 0 ? kDefaultSettleTasks : config->settle_max_tasks;
        if (!handle->application->wait_for_idle(tasks, handle->rendered, settled, &error)) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "initial settle failed");
        }
        *app = handle.release();
        return VELLUM_HOST_OK;
    });
}

void vellum_host_app_destroy(vellum_host_app* app) {
    delete app;
}

vellum_host_status vellum_host_app_viewport(
    const vellum_host_app* app, float* width, float* height) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (width == nullptr || height == nullptr) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "viewport outputs are null");
        }
        *width = app->rendered.scene.width;
        *height = app->rendered.scene.height;
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_pump(
    vellum_host_app* app, uint64_t advance_milliseconds, uint32_t max_tasks,
    vellum_host_pump_result* result) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (result != nullptr && !sized(result)) {
            return fail(VELLUM_HOST_ERROR_VERSION,
                "result struct_size is smaller than ABI v1 requires");
        }
        vellum::authoring::PumpResult pumped;
        std::string error;
        if (!app->application->pump(advance_milliseconds, max_tasks, app->rendered, pumped,
                                    &error)) {
            return native_failure(VELLUM_HOST_ERROR_FAILED, error, "pump failed");
        }
        if (!app->focused.empty() && find_input(*app, app->focused) == nullptr) {
            app->focused.clear();
        }
        if (result != nullptr) {
            result->rendered = pumped.rendered ? 1U : 0U;
            result->idle = pumped.idle ? 1U : 0U;
            result->tasks_executed = pumped.tasks_executed;
        }
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_dispatch(
    vellum_host_app* app, const char* action, const char* payload_json) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (action == nullptr || *action == '\0') {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "action is empty");
        }
        return dispatch(*app, action, payload_json == nullptr ? "null" : payload_json);
    });
}

vellum_host_status vellum_host_app_pointer(
    vellum_host_app* app, const vellum_host_pointer_event* event, uint32_t* outcome) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (const auto status = require_sized(event, "event")) return status;
        if (outcome != nullptr) *outcome = VELLUM_HOST_EVENT_IGNORED;
        if (!std::isfinite(event->x) || !std::isfinite(event->y)) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "pointer position is not finite");
        }
        if (event->phase != VELLUM_HOST_POINTER_DOWN) return VELLUM_HOST_OK;
        const auto& inputs = app->rendered.text_inputs;
        const auto input = std::find_if(inputs.rbegin(), inputs.rend(),
            [event](const TextInputControl& item) {
                return contains(item.bounds, event->x, event->y);
            });
        if (input != inputs.rend()) {
            app->focused = input->node_id;
            if (outcome != nullptr) *outcome = VELLUM_HOST_EVENT_FOCUSED;
            return VELLUM_HOST_OK;
        }
        const bool had_focus = !app->focused.empty();
        app->focused.clear();
        const auto& interactions = app->rendered.interactions;
        const auto target = std::find_if(interactions.rbegin(), interactions.rend(),
            [event](const Interaction& item) {
                return item.event == "press" && contains(item.bounds, event->x, event->y);
            });
        if (target == interactions.rend()) {
            if (outcome != nullptr && had_focus) *outcome = VELLUM_HOST_EVENT_UNFOCUSED;
            return VELLUM_HOST_OK;
        }
        const std::string action = target->action;
        const auto status = dispatch(*app, action,
            std::string("{\"pointerType\":") + json_string(pointer_type(event->device)) + "}");
        if (status == VELLUM_HOST_OK && outcome != nullptr) {
            *outcome = VELLUM_HOST_EVENT_DISPATCHED;
        }
        return status;
    });
}

vellum_host_status vellum_host_app_focus(vellum_host_app* app, const char* node_id) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (node_id == nullptr || *node_id == '\0') {
            app->focused.clear();
            return VELLUM_HOST_OK;
        }
        if (find_input(*app, node_id) == nullptr) {
            return fail(VELLUM_HOST_ERROR_NOT_FOUND,
                std::string("no text input with id ") + node_id);
        }
        app->focused = node_id;
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_key(
    vellum_host_app* app, const vellum_host_key_event* event, uint32_t* outcome) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (const auto status = require_sized(event, "event")) return status;
        if (outcome != nullptr) *outcome = VELLUM_HOST_EVENT_IGNORED;
        if (event->key == nullptr) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "key is null");
        }
        const std::string_view key(event->key);
        if (key.empty() || key.size() > kMaximumKeyBytes || !utf16_from_utf8(key)) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                "key must be non-empty UTF-8 of at most 64 bytes");
        }
        if (app->focused.empty()) return VELLUM_HOST_OK;
        const std::string focused = app->focused;
        const TextInputControl* input = find_input(*app, focused);
        if (input == nullptr) return VELLUM_HOST_OK;
        bool dispatched = false;
        if (!input->key_down_action.empty()) {
            const std::string action = input->key_down_action;
            const auto status = dispatch(*app, action,
                "{\"key\":" + json_string(key) + ",\"repeat\":" +
                (event->repeat != 0 ? "true" : "false") + ",\"source\":\"host\"}");
            if (status != VELLUM_HOST_OK) return status;
            dispatched = true;
            input = find_input(*app, focused);
            if (input == nullptr) {
                if (outcome != nullptr) *outcome = VELLUM_HOST_EVENT_DISPATCHED;
                return VELLUM_HOST_OK;
            }
        }
        if (key == "Enter" && !input->submit_action.empty()) {
            const std::string action = input->submit_action;
            const auto status = dispatch(*app, action,
                "{\"value\":" + json_string(input->value) + "}");
            if (status != VELLUM_HOST_OK) return status;
            dispatched = true;
        } else if (key == "Backspace") {
            const auto status = replace_text(*app, *input, u"", true,
                                             "deleteContentBackward");
            if (status != VELLUM_HOST_OK) return status;
            dispatched = true;
        }
        if (dispatched && outcome != nullptr) *outcome = VELLUM_HOST_EVENT_DISPATCHED;
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_insert_text(
    vellum_host_app* app, const char* utf8, uint32_t* outcome) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (outcome != nullptr) *outcome = VELLUM_HOST_EVENT_IGNORED;
        if (utf8 == nullptr) return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "text is null");
        const std::string_view text(utf8);
        const auto inserted = utf16_from_utf8(text);
        if (!inserted || text.size() > kMaximumTextBytes) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT,
                "text must be valid UTF-8 of at most 64 KiB");
        }
        if (app->focused.empty()) return VELLUM_HOST_OK;
        const TextInputControl* input = find_input(*app, app->focused);
        if (input == nullptr) return VELLUM_HOST_OK;
        const auto status = replace_text(*app, *input, *inserted, false, "insertText");
        if (status == VELLUM_HOST_OK && outcome != nullptr) {
            *outcome = VELLUM_HOST_EVENT_DISPATCHED;
        }
        return status;
    });
}

vellum_host_status vellum_host_app_accessibility_count(
    const vellum_host_app* app, size_t* count) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (count == nullptr) return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "count is null");
        *count = app->rendered.accessibility_nodes.size();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_accessibility_node(
    const vellum_host_app* app, size_t index, vellum_host_accessibility_node* node) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (const auto status = require_sized(node, "node")) return status;
        if (index >= app->rendered.accessibility_nodes.size()) {
            return fail(VELLUM_HOST_ERROR_OUT_OF_RANGE, "accessibility index is out of range");
        }
        const AccessibilityNode& source = app->rendered.accessibility_nodes[index];
        node->state = state_flags(source.state);
        node->node_id = source.node_id.c_str();
        node->role = source.role.c_str();
        node->label = source.label.c_str();
        node->value = source.value.c_str();
        node->action_count = source.actions.size();
        node->bounds = rect(source.bounds);
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_accessibility_action(
    const vellum_host_app* app, size_t node_index, size_t action_index,
    const char** action) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (action == nullptr) return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "action is null");
        const auto& nodes = app->rendered.accessibility_nodes;
        if (node_index >= nodes.size() || action_index >= nodes[node_index].actions.size()) {
            return fail(VELLUM_HOST_ERROR_OUT_OF_RANGE, "accessibility action is out of range");
        }
        *action = nodes[node_index].actions[action_index].c_str();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_accessibility_perform(
    vellum_host_app* app, const char* node_id, const char* action) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (node_id == nullptr || action == nullptr) {
            return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "node id and action are required");
        }
        const std::string_view id(node_id);
        const std::string_view event(action);
        const auto& interactions = app->rendered.interactions;
        const auto target = std::find_if(interactions.begin(), interactions.end(),
            [id, event](const Interaction& item) {
                return item.node_id == id && item.event == event;
            });
        if (target == interactions.end()) {
            return fail(VELLUM_HOST_ERROR_NOT_FOUND,
                "node " + std::string(id) + " has no " + std::string(event) + " action");
        }
        const std::string mapped = target->action;
        return dispatch(*app, mapped, "{\"pointerType\":\"accessibility\"}");
    });
}

vellum_host_status vellum_host_app_text_input_count(
    const vellum_host_app* app, size_t* count) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (count == nullptr) return fail(VELLUM_HOST_ERROR_INVALID_ARGUMENT, "count is null");
        *count = app->rendered.text_inputs.size();
        return VELLUM_HOST_OK;
    });
}

vellum_host_status vellum_host_app_text_input(
    const vellum_host_app* app, size_t index, vellum_host_text_input* input) {
    return guarded([&] {
        if (const auto status = require_owner(app, "app")) return status;
        if (const auto status = require_sized(input, "input")) return status;
        if (index >= app->rendered.text_inputs.size()) {
            return fail(VELLUM_HOST_ERROR_OUT_OF_RANGE, "text input index is out of range");
        }
        const TextInputControl& source = app->rendered.text_inputs[index];
        input->focused = source.node_id == app->focused ? 1U : 0U;
        input->node_id = source.node_id.c_str();
        input->value = source.value.c_str();
        input->placeholder = source.placeholder.c_str();
        input->selection_start = source.selection_start;
        input->selection_end = source.selection_end;
        input->bounds = rect(source.bounds);
        return VELLUM_HOST_OK;
    });
}

}
