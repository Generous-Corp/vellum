#include "rendered_tree_materializer.hpp"

#include <vellum/authoring/js_application.hpp>
#include <vellum/graphics/scene.hpp>

#include <chrono>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

using vellum::authoring::JsApplication;
using vellum::authoring::JsApplicationOptions;
using vellum::authoring::PumpResult;
using vellum::authoring::RenderedApplication;

// Script time is read at load, by a 100 ms timer, and by every render.
constexpr const char* kClockBundle = R"JS(
(() => {
  const loaded = Date.now();
  let fired = "pending";
  let dirty = true;
  setTimeout(() => { fired = "fired@" + (Date.now() - loaded); dirty = true; }, 100);
  const tree = () => ({
    type: "stack", id: "clock-screen", style: { width: 200, height: 100 },
    children: [{ type: "text", id: "clock-text", children: [{
      type: "text-run", id: "clock-text/run", children: [],
      text: fired + "|loaded=" + loaded + "|now=" + Math.round(
        typeof performance === "undefined" ? 0 : performance.now())
    }] }]
  });
  const render = () => { dirty = false; return JSON.stringify({
    protocol: "vellum.authoring-host.v1", tree: tree() }); };
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    hostProtocol: "vellum.authoring-host.v2",
    renderJSON: render,
    dispatchJSON: render,
    isDirty() { return dirty; },
    pumpJSON() { dirty = false; return JSON.stringify({
      protocol: "vellum.authoring-host.v2", kind: "render-result",
      revision: 1, tree: tree() }); }
  };
})();
)JS";

struct Capture final {
    std::vector<std::string> texts;
    std::uint64_t reads = 0;
    std::uint64_t clock = 0;
};

// Mirrors the host's fixed 16 ms stepping.
bool advance(JsApplication& application, std::uint64_t milliseconds,
             RenderedApplication& rendered, std::string* error) {
    PumpResult result;
    while (milliseconds > 0U) {
        const std::uint64_t step = milliseconds < 16U ? milliseconds : 16U;
        if (!application.pump(step, 64, rendered, result, error)) return false;
        milliseconds -= step;
    }
    return true;
}

bool run_scenario(bool manual_clock, Capture& capture, std::string* error) {
    JsApplicationOptions options;
    options.manual_clock = manual_clock;
    auto application = JsApplication::create(kClockBundle, options, error);
    RenderedApplication rendered;
    if (!application || !application->render(rendered, error)) return false;
    for (const std::uint64_t amount : {40U, 80U, 130U}) {
        if (!advance(*application, amount, rendered, error)) return false;
        const auto* node =
            vellum::graphics::find_node(rendered.scene, "clock-text");
        if (node == nullptr) return false;
        capture.texts.push_back(node->text);
    }
    capture.reads = application->manual_clock_reads();
    capture.clock = application->clock_milliseconds();
    return true;
}

bool manual_clock_is_deterministic() {
    std::string error;
    Capture first;
    Capture second;
    if (!run_scenario(true, first, &error) || !run_scenario(true, second, &error)) {
        std::cerr << "manual clock run failed: " << error << '\n';
        return false;
    }
    // The 100 ms timer fires on the 104 ms step (40 ms, then 56, 72, 88, 104)
    // and nothing is dirty after it, so the last capture repeats the second.
    const bool expected =
        first.texts.size() == 3U &&
        first.texts[0].rfind("pending|", 0) == 0U &&
        first.texts[1] == "fired@104|loaded=1700000000000|now=104" &&
        first.texts[2] == first.texts[1];
    if (first.texts != second.texts || !expected || first.clock != 250U ||
        first.reads == 0U || first.reads != second.reads) {
        std::cerr << "manual clock captures are not deterministic\n";
        for (const auto& text : first.texts) std::cerr << "  " << text << '\n';
        std::cerr << "  clock=" << first.clock << " reads=" << first.reads << '\n';
        return false;
    }
    // Control: the wall clock is not read through the manual clock, and two
    // runs a few milliseconds apart see different script time at load.
    Capture wall_first;
    Capture wall_second;
    if (!run_scenario(false, wall_first, &error)) return false;
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    if (!run_scenario(false, wall_second, &error)) return false;
    if (wall_first.reads != 0U || wall_first.texts == wall_second.texts) {
        std::cerr << "control did not use the wall clock\n";
        return false;
    }
    return true;
}

constexpr const char* kSourceTree = R"JSON({
  "protocol":"vellum.authoring-host.v2","kind":"render-result",
  "tree":{"type":"stack","id":"root","style":{"width":200,"height":120},
    "children":[
      {"type":"view","id":"panel","anchor":"figma:12:34",
        "sourceLocation":{"file":"src/Panel.tsx","line":12,"column":7},
        "style":{"x":10,"y":20,"width":100,"height":60},
        "children":[
          {"type":"view","id":"knob","anchor":"figma:12:35",
            "sourceLocation":{"file":"src/Knob.tsx","line":3,"column":5},
            "style":{"x":5,"y":6,"width":20,"height":20},"children":[]}]},
      {"type":"view","id":"bare","style":{"x":0,"y":90,"width":50,"height":10},
        "children":[]}]}})JSON";

std::string strip_source(std::string tree) {
    for (const std::string field : {
             R"("sourceLocation":{"file":"src/Panel.tsx","line":12,"column":7},)",
             R"("sourceLocation":{"file":"src/Knob.tsx","line":3,"column":5},)"}) {
        const auto at = tree.find(field);
        if (at != std::string::npos) tree.erase(at, field.size());
    }
    return tree;
}

// The check a reviewer relies on: the dump names the source file and box of
// the nested node.
bool dump_locates_knob(const std::string& tree) {
    RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "materialize failed: " << error << '\n';
        return false;
    }
    const std::string dump = vellum::authoring::tree_dump_json(rendered);
    return dump.find(R"("id":"knob","type":"view","box":{"x":15,"y":26,"width":20,"height":20},)"
                     R"("source":{"file":"src/Knob.tsx","line":3,"column":5},)"
                     R"("anchor":"figma:12:35")") != std::string::npos;
}

bool source_dump_reports_source_and_box() {
    if (!dump_locates_knob(kSourceTree)) {
        std::cerr << "dump is missing the knob source or box\n";
        return false;
    }
    // Control: with the source field stripped the same assertion must fail.
    if (dump_locates_knob(strip_source(kSourceTree))) {
        std::cerr << "control passed without a source field\n";
        return false;
    }
    RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(
            kSourceTree, rendered, &error) || rendered.nodes.size() != 4U ||
        rendered.nodes[3].node_id != "bare" || !rendered.nodes[3].source.file.empty() ||
        vellum::authoring::tree_dump_json(rendered).find(
            R"("id":"bare","type":"view","box":{"x":0,"y":90,"width":50,"height":10},"source":null,"anchor":null})")
            == std::string::npos) {
        std::cerr << "a node without source must dump null source\n";
        return false;
    }
    return true;
}

}  // namespace

int main() {
    return manual_clock_is_deterministic() && source_dump_reports_source_and_box()
        ? 0 : 1;
}
