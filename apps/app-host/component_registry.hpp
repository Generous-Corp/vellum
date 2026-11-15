#pragma once

#include <vellum/graphics/effect.hpp>
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
    /// Registers an application material drawn by a loaded component, named
    /// by `definition.component`, in `MaterialRegistry::shared()` with the
    /// `extension` disposition. Without a source, the provenance records the
    /// component's module path. The material is unregistered when this
    /// registry is destroyed, since its component unloads with it.
    [[nodiscard]] bool register_material(
        vellum::graphics::MaterialDefinition definition, std::string* error = nullptr);
    /// Expands every custom node into the paint its component emits, and every
    /// material effect a component of this registry draws into a group of the
    /// component's paint, placed first among the node's children so it lies
    /// over the node's own paint and under its subtree, and removes the effect.
    /// The component receives `{"material":<id>,"values":{...}}` as its
    /// properties, with each uniform as a number, a two-number array or a
    /// red, green, blue and alpha object. A scene before version 10, a mask
    /// node and an image node keep their material effects: acceptance refuses
    /// the first two, and the renderer draws the image without the material.
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
