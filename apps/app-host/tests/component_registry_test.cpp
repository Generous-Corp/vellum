#include "component_registry.hpp"

#include <iostream>
#include <string>

int main(int argc, char** argv) {
    if (argc != 3) return 2;
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
