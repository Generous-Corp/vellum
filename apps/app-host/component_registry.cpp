#include "component_registry.hpp"

#include <vellum/components/abi.h>

#include <dlfcn.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <map>
#include <optional>
#include <set>
#include <utility>

namespace vellum::app_host {
namespace {

constexpr std::size_t kMaximumCommandsPerComponent = 4096U;
constexpr std::size_t kMaximumTextBytes = 1024U * 1024U;

void set_error(std::string* destination, std::string value) {
    if (destination != nullptr) *destination = std::move(value);
}

bool component_id(std::string_view value) {
    if (value.empty() || value.size() > 64U || value.front() < 'a' || value.front() > 'z') {
        return false;
    }
    return std::all_of(value.begin() + 1, value.end(), [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= '0' && character <= '9') || character == '-';
    });
}

std::optional<std::string_view> bounded_string(const char* value, std::size_t maximum) {
    if (value == nullptr) return std::nullopt;
    const std::size_t length = ::strnlen(value, maximum + 1U);
    if (length > maximum) return std::nullopt;
    return std::string_view(value, length);
}

bool suffix_id(std::string_view value) {
    return !value.empty() && value.size() <= 128U &&
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

struct EmitState final {
    std::string node_id;
    std::set<std::string> suffixes;
    std::vector<vellum::graphics::SceneNode> children;
    std::string error;
};

int emit_command(void* user_data, const vellum_component_paint_command_v1* command) noexcept {
    auto* state = static_cast<EmitState*>(user_data);
    if (state == nullptr || command == nullptr) return 0;
    try {
        const auto suffix = bounded_string(command->id_suffix, 128U);
        if (command->struct_size != sizeof(vellum_component_paint_command_v1) ||
            !suffix || !suffix_id(*suffix) ||
            !state->suffixes.insert(std::string(*suffix)).second ||
            state->children.size() >= kMaximumCommandsPerComponent ||
            !finite_rect(command->bounds) || !finite_color(command->fill) ||
            !std::isfinite(command->corner_radius) || command->corner_radius < 0.0F) {
            state->error = "custom component emitted a malformed or duplicate paint command";
            return 0;
        }
        vellum::graphics::SceneNode node;
        node.id = state->node_id + "/custom/" + std::string(*suffix);
        node.bounds = {
            command->bounds.x, command->bounds.y,
            command->bounds.width, command->bounds.height,
        };
        node.fill = vellum::graphics::Color::rgba(
            command->fill.red, command->fill.green,
            command->fill.blue, command->fill.alpha);
        if (command->kind == VELLUM_COMPONENT_PAINT_RECTANGLE_V1) {
            node.kind = vellum::graphics::SceneNode::Kind::rectangle;
            node.corner_radius = command->corner_radius;
        } else if (command->kind == VELLUM_COMPONENT_PAINT_TEXT_V1) {
            const auto text = bounded_string(command->text, kMaximumTextBytes);
            if (!text || !std::isfinite(command->font_size) ||
                command->font_size < 1.0F || command->font_size > 1024.0F) {
                state->error = "custom component emitted an unsupported paint command";
                return 0;
            }
            node.kind = vellum::graphics::SceneNode::Kind::text;
            node.text.assign(text->data(), text->size());
            node.font_size = command->font_size;
        } else {
            state->error = "custom component emitted an unsupported paint command";
            return 0;
        }
        state->children.push_back(std::move(node));
        return 1;
    } catch (...) {
        state->error = "custom component paint command could not be copied";
        return 0;
    }
}

}  // namespace

class ComponentRegistry::Impl final {
public:
    ~Impl() {
        for (auto iterator = modules_.rbegin(); iterator != modules_.rend(); ++iterator) {
            if (iterator->handle != nullptr) dlclose(iterator->handle);
        }
    }

    bool load(const ComponentModuleSpec& spec, std::string* error) {
        if (!component_id(spec.component_id) || modules_by_id_.contains(spec.component_id)) {
            set_error(error, "custom component identifier is invalid or duplicated: " +
                             spec.component_id);
            return false;
        }
        std::error_code filesystem_error;
        const auto path = std::filesystem::weakly_canonical(spec.path, filesystem_error);
        if (filesystem_error || !std::filesystem::is_regular_file(path)) {
            set_error(error, "custom component module is not a regular file: " +
                             spec.path.string());
            return false;
        }
        void* handle = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (handle == nullptr) {
            const char* load_error = dlerror();
            set_error(error, "could not load custom component module: " +
                             std::string(load_error == nullptr ? "unknown dlopen error" : load_error));
            return false;
        }
        dlerror();
        auto entry = reinterpret_cast<const vellum_component_descriptor_v1* (*)()>(
            dlsym(handle, VELLUM_COMPONENT_ENTRY_SYMBOL));
        const char* symbol_error = dlerror();
        if (symbol_error != nullptr || entry == nullptr) {
            set_error(error, "custom component module has no "
                             VELLUM_COMPONENT_ENTRY_SYMBOL " entry point");
            dlclose(handle);
            return false;
        }
        const vellum_component_descriptor_v1* descriptor = nullptr;
        try {
            descriptor = entry();
        } catch (...) {
            set_error(error, "custom component entry point threw an exception");
            dlclose(handle);
            return false;
        }
        const auto descriptor_id = descriptor == nullptr
            ? std::nullopt : bounded_string(descriptor->component_id, 64U);
        if (descriptor == nullptr || descriptor->struct_size != sizeof(*descriptor) ||
            descriptor->abi_version != VELLUM_COMPONENT_ABI_VERSION ||
            !descriptor_id || descriptor->render == nullptr ||
            *descriptor_id != spec.component_id) {
            set_error(error, "custom component descriptor does not match its declaration or ABI");
            dlclose(handle);
            return false;
        }
        modules_.push_back({handle, path, descriptor});
        modules_by_id_.emplace(spec.component_id, descriptor);
        return true;
    }

    bool expand(vellum::graphics::Scene& scene, std::string* error) const {
        return expand_node(scene.root, error);
    }

    std::size_t size() const noexcept { return modules_.size(); }

private:
    struct Module final {
        void* handle = nullptr;
        std::filesystem::path path;
        const vellum_component_descriptor_v1* descriptor = nullptr;
    };

    bool expand_node(vellum::graphics::SceneNode& node, std::string* error) const {
        if (node.kind == vellum::graphics::SceneNode::Kind::custom) {
            const auto found = modules_by_id_.find(node.custom_component);
            if (found == modules_by_id_.end()) {
                set_error(error, "custom component is used but not declared and loaded: " +
                                 node.custom_component);
                return false;
            }
            EmitState emitted{.node_id = node.id};
            const vellum_component_render_context_v1 context{
                .struct_size = sizeof(vellum_component_render_context_v1),
                .abi_version = VELLUM_COMPONENT_ABI_VERSION,
                .component_id = node.custom_component.c_str(),
                .node_id = node.id.c_str(),
                .properties_json = node.custom_properties_json.c_str(),
                .bounds = {0.0F, 0.0F, node.bounds.width, node.bounds.height},
                .emit_user_data = &emitted,
                .emit = emit_command,
            };
            int rendered = 0;
            try {
                rendered = found->second->render(&context);
            } catch (...) {
                set_error(error, "custom component render callback threw an exception: " +
                                 node.custom_component);
                return false;
            }
            if (rendered != 1 || !emitted.error.empty() || emitted.children.empty()) {
                set_error(error, emitted.error.empty()
                    ? "custom component returned failure or emitted no paint commands: " +
                          node.custom_component
                    : emitted.error);
                return false;
            }
            node.kind = vellum::graphics::SceneNode::Kind::group;
            node.children = std::move(emitted.children);
            node.custom_component.clear();
            node.custom_properties_json.clear();
        }
        for (auto& child : node.children) {
            if (!expand_node(child, error)) return false;
        }
        return true;
    }

    std::vector<Module> modules_;
    std::map<std::string, const vellum_component_descriptor_v1*> modules_by_id_;
};

ComponentRegistry::ComponentRegistry() : impl_(std::make_unique<Impl>()) {}
ComponentRegistry::~ComponentRegistry() = default;
ComponentRegistry::ComponentRegistry(ComponentRegistry&&) noexcept = default;
ComponentRegistry& ComponentRegistry::operator=(ComponentRegistry&&) noexcept = default;

bool ComponentRegistry::load(const ComponentModuleSpec& spec, std::string* error) {
    return impl_->load(spec, error);
}

bool ComponentRegistry::expand(vellum::graphics::Scene& scene, std::string* error) const {
    return impl_->expand(scene, error);
}

std::size_t ComponentRegistry::size() const noexcept { return impl_->size(); }

bool parse_component_module_spec(
    std::string_view value, ComponentModuleSpec& output, std::string* error) {
    const auto separator = value.find('=');
    if (separator == std::string_view::npos || separator == 0U ||
        separator + 1U >= value.size()) {
        set_error(error, "--component requires COMPONENT_ID=MODULE_PATH");
        return false;
    }
    const std::string id(value.substr(0U, separator));
    if (!component_id(id)) {
        set_error(error, "--component has an invalid component identifier");
        return false;
    }
    output = {.component_id = id,
              .path = std::filesystem::path(value.substr(separator + 1U))};
    return true;
}

}  // namespace vellum::app_host
