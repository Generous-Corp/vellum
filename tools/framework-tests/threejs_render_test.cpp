// Three.js scenes rendered through the scripted WebGPU binding on a real Dawn
// adapter, then composited by CanvasWidget::paint through Skia and read back:
// a sampled DataTexture, a cube in a layout of native widgets, and the four
// visualizer scenes (bar spectrum, point cloud, indexed ribbon, line and point
// room). A broken upload, bind group, buffer copy or topology leaves the
// canvas black or the wrong colour, which these pixel checks see. Exits 77
// (skipped) when the host has no rendering adapter.

#include "framework_test.hpp"
#include "threejs_gpu.hpp"

#include <vellum/view/widgets.hpp>

#include <algorithm>
#include <cmath>
#include <utility>
#include <string>

namespace {

using threejs_test::ModuleOutcome;
using threejs_test::Readback;
using threejs_test::ScriptedView;

// Runs a scene module whose body sets `state.status` to 'ready' when it has
// rendered, or 'error' with a message. Returns whether it reached 'ready'.
bool run_scene(ScriptedView& v, const std::string& body, ModuleOutcome& m) {
    const std::string code = std::string("import * as THREE from 'three/webgpu';\n") + threejs_test::kCanvasAdapter +
                             "globalThis.state = { status: 'running' };\n"
                             "globalThis.__phase13BufferedSkips = [];\n"
                             "try {\n" + body + "\n} catch (e) { state.status = 'error'; state.message = errorText(e);"
                             " state.stack = String(e && e.stack); }\n";
    threejs_test::run_module(v, code, "state.status !== 'running'", m, 512);
    const bool ready = m.completed && m.error.empty() && v.js_string("state.status") == "ready";
    if (!ready)
        std::fprintf(stderr, "  module error: '%s' state: %s\n", m.error.c_str(),
                     v.js_string("JSON.stringify(globalThis.state)").c_str());
    return ready;
}

// A synthetic analysis frame the visualizer scenes read through a native
// function, the way the demo reads its audio analysis.
void register_frame(ScriptedView& v, const std::string& name, double peak, double rms, double beat, double time,
                    double (*bar)(uint32_t)) {
    v.engine.register_function(name, [=](choc::javascript::ArgumentList) {
        auto frame = choc::value::createObject("");
        frame.addMember("bars", choc::value::createArray(24, [bar](uint32_t i) { return choc::value::createFloat64(bar(i)); }));
        frame.addMember("peak", choc::value::createFloat64(peak));
        frame.addMember("rms", choc::value::createFloat64(rms));
        frame.addMember("beat", choc::value::createFloat64(beat));
        frame.addMember("time", choc::value::createFloat64(time));
        return frame;
    });
}

constexpr double kPi = 3.14159265358979323846;

FRAMEWORK_TEST(a_data_texture_is_sampled_from_its_uploaded_bytes) {
    ModuleOutcome m;
    ScriptedView v(128, 128, threejs_test::surface().get());
    CHECK(run_scene(v, threejs_test::renderer_setup("datatexture-canvas", 96, 96, "document.body") + R"JS(
renderer.setSize(96, 96, false);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x020617);
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
camera.position.z = 2;
const texture = new THREE.DataTexture(new Uint8Array([255, 8, 4, 255]), 1, 1, THREE.RGBAFormat);
texture.colorSpace = THREE.NoColorSpace;
texture.magFilter = texture.minFilter = THREE.NearestFilter;
texture.needsUpdate = true;
scene.add(new THREE.Mesh(new THREE.PlaneGeometry(1.4, 1.4),
                         new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide })));
frame(scene, camera);
frame(scene, camera);
state.contextType = renderer.getContext()._objectName;
state.status = 'ready';
)JS", m));
    CHECK(v.js_string("state.contextType") == "GPUCanvasContext");

    const auto r = threejs_test::paint_canvas(v, "datatexture-canvas", 96, 96);
    threejs_test::report("datatexture", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(r.width == 96 && r.height == 96);
    const auto* c = r.center();
    CHECK(c[0] > 160);
    CHECK(c[1] < 100);
    CHECK(c[2] < 80);
    // The plane covers the middle 70%; the corner is the dark background.
    CHECK(r.at(2, 2)[0] < 40);
}

FRAMEWORK_TEST(a_cube_renders_in_a_canvas_laid_out_among_native_widgets) {
    ModuleOutcome m;
    ScriptedView v(320, 220, threejs_test::surface().get());

    // Promise jobs nested inside an async executor run on V8's microtask queue.
    v.engine.evaluate("globalThis.nested = 'start'; new Promise(async (resolve) => {"
                      " await Promise.resolve(); resolve(); }).then(() => { globalThis.nested = 'resolved'; });");
    for (int i = 0; i < 16; ++i) v.engine.pump_message_loop();
    CHECK(v.js_string("nested") == "resolved");

    // Record what the buffered draw path hands the binding.
    v.engine.evaluate("globalThis.drawPayloads = [];"
                      "const forward = __gpuQueueDrawBufferedImpl;"
                      "__gpuQueueDrawBufferedImpl = function (payload) {"
                      "  drawPayloads.push(typeof payload === \"string\" ? payload : JSON.stringify(payload)); return forward(payload); };");

    CHECK(run_scene(v, R"JS(
document.body.style.backgroundColor = '#050816';
document.body.style.padding = '12px';
const shell = document.createElement('div');
shell.id = 'hybrid-shell';
Object.assign(shell.style, { flexDirection: 'row', gap: '12px', width: '296px', height: '196px',
                             backgroundColor: '#101626', padding: '12px' });
document.body.appendChild(shell);
const column = document.createElement('div');
column.style.flexGrow = '1';
shell.appendChild(column);
const title = document.createElement('h2');
title.id = 'hybrid-title';
title.textContent = 'Vellum Three.js';
column.appendChild(title);
const hud = document.createElement('div');
hud.id = 'hybrid-hud';
Object.assign(hud.style, { width: '88px', flexShrink: '0', backgroundColor: '#0b1220' });
shell.appendChild(hud);
)JS" + threejs_test::renderer_setup("hybrid-canvas", 96, 96, "column") + R"JS(
const scene = new THREE.Scene();
scene.background = new THREE.Color(1, 0, 0);
const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 10);
camera.position.z = 2;
const cube = new THREE.Mesh(new THREE.BoxGeometry(0.75, 0.75, 0.75), new THREE.MeshBasicMaterial({ color: 0x00ff00 }));
cube.rotation.set(0.4, 0.6, 0);
scene.add(cube);
frame(scene, camera);
frame(scene, camera);
state.width = renderer.domElement.width;
state.height = renderer.domElement.height;
state.contextType = renderer.getContext()._objectName;
state.backend = renderer.backend.constructor.name;
state.skips = globalThis.__phase13BufferedSkips.length;
state.status = 'ready';
)JS", m));
    CHECK(v.js_string("state.width") == "96");
    CHECK(v.js_string("state.height") == "96");
    CHECK(v.js_string("state.contextType") == "GPUCanvasContext");
    CHECK(v.js_string("state.backend") == "WebGPUBackend");
    CHECK(v.js_string("state.skips") == "0");

    v.root.layout_children();
    auto* title = dynamic_cast<vellum::view::Label*>(v.bridge.widget(v.js_string("document.getElementById('hybrid-title')._id")));
    CHECK(title != nullptr && title->text() == "Vellum Three.js");
    CHECK(v.bridge.widget(v.js_string("document.getElementById('hybrid-hud')._id")) != nullptr);

    // The scene's draws target an offscreen texture of the binding's, which the
    // canvas then presents.
    CHECK(v.js_true("drawPayloads.length > 0"));
    const auto target = v.js_string("drawPayloads.length ? JSON.parse(drawPayloads[0]).targetTextureId || '' : ''");
    CHECK(!target.empty());
    CHECK(v.bridge.describe_native_texture_frame(target).available);
    const auto canvas_frame = v.bridge.describe_native_canvas_frame(v.js_string("document.getElementById('hybrid-canvas')._id"));
    CHECK(canvas_frame.available && canvas_frame.width == 96 && canvas_frame.height == 96);

    const auto r = threejs_test::paint_canvas(v, "hybrid-canvas", 96, 96);
    threejs_test::report("cube", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    const auto* c = r.center();
    CHECK(c[1] > 100);
    CHECK(c[1] > c[0] + 32);
    CHECK(c[1] > c[2] + 32);
    // Outside the cube is the red background.
    const auto* corner = r.at(3, 3);
    CHECK(corner[0] > 150 && corner[1] < 80);
}

FRAMEWORK_TEST(spectrum_bars_render_their_peak_bar) {
    ModuleOutcome m;
    ScriptedView v(320, 220, threejs_test::surface().get());
    register_frame(v, "__readSpectrumFrame__", 1.0, 0.5, 0.0, 0.25,
                   [](uint32_t i) { return std::max(0.15, 1.0 - std::abs(double(i) - 11.5) / 11.5); });
    CHECK(run_scene(v, threejs_test::renderer_setup("spectrum-canvas", 160, 160, "document.body") + R"JS(
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x08111f);
const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 10);
camera.position.set(0, 0.6, 4.8);
const data = __readSpectrumFrame__();
const bars = new THREE.Group();
bars.rotation.x = -0.18;
const barGeometry = new THREE.BoxGeometry(0.2, 1, 0.28);
for (let i = 0; i < 24; ++i) {
    const height = 0.2 + Math.min(1, Math.max(0.05, data.bars[i])) * 2.4;
    const bar = new THREE.Mesh(barGeometry, new THREE.MeshBasicMaterial({ color: 0x38bdf8 }));
    bar.position.set((i - 11.5) * 0.22, height / 2, 0);
    bar.scale.y = height;
    bars.add(bar);
}
scene.add(bars);
const peakHeight = 0.35 + data.peak * 3;
const peak = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1, 0.6), new THREE.MeshBasicMaterial({ color: 0xa3e635 }));
peak.scale.y = peakHeight;
peak.position.set(0, peakHeight / 2, -0.7);
scene.add(peak);
frame(scene, camera);
frame(scene, camera);
state.peak = data.peak;
state.status = 'ready';
)JS", m));
    CHECK(v.js_string("state.peak") == "1");
    const auto r = threejs_test::paint_canvas(v, "spectrum-canvas", 160, 160);
    threejs_test::report("spectrum", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    const auto* c = r.center();
    CHECK(c[1] > 110);
    CHECK(c[1] > c[0]);
    CHECK(c[1] > c[2]);
}

FRAMEWORK_TEST(a_point_cloud_renders_around_its_pulse_core) {
    ModuleOutcome m;
    ScriptedView v(320, 220, threejs_test::surface().get());
    register_frame(v, "__readSpectrumFrame__", 0.92, 0.78, 0.96, 0.35,
                   [](uint32_t i) { return 0.25 + 0.75 * std::sin(double(i) / 23.0 * kPi); });
    CHECK(run_scene(v, threejs_test::renderer_setup("particles-canvas", 160, 160, "document.body") + R"JS(
const data = __readSpectrumFrame__();
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x050816);
const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 10);
camera.position.set(0, 0.7, 5.6);
const count = 320;
const positions = new Float32Array(count * 3);
const colors = new Float32Array(count * 3);
const color = new THREE.Color();
for (let i = 0; i < count; ++i) {
    const ring = 0.45 + (i % 20) * 0.06;
    const angle = (i / count) * Math.PI * 12;
    positions.set([Math.cos(angle) * ring, ((i % 18) - 9) * 0.09 + data.beat * 0.15, Math.sin(angle) * ring * 0.72], i * 3);
    color.setHSL(0.56 - data.bars[i % 24] * 0.28, 0.9, 0.5 + data.rms * 0.12);
    colors.set([color.r, color.g, color.b], i * 3);
}
const geometry = new THREE.BufferGeometry();
geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: 0.13, vertexColors: true }));
points.rotation.set(-0.12, 0.35, 0);
scene.add(points);
const core = new THREE.Mesh(new THREE.SphereGeometry(0.38, 20, 16), new THREE.MeshBasicMaterial({ color: 0x7dd3fc }));
core.position.z = -0.3;
core.scale.setScalar(1 + data.beat * 1.1);
scene.add(core);
frame(scene, camera);
frame(scene, camera);
state.status = 'ready';
)JS", m));
    const auto r = threejs_test::paint_canvas(v, "particles-canvas", 160, 160);
    threejs_test::report("particles", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    const auto* c = r.center();
    CHECK(c[1] > 85);
    CHECK(c[2] > 85);
    CHECK(c[1] + c[2] > c[0] + 70);
}

FRAMEWORK_TEST(an_indexed_waveform_ribbon_renders) {
    ModuleOutcome m;
    ScriptedView v(320, 220, threejs_test::surface().get());
    register_frame(v, "__readSpectrumFrame__", 0.88, 0.74, 0.91, 0.42,
                   [](uint32_t i) { return 0.18 + 0.82 * std::sin(double(i) / 23.0 * kPi); });
    CHECK(run_scene(v, threejs_test::renderer_setup("ribbon-canvas", 180, 140, "document.body") + R"JS(
const data = __readSpectrumFrame__();
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x040814);
const camera = new THREE.PerspectiveCamera(70, 180 / 140, 0.1, 10);
camera.position.set(0, 0.45, 6.2);
const segments = 96;
const positions = new Float32Array((segments + 1) * 2 * 3);
const colors = new Float32Array((segments + 1) * 2 * 3);
const indices = new Uint16Array(segments * 6);
for (let i = 0; i < segments; ++i)
    indices.set([2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 1, 2 * i + 3, 2 * i + 2], i * 6);
const color = new THREE.Color();
for (let i = 0; i <= segments; ++i) {
    const t = i / segments;
    const band = data.bars[Math.min(23, Math.floor(t * 24))];
    const x = (t - 0.5) * 6.8;
    const mid = Math.sin(t * Math.PI * 2 + data.time * 2.1) * (0.12 + band * 0.3) + (band - 0.5) * 0.34 + data.beat * 0.18;
    const half = 0.18 + data.rms * 0.2 + band * 0.07;
    const z = Math.cos(t * Math.PI * 4 + data.time * 1.8) * (0.08 + band * 0.13);
    const tilt = Math.sin(t * Math.PI * 1.5) * 0.08;
    positions.set([x, mid + half, z + tilt, x, mid - half, z - tilt], i * 6);
    color.setHSL(0.58 - band * 0.28, 0.92, 0.56 + data.rms * 0.12);
    colors.set([color.r, color.g, color.b, color.r * 0.42, color.g * 0.45, color.b * 0.72], i * 6);
}
const geometry = new THREE.BufferGeometry();
geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
geometry.setIndex(new THREE.BufferAttribute(indices, 1));
const ribbon = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }));
ribbon.rotation.set(-0.2, 0.18, 0);
scene.add(ribbon);
frame(scene, camera);
frame(scene, camera);
state.indexCount = geometry.index.count;
state.status = 'ready';
)JS", m));
    CHECK(v.js_string("state.indexCount") == "576");
    const auto r = threejs_test::paint_canvas(v, "ribbon-canvas", 180, 140);
    threejs_test::report("ribbon", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    const auto lit = r.count([](int red, int g, int b) { return g > 70 && b > 80 && g + b > red + 50; });
    std::fprintf(stderr, "  ribbon lit pixels: %zu\n", lit);
    CHECK(lit > 200);
}

FRAMEWORK_TEST(room_geometry_follows_its_parameters_between_frames) {
    ModuleOutcome m;
    ScriptedView v(320, 220, threejs_test::surface().get());
    const auto room = [&v](const std::string& name, double width, double height, double absorption, double source_x,
                           double listener_x, double phase, double time) {
        v.engine.register_function(name, [=](choc::javascript::ArgumentList) {
            auto frame = choc::value::createObject("");
            frame.addMember("waveform", choc::value::createArray(24, [phase](uint32_t i) {
                const double t = double(i) / 23.0;
                return choc::value::createFloat64(std::sin(t * 2 * kPi + phase) * 0.58 + std::cos(t * 2 * kPi + phase / 2) * 0.24);
            }));
            const std::pair<const char*, double> fields[] = {
                {"roomWidth", width}, {"roomHeight", height}, {"roomDepth", 5.8}, {"absorption", absorption},
                {"sourceX", source_x}, {"listenerX", listener_x}, {"time", time}};
            for (const auto& [key, value] : fields) frame.addMember(key, choc::value::createFloat64(value));
            return frame;
        });
    };
    room("__roomFrameA__", 6.8, 3.4, 0.22, 0.85, -0.58, 0.95, 0.18);
    room("__roomFrameB__", 8.4, 4.0, 0.64, -1.08, 0.62, 1.12, 0.77);

    CHECK(run_scene(v, threejs_test::renderer_setup("room-canvas", 180, 140, "document.body") + R"JS(
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x040816);
const camera = new THREE.PerspectiveCamera(68, 180 / 140, 0.1, 20);
camera.position.set(0.55, 1.05, 7.1);
camera.lookAt(0, 1.05, 0);
const room = new THREE.Group();
scene.add(room);
const shellMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshBasicMaterial({ color: 0x1e293b, wireframe: true, transparent: true, opacity: 0.8 }));
room.add(shellMesh);
const source = new THREE.Mesh(new THREE.SphereGeometry(0.1, 14, 10), new THREE.MeshBasicMaterial({ color: 0xfb7185 }));
const listener = new THREE.Mesh(new THREE.SphereGeometry(0.12, 14, 10), new THREE.MeshBasicMaterial({ color: 0x7dd3fc }));
room.add(source, listener);
const lines = [];
for (let i = 0; i < 6; ++i) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0xfef08a, transparent: true, opacity: 0.9 }));
    room.add(line);
    lines.push(line);
}
const cloudCount = 120;
const cloudPositions = new Float32Array(cloudCount * 3);
const cloudColors = new Float32Array(cloudCount * 3).fill(0.8);
const cloudGeometry = new THREE.BufferGeometry();
cloudGeometry.setAttribute('position', new THREE.BufferAttribute(cloudPositions, 3));
cloudGeometry.setAttribute('color', new THREE.BufferAttribute(cloudColors, 3));
room.add(new THREE.Points(cloudGeometry,
    new THREE.PointsMaterial({ size: 0.08, vertexColors: true, transparent: true, opacity: 0.7 })));
const tint = new THREE.Color();
function update(data) {
    const peak = data.waveform.reduce((p, x) => Math.max(p, Math.abs(x)), 0);
    shellMesh.scale.set(data.roomWidth, data.roomHeight, data.roomDepth);
    shellMesh.position.y = data.roomHeight / 2;
    shellMesh.material.opacity = Math.max(0.3, 0.86 - data.absorption * 0.5);
    source.position.set(data.sourceX, 0.75, -1.1);
    listener.position.set(data.listenerX, 0.47, 1.25);
    let reflectionX = 0;
    lines.forEach((line, i) => {
        const mid = source.position.clone().add(listener.position).multiplyScalar(0.5);
        if (i % 3 === 0) mid.x = (i < 3 ? -0.5 : 0.5) * data.roomWidth;
        else if (i % 3 === 1) mid.y = i < 3 ? 0 : data.roomHeight;
        else mid.z = (i < 3 ? -0.5 : 0.5) * data.roomDepth;
        mid.y += Math.sin(i * 0.9) * (0.06 + peak * 0.12);
        const p = line.geometry.getAttribute('position');
        p.setXYZ(0, source.position.x, source.position.y, source.position.z);
        p.setXYZ(1, mid.x, mid.y, mid.z);
        p.setXYZ(2, listener.position.x, listener.position.y, listener.position.z);
        p.needsUpdate = true;
        line.material.color.setHSL(0.14 + i * 0.06, 1, 0.72);
        if (i === 0) reflectionX = mid.x;
    });
    for (let i = 0; i < cloudCount; ++i) {
        const sample = data.waveform[i % 24];
        const energy = Math.min(1, Math.abs(sample) * 1.7 + data.absorption * 0.4);
        const swirl = (i / cloudCount) * Math.PI * 14 + data.time * 0.5 + sample * 0.7;
        const radius = 0.34 + peak * 0.5 + (0.28 + (i % 11) * 0.055) * (0.5 + energy * 0.65);
        cloudPositions.set([listener.position.x + Math.cos(swirl) * radius,
                            listener.position.y + Math.sin(data.time * 2 + i) * (0.09 + energy * 0.2),
                            listener.position.z + Math.sin(swirl) * radius * 0.56], i * 3);
        tint.setHSL(0.57 - energy * 0.18, 0.9, 0.54 + energy * 0.2);
        cloudColors.set([tint.r, tint.g, tint.b], i * 3);
    }
    cloudGeometry.getAttribute('position').needsUpdate = true;
    cloudGeometry.getAttribute('color').needsUpdate = true;
    return { width: data.roomWidth, height: data.roomHeight, absorption: data.absorption,
             reflectionX, cloudX: cloudPositions[0], opacity: shellMesh.material.opacity };
}
state.first = update(__roomFrameA__());
frame(scene, camera);
state.second = update(__roomFrameB__());
frame(scene, camera);
state.status = 'ready';
)JS", m));
    for (const char* field : {"width", "height", "absorption", "reflectionX", "cloudX", "opacity"}) {
        const std::string f = field;
        CHECK(v.js_true("Math.abs(state.first." + f + " - state.second." + f + ") > 0.01"));
    }
    const auto r = threejs_test::paint_canvas(v, "room-canvas", 180, 140);
    threejs_test::report("room", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(r.width == 180 && r.height == 140);
    const auto lit = r.count([](int red, int g, int b) { return g > 70 && b > 60 && g + b > red + 30; });
    std::fprintf(stderr, "  room lit pixels: %zu\n", lit);
    CHECK(lit > 12);
}

} // namespace

int main() {
    if (!vellum::view::is_engine_available(vellum::view::JsEngineType::v8)) {
        std::printf("FAIL the Three.js tests need V8 and this build has none\n");
        return 1;
    }
    if (const int skipped = threejs_test::open_surface(threejs_test::AdapterNeed::rendering)) return skipped;
    return framework_test::run_all();
}
