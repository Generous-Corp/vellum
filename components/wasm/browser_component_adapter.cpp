#include <vellum/components/abi.h>

#include <emscripten/emscripten.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <cstdint>
#include <iterator>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::size_t kMaximumCommands = 4096U;
constexpr std::size_t kMaximumSuffixBytes = 128U;
constexpr std::size_t kMaximumTextBytes = 1024U * 1024U;

struct StoredCommand final {
    std::uint32_t kind = 0U;
    std::string suffix;
    vellum_component_rect_v1 bounds{};
    vellum_component_color_v1 fill{};
    float corner_radius = 0.0F;
    std::string text;
    float font_size = 0.0F;
};

const vellum_component_descriptor_v1* descriptor = nullptr;
std::vector<StoredCommand> commands;
std::string error_message;

bool component_id(std::string_view value) {
    if (value.empty() || value.size() > 64U || value.front() < 'a' || value.front() > 'z') {
        return false;
    }
    return std::all_of(value.begin() + 1, value.end(), [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= '0' && character <= '9') || character == '-';
    });
}

bool suffix_id(std::string_view value) {
    return !value.empty() && value.size() <= kMaximumSuffixBytes &&
           std::all_of(value.begin(), value.end(), [](char character) {
               return (character >= 'a' && character <= 'z') ||
                      (character >= 'A' && character <= 'Z') ||
                      (character >= '0' && character <= '9') ||
                      character == '-' || character == '_';
           });
}

bool finite_rect(const vellum_component_rect_v1& value) {
    return std::isfinite(value.x) && std::isfinite(value.y) &&
           std::isfinite(value.width) && std::isfinite(value.height) &&
           value.width >= 0.0F && value.height >= 0.0F &&
           std::abs(value.x) <= 1000000.0F && std::abs(value.y) <= 1000000.0F &&
           value.width <= 1000000.0F && value.height <= 1000000.0F;
}

bool finite_color(const vellum_component_color_v1& value) {
    const float channels[] = {value.red, value.green, value.blue, value.alpha};
    return std::all_of(std::begin(channels), std::end(channels), [](float channel) {
        return std::isfinite(channel) && channel >= 0.0F && channel <= 1.0F;
    });
}

bool bounded_string(const char* value, std::size_t maximum, std::string& output) {
    if (value == nullptr) return false;
    const std::size_t length = ::strnlen(value, maximum + 1U);
    if (length > maximum) return false;
    output.assign(value, length);
    return true;
}

int emit_command(
    void*, const vellum_component_paint_command_v1* command
) noexcept {
    try {
        StoredCommand stored;
        if (command == nullptr ||
            command->struct_size != sizeof(vellum_component_paint_command_v1) ||
            commands.size() >= kMaximumCommands ||
            !bounded_string(command->id_suffix, kMaximumSuffixBytes, stored.suffix) ||
            !suffix_id(stored.suffix) ||
            std::any_of(commands.begin(), commands.end(), [&](const StoredCommand& prior) {
                return prior.suffix == stored.suffix;
            }) ||
            !finite_rect(command->bounds) || !finite_color(command->fill) ||
            !std::isfinite(command->corner_radius) || command->corner_radius < 0.0F) {
            error_message = "custom component emitted a malformed or duplicate paint command";
            return 0;
        }
        stored.kind = command->kind;
        stored.bounds = command->bounds;
        stored.fill = command->fill;
        stored.corner_radius = command->corner_radius;
        if (command->kind == VELLUM_COMPONENT_PAINT_TEXT_V1) {
            if (!bounded_string(command->text, kMaximumTextBytes, stored.text) ||
                !std::isfinite(command->font_size) ||
                command->font_size < 1.0F || command->font_size > 1024.0F) {
                error_message = "custom component emitted an unsupported text command";
                return 0;
            }
            stored.font_size = command->font_size;
        } else if (command->kind != VELLUM_COMPONENT_PAINT_RECTANGLE_V1) {
            error_message = "custom component emitted an unsupported paint command";
            return 0;
        }
        commands.push_back(std::move(stored));
        return 1;
    } catch (...) {
        error_message = "custom component paint command could not be copied";
        return 0;
    }
}

const StoredCommand* command_at(int index) {
    return index >= 0 && static_cast<std::size_t>(index) < commands.size()
        ? &commands[static_cast<std::size_t>(index)] : nullptr;
}

}  // namespace

extern "C" {

EMSCRIPTEN_KEEPALIVE int vellum_component_web_start(const char* expected_id) {
    commands.clear();
    error_message.clear();
    descriptor = vellum_component_entry_v1();
    std::string actual_id;
    if (expected_id == nullptr || descriptor == nullptr ||
        descriptor->struct_size != sizeof(vellum_component_descriptor_v1) ||
        descriptor->abi_version != VELLUM_COMPONENT_ABI_VERSION ||
        descriptor->render == nullptr ||
        !bounded_string(descriptor->component_id, 64U, actual_id) ||
        !component_id(actual_id) || actual_id != expected_id) {
        descriptor = nullptr;
        error_message = "custom component descriptor does not match its declaration or ABI";
        return 0;
    }
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_component_web_render(
    const char* node_id, const char* properties_json, float width, float height
) {
    commands.clear();
    error_message.clear();
    if (descriptor == nullptr || node_id == nullptr || node_id[0] == '\0' ||
        properties_json == nullptr || !std::isfinite(width) || !std::isfinite(height) ||
        width < 0.0F || height < 0.0F) {
        error_message = "custom component render context is invalid";
        return 0;
    }
    const vellum_component_render_context_v1 context{
        .struct_size = sizeof(vellum_component_render_context_v1),
        .abi_version = VELLUM_COMPONENT_ABI_VERSION,
        .component_id = descriptor->component_id,
        .node_id = node_id,
        .properties_json = properties_json,
        .bounds = {0.0F, 0.0F, width, height},
        .emit_user_data = nullptr,
        .emit = emit_command,
    };
    int rendered = 0;
    try {
        rendered = descriptor->render(&context);
    } catch (...) {
        error_message = "custom component render callback threw an exception";
        return 0;
    }
    if (rendered != 1 || commands.empty() || !error_message.empty()) {
        if (error_message.empty()) {
            error_message = "custom component returned failure or emitted no paint commands";
        }
        commands.clear();
        return 0;
    }
    return 1;
}

EMSCRIPTEN_KEEPALIVE int vellum_component_web_command_count() {
    return static_cast<int>(commands.size());
}

EMSCRIPTEN_KEEPALIVE int vellum_component_web_command_kind(int index) {
    const auto* command = command_at(index);
    return command == nullptr ? 0 : static_cast<int>(command->kind);
}

EMSCRIPTEN_KEEPALIVE const char* vellum_component_web_command_suffix(int index) {
    const auto* command = command_at(index);
    return command == nullptr ? "" : command->suffix.c_str();
}

EMSCRIPTEN_KEEPALIVE float vellum_component_web_command_number(int index, int field) {
    const auto* command = command_at(index);
    if (command == nullptr) return 0.0F;
    switch (field) {
        case 0: return command->bounds.x;
        case 1: return command->bounds.y;
        case 2: return command->bounds.width;
        case 3: return command->bounds.height;
        case 4: return command->fill.red;
        case 5: return command->fill.green;
        case 6: return command->fill.blue;
        case 7: return command->fill.alpha;
        case 8: return command->corner_radius;
        case 9: return command->font_size;
        default: return 0.0F;
    }
}

EMSCRIPTEN_KEEPALIVE const char* vellum_component_web_command_text(int index) {
    const auto* command = command_at(index);
    return command == nullptr ? "" : command->text.c_str();
}

EMSCRIPTEN_KEEPALIVE const char* vellum_component_web_error() {
    return error_message.c_str();
}

}  // extern "C"
