#include "component_registry.hpp"

#include <vellum/graphics/scene_acceptance.hpp>

#include <iostream>
#include <string>

namespace {

using vellum::graphics::EffectKind;
using vellum::graphics::LoweringDiagnostic;
using vellum::graphics::MaterialDefinition;
using vellum::graphics::MaterialRegistry;
using vellum::graphics::MaterialValueType;
using vellum::graphics::SceneNode;

MaterialDefinition sheen() {
    return {
        .id = "com.example.sheen",
        .provenance = {.owner = "com.example"},
        .uniforms = {{.name = "angle",
                      .type = MaterialValueType::number,
                      .default_value = {45.0F},
                      .minimum = 0.0F,
                      .maximum = 360.0F}},
        .component = "sheen",
        .passes = 1U,
    };
}

vellum::graphics::Scene material_scene() {
    vellum::graphics::Scene scene;
    scene.width = 100.0F;
    scene.height = 40.0F;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, 100.0F, 40.0F}};
    SceneNode card{.id = "card", .kind = SceneNode::Kind::rectangle,
                   .bounds = {10.0F, 5.0F, 80.0F, 30.0F},
                   .fill = vellum::graphics::Color::hex(0x334155)};
    card.effects = {{.kind = EffectKind::material,
                     .material = "com.example.sheen",
                     .values = {{.name = "angle", .value = {180.0F}}}}};
    card.children.push_back({.id = "card/label", .kind = SceneNode::Kind::rectangle,
                             .bounds = {4.0F, 4.0F, 10.0F, 10.0F}});
    scene.root.children.push_back(std::move(card));
    return scene;
}

bool application_materials(const char* module) {
    std::string error;
    {
        vellum::app_host::ComponentRegistry registry;
        if (registry.register_material(sheen(), &error) ||
            error.find("not loaded") == std::string::npos) {
            std::cerr << "a material whose component is not loaded registered: " << error << '\n';
            return false;
        }
        if (!registry.load({.component_id = "sheen", .path = module}, &error) ||
            !registry.register_material(sheen(), &error)) {
            std::cerr << "the sheen material did not register: " << error << '\n';
            return false;
        }
        const auto registered = MaterialRegistry::shared().find("com.example.sheen");
        if (!registered ||
            registered->disposition != LoweringDiagnostic::Disposition::extension ||
            registered->provenance.source.find("sheen") == std::string::npos) {
            std::cerr << "the material is not an extension with its module as source\n";
            return false;
        }
        auto scene = material_scene();
        const auto before = vellum::graphics::accept_scene(scene);
        if (!before.accepted || before.diagnostics.size() != 1U ||
            before.diagnostics[0].disposition != LoweringDiagnostic::Disposition::extension) {
            std::cerr << "a scene naming the material was not visibly non-portable\n";
            return false;
        }
        if (!registry.expand(scene, &error)) {
            std::cerr << "the material did not expand: " << error << '\n';
            return false;
        }
        const auto& card = scene.root.children[0];
        if (!card.effects.empty() || card.children.size() != 2U ||
            card.children[0].id != "card/material/com.example.sheen" ||
            card.children[0].children.size() != 1U ||
            card.children[0].children[0].fill.alpha != 0.5F ||
            card.children[0].children[0].bounds.width != 80.0F ||
            card.children[1].id != "card/label") {
            std::cerr << "the component's paint did not replace the material effect\n";
            return false;
        }
        if (!vellum::graphics::accept_scene(scene).diagnostics.empty()) {
            std::cerr << "an expanded scene still reports the material\n";
            return false;
        }
    }
    if (MaterialRegistry::shared().find("com.example.sheen") != nullptr) {
        std::cerr << "the material outlived the registry that loaded its component\n";
        return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) return 2;
    if (!application_materials(argv[3])) return 1;
    vellum::app_host::ComponentRegistry registry;
    std::string error;
    if (registry.load({.component_id = "level-meter", .path = argv[1]}, &error) ||
        error.find("descriptor") == std::string::npos) {
        std::cerr << "unterminated descriptor id was not rejected: " << error << '\n';
        return 1;
    }
    if (!registry.load({.component_id = "level-meter", .path = argv[2]}, &error)) {
        std::cerr << "bounded text fixture did not load: " << error << '\n';
        return 1;
    }
    vellum::graphics::Scene scene;
    scene.root = {
        .id = "root",
        .kind = vellum::graphics::SceneNode::Kind::custom,
        .bounds = {0.0F, 0.0F, 100.0F, 20.0F},
        .custom_component = "level-meter",
        .custom_properties_json = "{}",
    };
    error.clear();
    if (registry.expand(scene, &error) || error.find("unsupported") == std::string::npos) {
        std::cerr << "unterminated text was not rejected: " << error << '\n';
        return 1;
    }
    return 0;
}
