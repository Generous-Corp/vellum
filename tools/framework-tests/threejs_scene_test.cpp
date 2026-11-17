// A Three.js scene that loads a GLB and drives OrbitControls keeps working
// through a renderer resize, a pointer drag and a wheel event: the renderer's
// canvas takes the new size, the script's handlers see the events and the
// scene follows them, and the bridge records no skipped buffered draw. Nothing
// here reads pixels, so the test runs on Dawn's Null adapter when the host has
// no real one, and exits 77 (skipped) only when Dawn has no adapter at all.

#include "framework_test.hpp"
#include "threejs_gpu.hpp"

#include <string>

namespace {

using threejs_test::ModuleOutcome;
using threejs_test::ScriptedView;

FRAMEWORK_TEST(gltf_scene_follows_resize_pointer_drag_and_wheel) {
    const auto bytes = threejs_test::minimal_glb();
    const auto glb = threejs_test::write_fixture("vellum-threejs-gltf-input", "scene.glb", bytes);
    ModuleOutcome m;
    ScriptedView v(180, 140, threejs_test::surface().get());
    v.bridge.set_asset_roots({glb.parent_path()});

    std::string code = std::string(R"JS(
import * as THREE from 'three/webgpu';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
)JS") + threejs_test::kCanvasAdapter + R"JS(
globalThis.state = { status: 'running' };
globalThis.__phase13BufferedSkips = [];
function send(target, fields) {
    const event = Object.assign({ bubbles: true, cancelable: true,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }, fields);
    return target.dispatchEvent(event);
}
(async () => {
    const response = await fetch('@URL@');
    const buffer = await response.arrayBuffer();
    state.byteLength = buffer.byteLength;
    const gltf = await new Promise((resolve, reject) => new GLTFLoader().parse(buffer, '', resolve, reject));
)JS" + threejs_test::renderer_setup("gltf-input-canvas", 96, 96, "document.body") + R"JS(
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111827);
    const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 10);
    camera.position.z = 2.25;
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.enablePan = false;
    const object = gltf.scene;
    scene.add(object);
    controls.update();

    let dragging = false, lastX = 0, lastY = 0;
    state.drags = 0; state.wheels = 0;
    canvas.addEventListener('pointerdown', (e) => { dragging = true; lastX = e.clientX; lastY = e.clientY; });
    canvas.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        object.rotation.y += (e.clientX - lastX) * 0.01;
        object.rotation.x += (e.clientY - lastY) * 0.01;
        lastX = e.clientX; lastY = e.clientY;
        state.drags += 1;
    });
    canvas.addEventListener('pointerup', () => { dragging = false; });
    canvas.addEventListener('wheel', (e) => {
        camera.position.z = Math.min(6, Math.max(1.1, camera.position.z + e.deltaY * 0.0025));
        state.wheels += 1;
        e.preventDefault();
    });

    renderer.setSize(128, 112, false);
    camera.aspect = 128 / 112;
    camera.updateProjectionMatrix();
    state.canvasWidth = canvas.width;
    state.canvasHeight = canvas.height;
    state.aspect = camera.aspect;

    state.rotationBefore = object.rotation.y;
    state.zBefore = camera.position.z;
    send(canvas, { type: 'pointerdown', pointerId: 1, clientX: 12, clientY: 18 });
    send(canvas, { type: 'pointermove', pointerId: 1, clientX: 58, clientY: 34 });
    send(canvas, { type: 'pointerup', pointerId: 1, clientX: 58, clientY: 34 });
    send(canvas, { type: 'wheel', deltaY: -120 });
    controls.update();
    frame(scene, camera);

    state.rotationAfter = object.rotation.y;
    state.zAfter = camera.position.z;
    state.rendererWidth = renderer.domElement.width;
    state.rendererHeight = renderer.domElement.height;
    state.skips = globalThis.__phase13BufferedSkips.length;
    state.status = 'ready';
})().catch((e) => { state.status = 'error'; state.message = errorText(e); state.stack = String(e && e.stack); });
)JS";
    threejs_test::run_module(v, threejs_test::substitute(code, "@URL@", threejs_test::file_url(glb)),
                             "state.status !== 'running'", m, 512, true);

    CHECK(m.completed);
    CHECK(m.error.empty());
    if (v.js_string("state.status") != "ready")
        std::fprintf(stderr, "  state: %s\n", v.js_string("JSON.stringify(state)").c_str());
    CHECK(v.js_string("state.status") == "ready");
    CHECK(v.js_string("state.byteLength") == std::to_string(bytes.size()));
    CHECK(v.js_string("state.canvasWidth") == "128");
    CHECK(v.js_string("state.canvasHeight") == "112");
    CHECK(v.js_string("state.rendererWidth") == "128");
    CHECK(v.js_string("state.rendererHeight") == "112");
    CHECK(v.js_true("Math.abs(state.aspect - 128 / 112) < 1e-6"));
    CHECK(v.js_string("state.drags") == "1");
    CHECK(v.js_string("state.wheels") == "1");
    CHECK(v.js_true("state.rotationAfter > state.rotationBefore"));
    CHECK(v.js_true("state.zAfter < state.zBefore"));
    CHECK(v.js_string("state.skips") == "0");
    std::filesystem::remove(glb);
}

} // namespace

int main() {
    if (!vellum::view::is_engine_available(vellum::view::JsEngineType::v8)) {
        std::printf("FAIL the Three.js tests need V8 and this build has none\n");
        return 1;
    }
    if (const int skipped = threejs_test::open_surface(threejs_test::AdapterNeed::validation)) return skipped;
    return framework_test::run_all();
}
