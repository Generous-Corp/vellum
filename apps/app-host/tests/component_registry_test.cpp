#include "component_registry.hpp"

#include <vellum/graphics/scene_acceptance.hpp>

#include <clocale>
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
            card.children[0].children.size() != 2U ||
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

        // Nodes that cannot take the material keep it for acceptance.
        auto masked = material_scene();
        masked.root.children[0].mask = vellum::graphics::MaskMode::alpha;
        masked.root.children.push_back({.id = "masked-sibling",
                                        .kind = SceneNode::Kind::rectangle,
                                        .bounds = {0.0F, 0.0F, 4.0F, 4.0F}});
        auto old = material_scene();
        old.scene_version = 5U;
        auto image = material_scene();
        image.root.children[0].kind = SceneNode::Kind::image;
        image.root.children[0].children.clear();
        image.root.children[0].image = vellum::graphics::ImageReference{
            .content_hash = "sha256:" + std::string(64, '0')};
        for (auto* kept : {&masked, &old, &image}) {
            if (!registry.expand(*kept, &error) || kept->root.children[0].effects.size() != 1U) {
                std::cerr << "a node that cannot take the material lost its effect\n";
                return false;
            }
        }
        if (vellum::graphics::accept_scene(masked).accepted ||
            vellum::graphics::accept_scene(old).accepted ||
            !image.root.children[0].children.empty() ||
            !vellum::graphics::accept_scene(image).accepted) {
            std::cerr << "a masked or old scene was accepted, or the image was given children\n";
            return false;
        }

        // The properties are JSON in every locale.
        if (std::setlocale(LC_NUMERIC, "de_DE.UTF-8") != nullptr) {
            auto fractional = material_scene();
            fractional.root.children[0].effects[0].values[0].value[0] = 22.5F;
            const bool expanded = registry.expand(fractional, &error);
            std::setlocale(LC_NUMERIC, "C");
            const auto& echoed = fractional.root.children[0].children[0].children[1].text;
            if (!expanded ||
                echoed != "{\"material\":\"com.example.sheen\",\"values\":{\"angle\":22.5}}") {
                std::cerr << "the material properties were not locale independent: " << echoed
                          << '\n';
                return false;
            }
        } else {
            std::cout << "the de_DE.UTF-8 locale is not installed; the locale check did not run\n";
        }

        // A registry removes only the material it registered.
        if (!MaterialRegistry::shared().unregister_extension("com.example.sheen")) return false;
        vellum::app_host::ComponentRegistry other;
        if (!other.load({.component_id = "sheen", .path = module}, &error) ||
            !other.register_material(sheen(), &error)) {
            std::cerr << "a second registry could not register the material: " << error << '\n';
            return false;
        }
        const auto replacement = MaterialRegistry::shared().find("com.example.sheen");
        {
            vellum::app_host::ComponentRegistry stale = std::move(registry);
        }
        if (MaterialRegistry::shared().find("com.example.sheen") != replacement) {
            std::cerr << "destroying the first registry removed the second one's material\n";
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
