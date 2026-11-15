// The pinned Three.js payload loads as ES modules on V8 through the scripted
// view, with no GPU device: the WebGPU build exports its namespace, the
// renderer and the controls; the GLTF loader resolves with its utility
// imports; a GLB the bridge fetches from a reviewed asset root parses; and the
// mock WebGPU queue copies TypedArray data in elements, as Three.js expects.

#include "framework_test.hpp"
#include "threejs_harness.hpp"

#include <string>

namespace {

using threejs_test::ModuleOutcome;
using threejs_test::ScriptedView;

FRAMEWORK_TEST(three_webgpu_exports_the_namespace_renderer_and_orbit_controls) {
    ModuleOutcome m;
    ScriptedView v(64, 64);
    threejs_test::run_module(v, R"JS(
import * as THREE from 'three/webgpu';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
globalThis.probe = {
    revision: THREE.REVISION,
    renderer: typeof THREE.WebGPURenderer,
    render: typeof THREE.WebGPURenderer.prototype.render,
    init: typeof THREE.WebGPURenderer.prototype.init,
    scene: typeof THREE.Scene,
    mesh: typeof THREE.Mesh,
    nodeMaterial: typeof THREE.MeshBasicNodeMaterial,
    controls: typeof OrbitControls,
    controlsUpdate: typeof OrbitControls.prototype.update,
    box: new THREE.BoxGeometry(1, 1, 1).attributes.position.count
};
)JS", "globalThis.probe", m, 64);

    CHECK(m.completed);
    CHECK(m.error.empty());
    if (!m.error.empty()) std::fprintf(stderr, "  module error: %s\n", m.error.c_str());
    CHECK(v.js_string("probe.revision") == "183");
    CHECK(v.js_string("probe.renderer") == "function");
    CHECK(v.js_string("probe.render") == "function");
    CHECK(v.js_string("probe.init") == "function");
    CHECK(v.js_string("probe.scene") == "function");
    CHECK(v.js_string("probe.mesh") == "function");
    CHECK(v.js_string("probe.nodeMaterial") == "function");
    CHECK(v.js_string("probe.controls") == "function");
    CHECK(v.js_string("probe.controlsUpdate") == "function");
    CHECK(v.js_string("probe.box") == "24");
    // The WebGPU build pulls in the shared core; the controls import `three`.
    CHECK(m.resolver.requested.count("three/webgpu") == 1);
    CHECK(m.resolver.requested.count("./three.core.js") == 1);
    CHECK(m.resolver.requested.count("three") == 1);
    CHECK(m.resolver.requested.count("three/addons/controls/OrbitControls.js") == 1);
}

FRAMEWORK_TEST(gltf_loader_addon_resolves_with_its_utility_imports) {
    ModuleOutcome m;
    ScriptedView v(64, 64);
    threejs_test::run_module(v, R"JS(
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
const loader = new GLTFLoader();
globalThis.probe = {
    name: GLTFLoader.name,
    parse: typeof loader.parse,
    load: typeof loader.load,
    setPath: typeof loader.setPath,
    setResourcePath: typeof loader.setResourcePath,
    manager: !!loader.manager
};
)JS", "globalThis.probe", m, 64);

    CHECK(m.completed);
    CHECK(m.error.empty());
    CHECK(v.js_string("probe.name") == "GLTFLoader");
    CHECK(v.js_string("probe.parse") == "function");
    CHECK(v.js_string("probe.load") == "function");
    CHECK(v.js_string("probe.setPath") == "function");
    CHECK(v.js_string("probe.setResourcePath") == "function");
    CHECK(v.js_string("probe.manager") == "true");
    CHECK(m.resolver.requested.count("three/addons/loaders/GLTFLoader.js") == 1);
    CHECK(m.resolver.requested.count("../utils/BufferGeometryUtils.js") == 1);
    CHECK(m.resolver.requested.count("../utils/SkeletonUtils.js") == 1);
}

FRAMEWORK_TEST(a_glb_fetched_from_an_asset_root_parses_with_gltf_loader) {
    const auto bytes = threejs_test::minimal_glb();
    const auto glb = threejs_test::write_fixture("vellum-threejs-glb-fetch", "scene.glb", bytes);
    ModuleOutcome m;
    ScriptedView v(64, 64);
    v.bridge.set_asset_roots({glb.parent_path()});

    const auto code = threejs_test::substitute(std::string(R"JS(
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
)JS") + threejs_test::kCanvasAdapter + R"JS(
globalThis.state = { status: 'fetching' };
(async () => {
    const response = await fetch('@URL@');
    state.ok = response.ok;
    state.httpStatus = response.status;
    state.contentType = String(response.headers.get('content-type') || '');
    const buffer = await response.arrayBuffer();
    state.byteLength = buffer.byteLength;
    const gltf = await new Promise((resolve, reject) => new GLTFLoader().parse(buffer, '', resolve, reject));
    state.scenes = gltf.scenes.length;
    state.sceneType = gltf.scene.type;
    state.status = 'parsed';
})().catch((e) => { state.status = 'error'; state.message = errorText(e); });
)JS", "@URL@", threejs_test::file_url(glb));
    threejs_test::run_module(v, code, "state.status !== 'fetching'", m, 256);

    CHECK(m.completed);
    CHECK(m.error.empty());
    const auto state = v.js_string("JSON.stringify(state)");
    if (v.js_string("state.status") != "parsed") std::fprintf(stderr, "  state: %s\n", state.c_str());
    CHECK(v.js_string("state.status") == "parsed");
    CHECK(v.js_string("state.ok") == "true");
    CHECK(v.js_string("state.httpStatus") == "200");
    CHECK(v.js_string("state.contentType") == "application/octet-stream");
    CHECK(v.js_string("state.byteLength") == std::to_string(bytes.size()));
    CHECK(v.js_string("state.scenes") == "1");
    CHECK(v.js_string("state.sceneType") == "Group");
    std::filesystem::remove(glb);
}

FRAMEWORK_TEST(a_glb_outside_every_asset_root_is_refused) {
    const auto glb = threejs_test::write_fixture("vellum-threejs-glb-refused", "scene.glb",
                                                 threejs_test::minimal_glb());
    ModuleOutcome m;
    ScriptedView v(64, 64);
    const auto code = threejs_test::substitute(R"JS(
globalThis.state = { status: 'fetching' };
fetch('@URL@').then((r) => { state.ok = r.ok; state.httpStatus = r.status; state.status = 'done'; },
                    (e) => { state.status = 'rejected'; });
)JS", "@URL@", threejs_test::file_url(glb));
    threejs_test::run_module(v, code, "state.status !== 'fetching'", m, 64);

    CHECK(m.error.empty());
    CHECK(v.js_string("state.status") == "done");
    CHECK(v.js_string("state.ok") == "false");
    CHECK(v.js_string("state.httpStatus") == "403");
    std::filesystem::remove(glb);
}

// GPUQueue.writeBuffer measures dataOffset and size in elements when the data
// is a TypedArray (bytes only for ArrayBuffer and DataView). Three.js uploads a
// mat4 as writeBuffer(buffer, 0, Float32Array(16), 0, 16).
FRAMEWORK_TEST(mock_write_buffer_counts_typed_array_offset_and_size_in_elements) {
    ScriptedView v(64, 64);
    const auto written = [&v](const std::string& setup) {
        return v.js_string("(() => { const dev = __createMockGPUDevice(__createMockGPUAdapter({}), {}, {});" + setup +
                           " })()");
    };
    CHECK(written("const b = dev.createBuffer({size: 16, usage: 0}); const f = new Float32Array([1, 2, 3, 4]);"
                  " dev.queue.writeBuffer(b, 0, f, 0, f.length);"
                  " return Array.from(new Float32Array(b._bytes.buffer, b._bytes.byteOffset, 4)).join(',');") ==
          "1,2,3,4");
    CHECK(written("const b = dev.createBuffer({size: 8, usage: 0}); const f = new Float32Array([10, 20, 30, 40]);"
                  " dev.queue.writeBuffer(b, 0, f, 2, 2);"
                  " return Array.from(new Float32Array(b._bytes.buffer, b._bytes.byteOffset, 2)).join(',');") ==
          "30,40");
    // An ArrayBuffer source keeps byte units.
    CHECK(written("const b = dev.createBuffer({size: 4, usage: 0}); const u = new Uint8Array([9, 8, 7, 6, 5, 4]);"
                  " dev.queue.writeBuffer(b, 0, u.buffer, 2, 4);"
                  " return Array.from(b._bytes.subarray(0, 4)).join(',');") == "7,6,5,4");
}

} // namespace

int main() {
    if (!vellum::view::is_engine_available(vellum::view::JsEngineType::v8)) {
        std::printf("FAIL the Three.js tests need V8 and this build has none\n");
        return 1;
    }
    return framework_test::run_all();
}
