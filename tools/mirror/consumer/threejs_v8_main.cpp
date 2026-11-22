#include <vellum/graphics/dawn_native_bootstrap.hpp>
#include <vellum/state/store.hpp>
#include <vellum/view/frame_clock.hpp>
#include <vellum/view/js_engine.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/theme.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/window_host.hpp>

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include <unistd.h>

using namespace vellum::view;

namespace {

std::string read_text_file(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in.good()) {
        throw std::runtime_error("Cannot read file: " + path.string());
    }
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

std::optional<std::string> resolve_threejs_module(std::string_view path) {
    const auto root = std::filesystem::path(VELLUM_THREEJS_ROOT);
    if (path == "three") {
        return read_text_file(root / "build" / "three.module.js");
    }
    if (path == "three/webgpu") {
        return read_text_file(root / "build" / "three.webgpu.js");
    }
    if (path == "./three.core.js" || path == "three.core.js") {
        return read_text_file(root / "build" / "three.core.js");
    }
    if (path == "three/addons/controls/OrbitControls.js") {
        return read_text_file(root / "examples" / "jsm" / "controls" / "OrbitControls.js");
    }
    if (path == "three/addons/loaders/GLTFLoader.js") {
        return read_text_file(root / "examples" / "jsm" / "loaders" / "GLTFLoader.js");
    }
    if (path == "../utils/BufferGeometryUtils.js" || path == "three/addons/utils/BufferGeometryUtils.js") {
        return read_text_file(root / "examples" / "jsm" / "utils" / "BufferGeometryUtils.js");
    }
    if (path == "../utils/SkeletonUtils.js" || path == "three/addons/utils/SkeletonUtils.js") {
        return read_text_file(root / "examples" / "jsm" / "utils" / "SkeletonUtils.js");
    }
    return std::nullopt;
}

int run_module_smoke() {
    try {
        ScriptEngine engine(JsEngineType::v8);
        bool completed = false;
        std::string error;
        engine.run_module(
            "import * as THREE from 'three/webgpu';"
            "import {OrbitControls} from 'three/addons/controls/OrbitControls.js';"
            "import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';"
            "const scene = new THREE.Scene();"
            "scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));"
            "globalThis.demoModuleSmoke = scene.children.length === 1 &&"
            "typeof OrbitControls === 'function' && typeof GLTFLoader === 'function';",
            resolve_threejs_module,
            [&](const std::string& message, const choc::value::Value&) {
                error = message;
                completed = true;
            });
        for (int i = 0; i < 128 && !completed; ++i) engine.pump_message_loop();
        if (!completed) throw std::runtime_error("Three.js module did not complete");
        if (!error.empty()) throw std::runtime_error(error);
        if (!engine.evaluate("demoModuleSmoke").getWithDefault<bool>(false))
            throw std::runtime_error("Three.js scene or addon exports were unavailable");
        std::cout << "installed Three.js module and scene: ok\n";
    } catch (const std::exception& error) {
        std::cerr << "installed Three.js consumer: " << error.what() << '\n';
        return 1;
    }
    return 0;
}

std::string eval_string(ScriptEngine& engine, const std::string& code) {
    return std::string(engine.evaluate(code).getWithDefault<std::string_view>(""));
}

void write_binary_file(const std::filesystem::path& path, const std::vector<uint8_t>& bytes) {
    std::ofstream out(path, std::ios::binary);
    if (!out.good()) {
        throw std::runtime_error("Cannot write file: " + path.string());
    }
    out.write(reinterpret_cast<const char*>(bytes.data()),
              static_cast<std::streamsize>(bytes.size()));
    out.close();
    if (!out.good()) {
        throw std::runtime_error("Failed to write file: " + path.string());
    }
}

std::string js_single_quoted_string(std::string value) {
    std::string out;
    out.reserve(value.size() + 2);
    for (char c : value) {
        if (c == '\\' || c == '\'') {
            out.push_back('\\');
            out.push_back(c);
        } else if (c == '\n') {
            out += "\\n";
        } else if (c == '\r') {
            out += "\\r";
        } else {
            out.push_back(c);
        }
    }
    return out;
}

std::vector<uint8_t> make_textured_box_glb() {
    struct Vertex {
        float px, py, pz;
        float nx, ny, nz;
        float u, v;
    };

    const std::array<Vertex, 24> vertices = {{
        {-0.5f, -0.5f,  0.5f,  0,  0,  1, 0, 0}, { 0.5f, -0.5f,  0.5f,  0,  0,  1, 1, 0}, { 0.5f,  0.5f,  0.5f,  0,  0,  1, 1, 1}, {-0.5f,  0.5f,  0.5f,  0,  0,  1, 0, 1},
        { 0.5f, -0.5f, -0.5f,  0,  0, -1, 0, 0}, {-0.5f, -0.5f, -0.5f,  0,  0, -1, 1, 0}, {-0.5f,  0.5f, -0.5f,  0,  0, -1, 1, 1}, { 0.5f,  0.5f, -0.5f,  0,  0, -1, 0, 1},
        {-0.5f, -0.5f, -0.5f, -1,  0,  0, 0, 0}, {-0.5f, -0.5f,  0.5f, -1,  0,  0, 1, 0}, {-0.5f,  0.5f,  0.5f, -1,  0,  0, 1, 1}, {-0.5f,  0.5f, -0.5f, -1,  0,  0, 0, 1},
        { 0.5f, -0.5f,  0.5f,  1,  0,  0, 0, 0}, { 0.5f, -0.5f, -0.5f,  1,  0,  0, 1, 0}, { 0.5f,  0.5f, -0.5f,  1,  0,  0, 1, 1}, { 0.5f,  0.5f,  0.5f,  1,  0,  0, 0, 1},
        {-0.5f,  0.5f,  0.5f,  0,  1,  0, 0, 0}, { 0.5f,  0.5f,  0.5f,  0,  1,  0, 1, 0}, { 0.5f,  0.5f, -0.5f,  0,  1,  0, 1, 1}, {-0.5f,  0.5f, -0.5f,  0,  1,  0, 0, 1},
        {-0.5f, -0.5f, -0.5f,  0, -1,  0, 0, 0}, { 0.5f, -0.5f, -0.5f,  0, -1,  0, 1, 0}, { 0.5f, -0.5f,  0.5f,  0, -1,  0, 1, 1}, {-0.5f, -0.5f,  0.5f,  0, -1,  0, 0, 1}
    }};
    const std::array<uint16_t, 36> indices = {{
        0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7, 8, 9, 10, 8, 10, 11,
        12, 13, 14, 12, 14, 15, 16, 17, 18, 16, 18, 19, 20, 21, 22, 20, 22, 23
    }};

    std::vector<uint8_t> bin;
    auto append_float = [&](float value) {
        uint32_t bits = 0;
        static_assert(sizeof(bits) == sizeof(value));
        std::memcpy(&bits, &value, sizeof(value));
        bin.push_back(static_cast<uint8_t>(bits & 0xff));
        bin.push_back(static_cast<uint8_t>((bits >> 8) & 0xff));
        bin.push_back(static_cast<uint8_t>((bits >> 16) & 0xff));
        bin.push_back(static_cast<uint8_t>((bits >> 24) & 0xff));
    };
    auto append_u16 = [&](uint16_t value) {
        bin.push_back(static_cast<uint8_t>(value & 0xff));
        bin.push_back(static_cast<uint8_t>((value >> 8) & 0xff));
    };

    const uint32_t position_offset = static_cast<uint32_t>(bin.size());
    for (const auto& v : vertices) {
        append_float(v.px); append_float(v.py); append_float(v.pz);
    }
    const uint32_t normal_offset = static_cast<uint32_t>(bin.size());
    for (const auto& v : vertices) {
        append_float(v.nx); append_float(v.ny); append_float(v.nz);
    }
    const uint32_t uv_offset = static_cast<uint32_t>(bin.size());
    for (const auto& v : vertices) {
        append_float(v.u); append_float(v.v);
    }
    const uint32_t index_offset = static_cast<uint32_t>(bin.size());
    for (auto index : indices) {
        append_u16(index);
    }

    const char* texture_png =
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP4Yh/33+TyzP8MIALEAQBa+Qpfg6yfXgAAAABJRU5ErkJggg==";
    std::ostringstream json;
    json
        << "{\"asset\":{\"version\":\"2.0\",\"generator\":\"vellum-threejs-native-demo\"},"
        << "\"scene\":0,\"scenes\":[{\"nodes\":[0]}],"
        << "\"nodes\":[{\"mesh\":0,\"name\":\"BoxTextured\"}],"
        << "\"meshes\":[{\"name\":\"BoxTextured\",\"primitives\":[{\"attributes\":{\"POSITION\":0,\"NORMAL\":1,\"TEXCOORD_0\":2},\"indices\":3,\"material\":0}]}],"
        << "\"materials\":[{\"name\":\"PulpTextureMaterial\",\"pbrMetallicRoughness\":{\"baseColorTexture\":{\"index\":0},\"metallicFactor\":0,\"roughnessFactor\":0.7}}],"
        << "\"textures\":[{\"sampler\":0,\"source\":0}],\"samplers\":[{\"magFilter\":9728,\"minFilter\":9728,\"wrapS\":10497,\"wrapT\":10497}],"
        << "\"images\":[{\"mimeType\":\"image/png\",\"uri\":\"data:image/png;base64," << texture_png << "\"}],"
        << "\"buffers\":[{\"byteLength\":" << bin.size() << "}],"
        << "\"bufferViews\":["
        << "{\"buffer\":0,\"byteOffset\":" << position_offset << ",\"byteLength\":" << (vertices.size() * 3 * sizeof(float)) << ",\"target\":34962},"
        << "{\"buffer\":0,\"byteOffset\":" << normal_offset << ",\"byteLength\":" << (vertices.size() * 3 * sizeof(float)) << ",\"target\":34962},"
        << "{\"buffer\":0,\"byteOffset\":" << uv_offset << ",\"byteLength\":" << (vertices.size() * 2 * sizeof(float)) << ",\"target\":34962},"
        << "{\"buffer\":0,\"byteOffset\":" << index_offset << ",\"byteLength\":" << (indices.size() * sizeof(uint16_t)) << ",\"target\":34963}],"
        << "\"accessors\":["
        << "{\"bufferView\":0,\"componentType\":5126,\"count\":24,\"type\":\"VEC3\",\"min\":[-0.5,-0.5,-0.5],\"max\":[0.5,0.5,0.5]},"
        << "{\"bufferView\":1,\"componentType\":5126,\"count\":24,\"type\":\"VEC3\"},"
        << "{\"bufferView\":2,\"componentType\":5126,\"count\":24,\"type\":\"VEC2\"},"
        << "{\"bufferView\":3,\"componentType\":5123,\"count\":36,\"type\":\"SCALAR\"}]}";

    std::string json_text = json.str();
    while ((json_text.size() % 4) != 0) json_text.push_back(' ');
    while ((bin.size() % 4) != 0) bin.push_back(0);

    const uint32_t total_length = static_cast<uint32_t>(12 + 8 + json_text.size() + 8 + bin.size());
    std::vector<uint8_t> out;
    out.reserve(total_length);
    auto append_u32 = [&](uint32_t value) {
        out.push_back(static_cast<uint8_t>(value & 0xff));
        out.push_back(static_cast<uint8_t>((value >> 8) & 0xff));
        out.push_back(static_cast<uint8_t>((value >> 16) & 0xff));
        out.push_back(static_cast<uint8_t>((value >> 24) & 0xff));
    };
    append_u32(0x46546c67);
    append_u32(2);
    append_u32(total_length);
    append_u32(static_cast<uint32_t>(json_text.size()));
    append_u32(0x4e4f534a);
    out.insert(out.end(), json_text.begin(), json_text.end());
    append_u32(static_cast<uint32_t>(bin.size()));
    append_u32(0x004e4942);
    out.insert(out.end(), bin.begin(), bin.end());
    return out;
}

class GltfBoxAsset {
public:
    GltfBoxAsset() = default;
    GltfBoxAsset(const GltfBoxAsset&) = delete;
    GltfBoxAsset& operator=(const GltfBoxAsset&) = delete;

    ~GltfBoxAsset() {
        if (directory_.empty()) return;
#ifndef VELLUM_THREEJS_DISABLE_ASSET_CLEANUP
        std::error_code ec;
        std::filesystem::remove(directory_ / filename, ec);
        std::filesystem::remove(directory_, ec);
#endif
    }

    bool stage(const std::filesystem::path& root) {
        std::error_code ec;
        const auto absolute_root = std::filesystem::absolute(root, ec);
        if (ec) return report_error(root, ec);
        std::filesystem::create_directories(absolute_root, ec);
        if (ec) return report_error(absolute_root, ec);

        // Each process owns its fixture, including simultaneous captures.
        auto pattern = (absolute_root / "gltf-box.XXXXXX").string();
        if (!::mkdtemp(pattern.data())) {
            return report_error(absolute_root, {errno, std::generic_category()});
        }
        directory_ = pattern;
        write_binary_file(directory_ / filename, make_textured_box_glb());
        return true;
    }

    std::string load_prelude(WidgetBridge& bridge) const {
        if (!directory_.empty()) bridge.set_asset_roots({directory_});
        bridge.load_script("");
        if (directory_.empty()) return {};
        return "file://" + (directory_ / filename).generic_string();
    }

private:
    static bool report_error(const std::filesystem::path& root, const std::error_code& ec) {
        std::cerr << "Cannot create glTF asset directory " << root << " (" << ec.message() << ")\n";
        return false;
    }

    static constexpr auto filename = "BoxTextured.glb";
    std::filesystem::path directory_;
};

int run_gltf_asset_smoke(const std::filesystem::path& root) {
    GltfBoxAsset asset;
    if (!asset.stage(root)) return 1;

    View view;
    ScriptEngine engine(JsEngineType::v8);
    vellum::state::StateStore store;
    WidgetBridge bridge(engine, view, store);
    const auto url = asset.load_prelude(bridge);
    engine.evaluate("globalThis.assetStatus = 'fetching';"
                    "fetch('" + js_single_quoted_string(url) + "').then(async response => {"
                    "  if (!response.ok) throw new Error('HTTP ' + response.status);"
                    "  const bytes = await response.arrayBuffer();"
                    "  if (bytes.byteLength <= 12 || new DataView(bytes).getUint32(0, true) !== 0x46546c67)"
                    "    throw new Error('Invalid GLB');"
                    "  assetStatus = 'ready';"
                    "}).catch(error => { assetStatus = String(error); });");
    for (int i = 0; i < 128; ++i) {
        engine.pump_message_loop();
        const auto status = eval_string(engine, "assetStatus");
        if (status == "ready") {
            std::cout << "Staged glTF asset fetched through WidgetBridge\n";
            return 0;
        }
        if (status == "fetching") continue;
        std::cerr << "Staged glTF asset fetch failed: " << status << '\n';
        return 1;
    }
    std::cerr << "Staged glTF asset fetch did not complete\n";
    return 1;
}

struct DemoEnvironment {
    View root;
    ScriptEngine engine{JsEngineType::v8};
    vellum::state::StateStore store;
    std::unique_ptr<WindowHost> window;
    std::unique_ptr<WidgetBridge> bridge;

    DemoEnvironment(float width, float height) {
        root.set_bounds({0, 0, width, height});
        root.set_theme(Theme::dark());
    }
};

std::string make_threejs_demo_module(int width, int height, std::string_view mode,
                                     const std::string& gltf_box_url) {
    namespace fs = std::filesystem;
    // __FILE__ may be prefix-mapped by ccache, so bind this runtime fixture to
    // the configure-time source directory instead of a diagnostic file path.
    const fs::path template_path = fs::path(VELLUM_THREEJS_DEMO_SOURCE_DIR)
                                 / "demo.js.template";
    std::string js = read_text_file(template_path);
    auto replace_all = [&](const std::string& from, const std::string& to) {
        size_t pos = 0;
        while ((pos = js.find(from, pos)) != std::string::npos) {
            js.replace(pos, from.length(), to);
            pos += to.length();
        }
    };
    replace_all("__WIDTH__", std::to_string(width));
    replace_all("__HEIGHT__", std::to_string(height));
    const bool untextured_gltf_box = mode == "gltf-box-untextured";
    replace_all("__MODE__", untextured_gltf_box ? "gltf-box" : std::string(mode));
    replace_all("__DISABLE_GLTF_TEXTURE__", untextured_gltf_box ? "true" : "false");
    replace_all("__PARTICLE_COUNT__", "480");
    replace_all("__GLTF_BOX_URL__", js_single_quoted_string(gltf_box_url));

    return js;
}

void prime_demo_frames(DemoEnvironment& env, int frames) {
    for (int i = 0; i < frames; ++i) {
        env.bridge->service_frame_callbacks();
        env.engine.pump_message_loop();
    }
}

bool load_demo(DemoEnvironment& env, int width, int height, std::string_view mode,
               std::string& error_out,
               const std::string& gltf_box_url) {
    bool module_completed = false;
    std::string module_error;
    env.engine.run_module(
        make_threejs_demo_module(width, height, mode, gltf_box_url),
        resolve_threejs_module,
        [&](const std::string& error, const choc::value::Value&) {
            module_completed = true;
            module_error = error;
        });

    // Pump microtasks and animation frames while the module promise settles.
    for (int i = 0; i < 1024; ++i) {
        env.bridge->service_frame_callbacks();
        env.engine.pump_message_loop();
        const auto status = eval_string(
            env.engine,
            "globalThis.__pulpThreeDemoState && globalThis.__pulpThreeDemoState.status || ''");
        if (status == "ready" || status == "error") {
            break;
        }
    }

    // Flush trailing microtasks without advancing the scene again.
    for (int i = 0; i < 64; ++i) {
        env.engine.pump_message_loop();
    }

    if (!module_completed) {
        error_out = "Three.js demo module did not complete";
        return false;
    }
    if (!module_error.empty()) {
        error_out = module_error;
        return false;
    }

    const auto status = eval_string(env.engine, "globalThis.__pulpThreeDemoState.status");
    if (status != "ready") {
        const auto state = eval_string(env.engine, "JSON.stringify(globalThis.__pulpThreeDemoState || {})");
        error_out = "Three.js demo failed: " + state;
        return false;
    }

    return true;
}

int print_engine_identity() {
    ScriptEngine engine(JsEngineType::v8);
    std::cout << "engine_type=" << engine_type_name(engine.engine_type()) << '\n'
              << "runtime_version=" << engine.runtime_version() << '\n'
              << "expected_runtime_version=" << engine.expected_runtime_version() << '\n'
              << "provider_kind=" << engine.provider_kind() << '\n'
              << "provider_path=" << engine.provider_path() << '\n';
    const std::string_view expected = VELLUM_THREEJS_V8_VERSION;
    const auto version = engine.runtime_version();
    if (engine.engine_type() != JsEngineType::v8 ||
        (version != expected && !version.starts_with(std::string(expected) + "-")) ||
        engine.expected_runtime_version() != expected ||
        engine.provider_kind() != "v8builder" ||
        engine.provider_path() != "@rpath/libv8.dylib") {
        std::cerr << "The demo did not load the SDK's pinned V8 provider\n";
        return 1;
    }
    return 0;
}

} // namespace

int main(int argc, char* argv[]) try {
    if (argc == 2 && std::strcmp(argv[1], "--module-smoke") == 0) {
        return run_module_smoke();
    }
    if (!is_engine_available(JsEngineType::v8)) {
        std::cerr << "V8 is required for the native Three.js demo\n";
        return 1;
    }
    if (argc == 2 && std::strcmp(argv[1], "--print-engine-identity") == 0) {
        return print_engine_identity();
    }
    if ((argc == 2 || argc == 3) && std::strcmp(argv[1], "--gltf-asset-smoke") == 0) {
        return run_gltf_asset_smoke(argc == 3 ? argv[2] : VELLUM_THREEJS_DEMO_ASSET_DIR);
    }

    int width = 820;
    int height = 560;
    std::string mode = "cube";
    std::optional<std::filesystem::path> capture_path;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--size") == 0 && i + 1 < argc) {
            std::string size = argv[++i];
            const auto x = size.find('x');
            if (x != std::string::npos) {
                width = std::stoi(size.substr(0, x));
                height = std::stoi(size.substr(x + 1));
            }
        } else if (std::strcmp(argv[i], "--demo") == 0 && i + 1 < argc) {
            mode = argv[++i];
            if (mode != "cube" && mode != "gltf-box" && mode != "gltf-box-untextured") {
                std::cerr << "Unknown demo mode: " << mode << '\n';
                return 1;
            }
        } else if (std::strcmp(argv[i], "--capture") == 0 && i + 1 < argc) {
            capture_path = std::filesystem::path(argv[++i]);
        } else {
            std::cerr << "Usage: vellum-threejs-native-demo [--demo cube|gltf-box] "
                         "[--size WIDTHxHEIGHT] [--capture PATH]\n";
            return 1;
        }
    }

    std::string error;
    GltfBoxAsset asset;
    if ((mode == "gltf-box" || mode == "gltf-box-untextured") &&
        !asset.stage(VELLUM_THREEJS_DEMO_ASSET_DIR)) return 1;

    if (!vellum::app_host::register_native_dawn_bootstrap(&error)) {
        std::cerr << "Dawn bootstrap registration failed: " << error << '\n';
        return 1;
    }

    DemoEnvironment env(static_cast<float>(width), static_cast<float>(height));
    WindowOptions opts;
    opts.title = "Vellum Native Three.js Demo";
    opts.width = width;
    opts.height = height;
    opts.use_gpu = true;
    opts.initially_hidden = capture_path.has_value();

    env.window = WindowHost::create(env.root, opts);
    if (!env.window) {
        std::cerr << "WindowHost::create failed\n";
        return 1;
    }

    env.bridge = std::make_unique<WidgetBridge>(
        env.engine, env.root, env.store, env.window->gpu_surface());
    const auto gltf_box_url = asset.load_prelude(*env.bridge);
    const auto backend = eval_string(env.engine, "String(getGPUInfo().backendType || '')");
    if (!env.window->gpu_surface() || backend == "Null" || backend.empty() || backend == "Unknown") {
        std::cerr << "No real Dawn adapter available (backend: "
                  << (backend.empty() ? "unavailable" : backend) << ")\n";
        return capture_path ? 77 : 1;
    }
    if (!env.window->is_gpu_backed()) {
        std::cerr << "The Dawn adapter is available but the GPU window could not initialize\n";
        return 1;
    }

    env.bridge->set_repaint_callback([host = env.window.get()] { host->repaint(); });
    if (auto* clock = env.root.frame_clock()) {
        clock->subscribe([bridge = env.bridge.get()](float) {
            bridge->service_frame_callbacks();
            return true;
        });
    }

    if (!load_demo(env, width, height, mode, error, gltf_box_url)) {
        std::cerr << error << '\n';
        return 1;
    }

    prime_demo_frames(env, mode == "cube" ? 4 : 10);
    env.root.layout_children();
    env.window->repaint();
    std::cout << "Three.js native demo ready (" << mode << "): "
              << eval_string(env.engine, "JSON.stringify(globalThis.__pulpThreeDemoState)") << '\n';

    if (capture_path) {
        const auto png = env.window->capture_back_buffer_png();
        if (png.empty()) {
            std::cerr << "Demo GPU back-buffer capture failed\n";
            return 1;
        }
        write_binary_file(*capture_path, png);
        std::cout << "Captured demo frame: " << capture_path->string() << '\n';
        return 0;
    }

    env.window->run_event_loop();
    return 0;
} catch (const std::exception& error) {
    std::cerr << "Vellum Three.js demo: " << error.what() << '\n';
    return 1;
}
