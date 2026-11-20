// Installed consumer smoke test for the V8 plus Three.js SDK surface.

#include <vellum/view/script_engine.hpp>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <stdexcept>

namespace {
std::optional<std::string> read_file(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) return std::nullopt;
    std::ostringstream bytes;
    bytes << input.rdbuf();
    return bytes.str();
}
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::filesystem::path root = argv[1];
    try {
        if (!std::filesystem::is_regular_file(root / "build/three.webgpu.js") ||
            !std::filesystem::is_regular_file(root / "build/three.core.js"))
            throw std::runtime_error("installed Three.js payload is incomplete");
        vellum::view::ScriptEngine engine(vellum::view::JsEngineType::v8);
        bool completed = false;
        std::string error;
        engine.run_module(
            "import * as THREE from 'three'; globalThis.__scene = new THREE.Scene();"
            "globalThis.__mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));"
            "__scene.add(__mesh);",
            [&](std::string_view specifier) -> std::optional<std::string> {
                if (specifier == "three") return read_file(root / "build/three.module.js");
                if (specifier.ends_with("three.core.js")) return read_file(root / "build/three.core.js");
                return std::nullopt;
            },
            [&](const std::string& message, const choc::value::Value&) {
                error = message;
                completed = true;
            });
        for (int i = 0; i < 128 && !completed; ++i) engine.pump_message_loop();
        if (!completed) throw std::runtime_error("Three.js module did not complete");
        if (!error.empty()) throw std::runtime_error(error);
        if (engine.evaluate("String(__scene.children.length);").toString() != "1")
            throw std::runtime_error("Three.js scene did not contain one mesh");
        std::cout << "installed Three.js module and scene: ok\n";
    } catch (const std::exception& error) {
        std::cerr << "installed Three.js consumer: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
