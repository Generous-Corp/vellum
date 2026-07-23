#pragma once

#include <vellum/graphics/scene.hpp>

#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::app_host {

struct ComponentModuleSpec final {
    std::string component_id;
    std::filesystem::path path;
};

class ComponentRegistry final {
public:
    ComponentRegistry();
    ~ComponentRegistry();
    ComponentRegistry(ComponentRegistry&&) noexcept;
    ComponentRegistry& operator=(ComponentRegistry&&) noexcept;
    ComponentRegistry(const ComponentRegistry&) = delete;
    ComponentRegistry& operator=(const ComponentRegistry&) = delete;

    [[nodiscard]] bool load(
        const ComponentModuleSpec& spec, std::string* error = nullptr);
    [[nodiscard]] bool expand(
        vellum::graphics::Scene& scene, std::string* error = nullptr) const;
    [[nodiscard]] std::size_t size() const noexcept;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

[[nodiscard]] bool parse_component_module_spec(
    std::string_view value, ComponentModuleSpec& output,
    std::string* error = nullptr);

}  // namespace vellum::app_host
