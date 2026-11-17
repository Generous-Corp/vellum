// Several draws in one scripted WebGPU render pass, rendered on a real Dawn
// adapter through the bridge's buffered draw path, composited by
// CanvasWidget::paint through Skia and read back. Every draw of the pass must
// survive into the readback, and a pass with a depth attachment must depth-test
// a later draw against the depth an earlier draw wrote.
//
// Runs on the default script engine. Exits 77 (skipped) when the host has no
// rendering adapter.

#include "framework_test.hpp"

#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_surface.hpp>
#include <vellum/render/skia_surface.hpp>
#include <vellum/state/store.hpp>
#include <vellum/view/canvas_widget.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/theme.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <cstdio>
#include <memory>
#include <string>
#include <vector>

namespace {

namespace view = vellum::view;

constexpr uint32_t kWidth = 64;
constexpr uint32_t kHeight = 32;

std::unique_ptr<gpu_runtime::GpuSurface> g_surface;

// Sets up g_surface on a real adapter and returns 0, or returns 77 (skipped).
int open_surface() {
    std::string bootstrap_error;
    if (!gpu_runtime::register_native_dawn_bootstrap("framework-test", &bootstrap_error)) {
        std::printf("SKIP Dawn bootstrap unavailable: %s\n", bootstrap_error.c_str());
        return 77;
    }
    auto candidate = gpu_runtime::GpuSurface::create_dawn();
    gpu_runtime::GpuSurface::Config config;
    config.width = kWidth;
    config.height = kHeight;
    config.vsync = false;
    if (!candidate || !candidate->initialize(config) || !candidate->adapter_info().native_bridge ||
        candidate->adapter_info().null_backend) {
        std::printf("SKIP no rendering Dawn adapter available\n");
        return 77;
    }
    std::printf("adapter: %s (%s)\n", candidate->adapter_info().name.c_str(),
                candidate->adapter_info().backend.c_str());
    g_surface = std::move(candidate);
    return 0;
}

struct ScriptedCanvas {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    view::WidgetBridge bridge;

    ScriptedCanvas() : bridge(engine, root, store, g_surface.get()) {
        root.set_bounds({0, 0, float(kWidth), float(kHeight)});
        root.set_theme(view::Theme::dark());
        bridge.load_script("");
    }

    std::string js_string(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }
};

// A canvas `id` filling the root, a bridged device, a configured context, and
// helpers the cases draw with: quad(x0, x1, z) is two triangles spanning the
// full height between clip-space x0 and x1 at depth z, and pipeline(rgb, depth)
// fills with one colour and, when `depth` is true, tests `less` and writes depth.
constexpr const char* kSetup = R"JS(
globalThis.drawResults = [];
globalThis.__phase13BufferedSkips = [];
const forwardDraw = __gpuQueueDrawBufferedImpl;
__gpuQueueDrawBufferedImpl = function (payload) {
    const ok = forwardDraw(payload);
    drawResults.push(String(ok));
    return ok;
};
document.body.style.padding = '0px';
const canvas = document.createElement('canvas');
canvas.id = 'multi-draw'; canvas.width = 64; canvas.height = 32;
document.body.appendChild(canvas);
// getContext installs the native command path, so the device comes after it.
const context = canvas.getContext('webgpu');
const dev = __createMockGPUDevice(__createGPUAdapter({nativeBridge: true}), {}, {nativeBridge: true});
globalThis.gpuErrors = [];
dev.onuncapturederror = function (event) { gpuErrors.push(String(event.error && event.error.message)); };
context.configure({device: dev, format: 'bgra8unorm'});
function quad(x0, x1, z) {
    const v = new Float32Array([x0, -1, z, x1, -1, z, x1, 1, z, x0, -1, z, x1, 1, z, x0, 1, z]);
    const buffer = dev.createBuffer({size: v.byteLength, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST});
    dev.queue.writeBuffer(buffer, 0, v);
    return buffer;
}
function pipeline(rgb, depth) {
    const module = dev.createShaderModule({code:
        '@vertex fn vs(@location(0) p : vec3f) -> @builtin(position) vec4f { return vec4f(p, 1.0); }\n' +
        '@fragment fn fs() -> @location(0) vec4f { return vec4f(' + rgb + ', 1.0); }\n'});
    const descriptor = {
        layout: 'auto',
        vertex: {module, entryPoint: 'vs',
                 buffers: [{arrayStride: 12, attributes: [{shaderLocation: 0, offset: 0, format: 'float32x3'}]}]},
        fragment: {module, entryPoint: 'fs', targets: [{format: 'bgra8unorm'}]},
        primitive: {topology: 'triangle-list'}
    };
    if (depth) descriptor.depthStencil = {format: 'depth24plus', depthCompare: 'less', depthWriteEnabled: true};
    return dev.createRenderPipeline(descriptor);
}
function colourAttachment() {
    return {view: context.getCurrentTexture().createView(), loadOp: 'clear', storeOp: 'store',
            clearValue: {r: 0, g: 0, b: 1, a: 1}};
}
// One pass into `colour` with a fresh depth attachment of the given size and
// sample count: near red over the left half, then far green over everything.
function depthPass(colour, width, height, sampleCount) {
    const depthTexture = dev.createTexture({size: {width, height}, format: 'depth24plus', sampleCount,
                                            usage: GPUTextureUsage.RENDER_ATTACHMENT});
    const encoder = dev.createCommandEncoder();
    const pass = encoder.beginRenderPass({
        colorAttachments: [colour],
        depthStencilAttachment: {view: depthTexture.createView(), depthLoadOp: 'clear', depthClearValue: 1.0,
                                 depthStoreOp: 'store'}
    });
    draw(pass, pipeline('1.0, 0.0, 0.0', true), quad(-1, 0, 0.2));
    draw(pass, pipeline('0.0, 1.0, 0.0', true), quad(-1, 1, 0.8));
    pass.end();
    dev.queue.submit([encoder.finish()]);
}
function draw(pass, pipe, buffer) {
    pass.setPipeline(pipe);
    pass.setVertexBuffer(0, buffer);
    pass.draw(6);
}
)JS";

struct Readback {
    uint32_t width = 0;
    uint32_t height = 0;
    std::vector<uint8_t> rgba;

    bool ok() const { return !rgba.empty() && rgba.size() == size_t(width) * height * 4u; }
    const uint8_t* at(uint32_t x, uint32_t y) const { return rgba.data() + (size_t(y) * width + x) * 4u; }
};

Readback paint_canvas(ScriptedCanvas& s) {
    Readback out;
    s.root.layout_children();
    const auto native_id = s.js_string("document.getElementById('multi-draw')._id");
    auto* widget = dynamic_cast<view::CanvasWidget*>(s.bridge.widget(native_id));
    if (widget == nullptr) return out;
    auto skia = vellum::render::SkiaSurface::create(*g_surface, {.width = kWidth, .height = kHeight});
    if (!skia || !skia->is_available() || !g_surface->begin_frame()) return out;
    auto* canvas = skia->begin_frame();
    if (canvas == nullptr) return out;
    widget->paint(*canvas);
    const bool painted = widget->last_native_gpu_texture_draw_succeeded();
    skia->end_frame();
    const bool read = skia->read_current_rgba(out.rgba, out.width, out.height);
    g_surface->end_frame();
    if (!read || !painted) out.rgba.clear();
    return out;
}

// What the binding returned for each buffered draw, comma-separated, and the
// draws the script side skipped before reaching it.
std::string draw_results(ScriptedCanvas& s) {
    const auto results = s.js_string("drawResults.join(',')");
    std::fprintf(stderr, "  draws: [%s] skipped: %s\n", results.c_str(),
                 s.js_string("JSON.stringify(__phase13BufferedSkips)").c_str());
    return results;
}

bool is_red(const uint8_t* p) { return p[0] > 200 && p[1] < 60 && p[2] < 60; }
bool is_green(const uint8_t* p) { return p[0] < 60 && p[1] > 200 && p[2] < 60; }

void report(const char* label, const Readback& r) {
    if (!r.ok()) {
        std::fprintf(stderr, "  %s: no readback\n", label);
        return;
    }
    for (uint32_t x : {8u, 24u, 40u, 56u}) {
        const auto* p = r.at(x, kHeight / 2);
        std::fprintf(stderr, "  %s: x=%u rgb=%d,%d,%d\n", label, x, p[0], p[1], p[2]);
    }
}

FRAMEWORK_TEST(every_draw_of_a_pass_survives_into_the_canvas) {
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + R"JS(
const encoder = dev.createCommandEncoder();
const pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('1.0, 0.0, 0.0', false), quad(-1, 0, 0.5));
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(0, 1, 0.5));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("two-quads", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    // The first draw (left half, red) and the second (right half, green).
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_red(r.at(24, kHeight / 2)));
    CHECK(is_green(r.at(40, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_nearer_earlier_draw_is_not_overwritten_by_a_farther_later_one) {
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + "depthPass(colourAttachment(), 64, 32, 1);");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("depth", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_red(r.at(24, kHeight / 2)));
    CHECK(is_green(r.at(40, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_discarding_pass_keeps_every_draw_for_later_draws) {
    // storeOp=discard on a multi-draw pass: each draw replays as its own native
    // pass, so an earlier draw must survive into the next one.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + R"JS(
const encoder = dev.createCommandEncoder();
const attachment = colourAttachment();
attachment.storeOp = 'discard';
const pass = encoder.beginRenderPass({colorAttachments: [attachment]});
draw(pass, pipeline('1.0, 0.0, 0.0', false), quad(-1, 0, 0.5));
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(0, 1, 0.5));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("discard", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_second_pass_that_clears_starts_from_its_clear_colour) {
    // Control for the first-draw rule: a new pass applies its own load
    // operation, so the earlier pass's draws are cleared away.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + R"JS(
let encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('1.0, 0.0, 0.0', false), quad(-1, 0, 0.5));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(0, 1, 0.5));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("second-pass", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    const auto* left = r.at(8, kHeight / 2);
    CHECK(left[0] < 60 && left[1] < 60 && left[2] > 200);
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_resized_target_gets_a_depth_texture_of_its_new_size) {
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + R"JS(
depthPass(colourAttachment(), 64, 32, 1);
canvas.width = 32; canvas.height = 16;
context.configure({device: dev, format: 'bgra8unorm'});
drawResults.length = 0;
depthPass(colourAttachment(), 32, 16, 1);
)JS");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    std::fprintf(stderr, "  errors: %s\n", s.js_string("gpuErrors.join(' | ')").c_str());
    const auto r = paint_canvas(s);
    report("resized", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_multisampled_target_gets_a_multisampled_depth_texture) {
    // The buffered path does not resolve a multisampled target, so this case
    // checks only that the pass is valid: the pipeline and the depth texture
    // take the colour target's sample count.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + R"JS(
const msaa = dev.createTexture({size: {width: 64, height: 32}, format: 'bgra8unorm', sampleCount: 4,
                                usage: GPUTextureUsage.RENDER_ATTACHMENT});
depthPass({view: msaa.createView(), loadOp: 'clear', storeOp: 'store', clearValue: {r: 0, g: 0, b: 1, a: 1}},
          64, 32, 4);
)JS");
    CHECK(draw_results(s) == "true,true");
    std::fprintf(stderr, "  errors: %s\n", s.js_string("gpuErrors.join(' | ')").c_str());
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
}

bool is_blue(const uint8_t* p) { return p[0] < 60 && p[1] < 60 && p[2] > 200; }
bool is_yellow(const uint8_t* p) { return p[0] > 200 && p[1] > 200 && p[2] < 60; }

// A pass into the canvas that loads the colour already there.
constexpr const char* kLoadHelpers = R"JS(
function loadAttachment() {
    return {view: context.getCurrentTexture().createView(), loadOp: 'load', storeOp: 'store'};
}
function depthAttachment(texture, loadOp) {
    return {view: texture.createView(), depthLoadOp: loadOp, depthClearValue: 1.0, depthStoreOp: 'store'};
}
function depthTexture() {
    return dev.createTexture({size: {width: 64, height: 32}, format: 'depth24plus',
                              usage: GPUTextureUsage.RENDER_ATTACHMENT});
}
)JS";

FRAMEWORK_TEST(a_canvas_pass_that_loads_keeps_what_an_earlier_pass_drew) {
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('1.0, 0.0, 0.0', false), quad(-1, 0, 0.5));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()]});
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(0, 1, 0.5));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(draw_results(s) == "true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("canvas-load", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(depth_belongs_to_the_depth_attachment_not_the_colour_target) {
    // Pass 1 writes depth 0.2 over the left half of depth texture A. Pass 2
    // clears depth texture B and writes 0.5 over the right half. Pass 3 loads A
    // and draws at 0.8: A still holds 0.2 on the left, which rejects it, and 1.0
    // on the right, which accepts it. Sharing one depth between the passes
    // because they share a colour target gives the opposite result.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const a = depthTexture();
const b = depthTexture();
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()],
                                    depthStencilAttachment: depthAttachment(a, 'clear')});
draw(pass, pipeline('1.0, 0.0, 0.0', true), quad(-1, 0, 0.2));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                depthStencilAttachment: depthAttachment(b, 'clear')});
draw(pass, pipeline('0.0, 1.0, 0.0', true), quad(0, 1, 0.5));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                depthStencilAttachment: depthAttachment(a, 'load')});
draw(pass, pipeline('1.0, 1.0, 0.0', true), quad(-1, 1, 0.8));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(draw_results(s) == "true,true,true");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("depth-identity", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_yellow(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(retired_depth_textures_do_not_accumulate) {
    // A script that recreates its depth texture (three.js does on every resize)
    // names a new depth key each time. Depth A is written near on the left, then
    // twelve other depth textures are used, then A is loaded and drawn at 0.8.
    // The bridge keeps a bounded number of depth textures, so A has been
    // released by then and loads as a fresh, cleared depth: the draw lands
    // (yellow). An unbounded store would still hold 0.2 there and reject it (red).
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const a = depthTexture();
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()],
                                    depthStencilAttachment: depthAttachment(a, 'clear')});
draw(pass, pipeline('1.0, 0.0, 0.0', true), quad(-1, 0, 0.2));
pass.end();
for (let i = 0; i < 12; ++i) {
  pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                  depthStencilAttachment: depthAttachment(depthTexture(), 'clear')});
  draw(pass, pipeline('0.0, 0.0, 1.0', true), quad(0, 1, 0.9));
  pass.end();
}
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                depthStencilAttachment: depthAttachment(a, 'load')});
draw(pass, pipeline('1.0, 1.0, 0.0', true), quad(-1, 0, 0.8));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("depth-bounded", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_yellow(r.at(8, kHeight / 2)));
}

FRAMEWORK_TEST(a_zero_count_draw_keeps_the_pass_clear_and_later_draws) {
    // The canvas holds green. A pass clears to blue, issues draw(0) and
    // drawIndexed(0), then draws red on the left: the right half must be the
    // pass's blue, not the green left behind.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const indices = dev.createBuffer({size: 16, usage: GPUBufferUsage.INDEX | GPUBufferUsage.COPY_DST});
dev.queue.writeBuffer(indices, 0, new Uint32Array([0, 1, 2, 3]));
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(-1, 1, 0.5));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
const pipe = pipeline('1.0, 0.0, 0.0', false);
pass.setPipeline(pipe);
pass.setVertexBuffer(0, quad(-1, 0, 0.5));
pass.draw(0);
pass.setIndexBuffer(indices, 'uint32');
pass.drawIndexed(0);
pass.draw(6);
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("zero-count", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_red(r.at(8, kHeight / 2)));
    CHECK(is_blue(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(a_pass_with_only_a_zero_count_draw_still_clears) {
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
draw(pass, pipeline('0.0, 1.0, 0.0', false), quad(-1, 1, 0.5));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()]});
pass.setPipeline(pipeline('1.0, 0.0, 0.0', false));
pass.setVertexBuffer(0, quad(-1, 0, 0.5));
pass.draw(0);
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("zero-only", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_blue(r.at(8, kHeight / 2)));
    CHECK(is_blue(r.at(56, kHeight / 2)));
}

FRAMEWORK_TEST(an_empty_pass_still_applies_its_depth_clear) {
    // Near depth, then an empty pass that clears depth, then a far draw that
    // loads depth: the far draw must land everywhere.
    ScriptedCanvas s;
    s.engine.evaluate(std::string(kSetup) + kLoadHelpers + R"JS(
const d = depthTexture();
const encoder = dev.createCommandEncoder();
let pass = encoder.beginRenderPass({colorAttachments: [colourAttachment()],
                                    depthStencilAttachment: depthAttachment(d, 'clear')});
draw(pass, pipeline('1.0, 0.0, 0.0', true), quad(-1, 0, 0.2));
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                depthStencilAttachment: depthAttachment(d, 'clear')});
pass.end();
pass = encoder.beginRenderPass({colorAttachments: [loadAttachment()],
                                depthStencilAttachment: depthAttachment(d, 'load')});
draw(pass, pipeline('0.0, 1.0, 0.0', true), quad(-1, 1, 0.8));
pass.end();
dev.queue.submit([encoder.finish()]);
)JS");
    CHECK(s.js_string("gpuErrors.join(' | ')").empty());
    const auto r = paint_canvas(s);
    report("empty-depth-pass", r);
    CHECK(r.ok());
    if (!r.ok()) return;
    CHECK(is_green(r.at(8, kHeight / 2)));
    CHECK(is_green(r.at(56, kHeight / 2)));
}

} // namespace

int main() {
    if (const int skip = open_surface(); skip != 0) return skip;
    return framework_test::run_all();
}
