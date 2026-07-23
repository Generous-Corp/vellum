#include <vellum/authoring/js_application.hpp>

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

constexpr const char* kBundle = R"JS(
(() => {
  let count = 1;
  const render = () => JSON.stringify({
    protocol: "vellum.authoring-host.v1",
    tree: {
      type: "stack",
      id: "screen",
      style: { width: 320, height: 200, padding: 20, gap: 12,
               backgroundColor: "#0F172A" },
      children: [
        { type: "text", id: "title", style: { height: 28, color: "#F8FAFC",
          fontSize: 18 }, children: [{ type: "text-run", id: "title/text",
          text: `Boards ${count}`, children: [] }] },
        { type: "button", id: "add", style: { width: 140, height: 44 },
          events: { press: "add:press" }, children: [
          { type: "text-run", id: "add/text", text: "Add board", children: [] }
        ] },
        { type: "custom", id: "meter", component: "level-meter",
          properties: { values: [0.2, 0.7, 0.4] },
          style: { width: 180, height: 48 }, children: [
          { type: "view", id: "meter-fallback",
            style: { width: 180, height: 48, backgroundColor: "#334155" },
            children: [] }
        ] }
      ]
    }
  });
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    renderJSON: render,
    dispatchJSON(request) {
      const parsed = JSON.parse(request);
      if (parsed.action === "add:press") count += 1;
      return render();
    },
    snapshotStateJSON() {
      return JSON.stringify({ protocol: "vellum.authoring-host.v1", state: { count } });
    },
    restoreStateJSON(snapshot) {
      count = JSON.parse(snapshot).state.count;
      return render();
    }
  };
})();
)JS";

constexpr const char* kAsyncBundle = R"JS(
(() => {
  let values = [];
  let dirty = true;
  let revision = 0;
  const tree = () => ({
    type: "stack", id: "async-screen",
    style: { width: 320, height: 200 },
    children: [{
      type: "text", id: "async-value", children: [{
        type: "text-run", id: "async-value/text",
        text: values.length ? values.join(",") : "initial", children: []
      }]
    }]
  });
  const changed = (value) => {
    values.push(value);
    dirty = true;
  };
  setTimeout(() => {
    changed("first");
    Promise.resolve().then(() => changed("promise"));
  }, 5);
  const cancelled = setTimeout(() => changed("cancelled"), 5);
  clearTimeout(cancelled);
  setTimeout(() => changed("late"), 10);
  const renderLegacy = () => {
    dirty = false;
    revision += 1;
    return JSON.stringify({
      protocol: "vellum.authoring-host.v1", tree: tree()
    });
  };
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    hostProtocol: "vellum.authoring-host.v2",
    renderJSON: renderLegacy,
    dispatchJSON(request) {
      if (JSON.parse(request).action === "runaway") {
        const repeat = () => { changed("runaway"); setTimeout(repeat, 0); };
        setTimeout(repeat, 0);
      }
      return renderLegacy();
    },
    snapshotStateJSON() {
      return JSON.stringify({ protocol: "vellum.authoring-host.v1", state: null });
    },
    restoreStateJSON() { return renderLegacy(); },
    isDirty() { return dirty; },
    pumpJSON() {
      dirty = false;
      revision += 1;
      return JSON.stringify({
        protocol: "vellum.authoring-host.v2",
        kind: "render-result", revision, tree: tree()
      });
    }
  };
})();
)JS";

constexpr const char* kMappedErrorBundle = R"JS(
(() => {
  globalThis.__vellumMapExceptionJSON = error => JSON.stringify({
    protocol: "vellum.authoring-host.v2",
    kind: "diagnostic",
    severity: "error",
    code: "VELLUM_RUNTIME_EXCEPTION",
    message: error.message,
    source: {
      file: "vellum://app/src/App.tsx", line: 40, column: 15,
      function: "throwMappedError"
    },
    stack: [{
      file: "vellum://app/src/App.tsx", line: 40, column: 15,
      function: "throwMappedError"
    }]
  });
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    renderJSON() {
      return JSON.stringify({
        protocol: "vellum.authoring-host.v1",
        tree: {type: "button", id: "mapped-error",
          style: {width: 320, height: 200},
          events: {press: "mapped-error:press"}, children: []}
      });
    },
    dispatchJSON() { throw new Error("phase3-source-map-proof"); }
  };
})();
)JS";

constexpr const char* kTextSemanticsBundle = R"JS(
(() => {
  const render = () => JSON.stringify({
    protocol: "vellum.authoring-host.v1",
    tree: {
      type: "stack", id: "semantic-screen",
      accessibilityLabel: "Editor",
      style: { width: 320, height: 120 },
      children: [{
        type: "text-input", id: "localized-title", primitiveVersion: 1,
        value: "A😀B", placeholder: "Title",
        selection: { start: 1, end: 3 },
        accessibilityLabel: "Localized title",
        accessibilityValue: "A, emoji, B",
        accessibilityState: { disabled: false, selected: true },
        style: { width: 280, height: 44 },
        events: {
          change: "text:change",
          selectionChange: "text:selection",
          compositionStart: "text:start",
          compositionUpdate: "text:update",
          compositionEnd: "text:end"
        },
        children: []
      }]
    }
  });
  globalThis.__vellum = {
    protocol: "vellum.authoring-host.v1",
    renderJSON: render,
    dispatchJSON() { return render(); },
    snapshotStateJSON() {
      return JSON.stringify({ protocol: "vellum.authoring-host.v1", state: null });
    },
    restoreStateJSON() { return render(); }
  };
})();
)JS";

}  // namespace

int main(int argc, char** argv) {
    std::string bundle = kBundle;
    const bool external_bundle = argc == 2;
    if (external_bundle) {
        std::ifstream input(argv[1], std::ios::binary);
        std::ostringstream contents;
        contents << input.rdbuf();
        if (!input || contents.str().empty()) {
            std::cerr << "could not read external authoring bundle\n";
            return 1;
        }
        bundle = contents.str();
    } else if (argc != 1) {
        std::cerr << "usage: vellum-authoring-test [classic-script-bundle]\n";
        return 1;
    }

    std::string error;
    auto application = vellum::authoring::JsApplication::create(bundle, &error);
    if (!application) {
        std::cerr << error << '\n';
        return 1;
    }

    vellum::authoring::RenderedApplication rendered;
    if (!application->render(rendered, &error) ||
        rendered.scene.width != 320.0F ||
        rendered.scene.height != (external_bundle ? 180.0F : 200.0F) ||
        rendered.interactions.size() != (external_bundle ? 7U : 1U) ||
        rendered.interactions[0].node_id !=
            (external_bundle ? "native-increment" : "add") ||
        vellum::graphics::find_node(
            rendered.scene, external_bundle ? "native-title" : "title") == nullptr ||
        vellum::graphics::find_node(
            rendered.scene, external_bundle ? "native-increment/label" : "add/label") == nullptr) {
        std::cerr << (error.empty() ? "initial JS materialization failed" : error) << '\n';
        return 1;
    }
    if (external_bundle &&
        (rendered.text_inputs.size() != 1U ||
         rendered.text_inputs[0].node_id != "native-title-input" ||
         rendered.text_inputs[0].value != "Draft" ||
         rendered.text_inputs[0].change_action.empty() ||
         rendered.text_inputs[0].submit_action.empty() ||
         rendered.text_inputs[0].selection_change_action.empty() ||
         rendered.text_inputs[0].composition_start_action.empty() ||
         rendered.text_inputs[0].composition_update_action.empty() ||
         rendered.text_inputs[0].composition_end_action.empty() ||
         rendered.accessibility_nodes.size() != 2U ||
         rendered.accessibility_nodes[1].role != "text-field" ||
         rendered.accessibility_nodes[1].label != "Board title")) {
        std::cerr << "TextInput v1 materialization failed\n";
        return 1;
    }
    if (!external_bundle) {
        const auto* custom = vellum::graphics::find_node(rendered.scene, "meter");
        if (custom == nullptr ||
            custom->kind != vellum::graphics::SceneNode::Kind::custom ||
            custom->custom_component != "level-meter" ||
            custom->custom_properties_json.find("\"values\"") == std::string::npos ||
            custom->children.size() != 1U || custom->children[0].id != "meter-fallback") {
            return 1;
        }
    }
    const std::string action = rendered.interactions[0].action;
    if (!application->dispatch(action, R"({"pointerType":"mouse"})",
                               rendered, &error)) {
        std::cerr << error << '\n';
        return 1;
    }
    const auto* changed = vellum::graphics::find_node(
        rendered.scene, external_bundle ? "native-increment/label" : "title");
    if (changed == nullptr || changed->text != (external_bundle ? "Count 1" : "Boards 2")) {
        return 1;
    }

    std::string snapshot;
    if (!application->snapshot_state(snapshot, &error) || snapshot.empty()) return 1;
    if (!application->dispatch(action, "null", rendered, &error)) return 1;
    if (!application->restore_state(snapshot, rendered, &error)) return 1;
    changed = vellum::graphics::find_node(
        rendered.scene, external_bundle ? "native-increment/label" : "title");
    if (changed == nullptr || changed->text != (external_bundle ? "Count 1" : "Boards 2")) {
        return 1;
    }

    auto missing = vellum::authoring::JsApplication::create("1 + 1", &error);
    if (missing != nullptr || error.find("did not mount") == std::string::npos) return 1;

    auto malformed = vellum::authoring::JsApplication::create(
        "globalThis.__vellum={protocol:'vellum.authoring-host.v1',"
        "renderJSON(){return '{}'}}", &error);
    if (!malformed || malformed->render(rendered, &error) ||
        error.find("protocol mismatch") == std::string::npos) return 1;

    auto semantic = vellum::authoring::JsApplication::create(
        kTextSemanticsBundle, &error);
    if (!semantic || !semantic->render(rendered, &error) ||
        rendered.text_inputs.size() != 1U ||
        rendered.text_inputs[0].selection_start != 1U ||
        rendered.text_inputs[0].selection_end != 3U ||
        rendered.text_inputs[0].composition_start_action != "text:start" ||
        rendered.text_inputs[0].composition_update_action != "text:update" ||
        rendered.text_inputs[0].composition_end_action != "text:end" ||
        rendered.accessibility_nodes.size() != 2U ||
        rendered.accessibility_nodes[0].node_id != "semantic-screen" ||
        rendered.accessibility_nodes[1].node_id != "localized-title" ||
        rendered.accessibility_nodes[1].role != "text-field" ||
        rendered.accessibility_nodes[1].label != "Localized title" ||
        rendered.accessibility_nodes[1].value != "A, emoji, B" ||
        !rendered.accessibility_nodes[1].state.selected ||
        rendered.accessibility_nodes[1].state.disabled ||
        rendered.accessibility_nodes[1].actions.size() != 2U) {
        std::cerr << (error.empty() ? "text semantics materialization failed" : error) << '\n';
        return 1;
    }

    auto asynchronous = vellum::authoring::JsApplication::create(kAsyncBundle, &error);
    if (!asynchronous || !asynchronous->render(rendered, &error)) return 1;
    vellum::authoring::PumpResult pump;
    if (!asynchronous->pump(5, 8, rendered, pump, &error) ||
        !pump.rendered || pump.idle || pump.tasks_executed != 1U) {
        std::cerr << (error.empty() ? "first async pump failed" : error) << '\n';
        return 1;
    }
    const auto* async_value =
        vellum::graphics::find_node(rendered.scene, "async-value");
    if (async_value == nullptr || async_value->text != "first,promise") return 1;
    if (!asynchronous->wait_for_idle(8, rendered, pump, &error) ||
        !pump.rendered || !pump.idle || pump.tasks_executed != 1U) {
        std::cerr << (error.empty() ? "async wait-for-idle failed" : error) << '\n';
        return 1;
    }
    async_value = vellum::graphics::find_node(rendered.scene, "async-value");
    if (async_value == nullptr || async_value->text != "first,promise,late" ||
        async_value->text.find("cancelled") != std::string::npos) return 1;

    auto runaway = vellum::authoring::JsApplication::create(kAsyncBundle, &error);
    if (!runaway || !runaway->render(rendered, &error) ||
        !runaway->dispatch("runaway", "null", rendered, &error) ||
        runaway->wait_for_idle(3, rendered, pump, &error) ||
        error.find("task limit exceeded") == std::string::npos) {
        std::cerr << "runaway timer protection failed: " << error << '\n';
        return 1;
    }

    auto mapped = vellum::authoring::JsApplication::create(kMappedErrorBundle, &error);
    if (!mapped || !mapped->render(rendered, &error) ||
        mapped->dispatch("mapped-error:press", "null", rendered, &error) ||
        mapped->last_diagnostic_json() != error ||
        error.find("\"code\":\"VELLUM_RUNTIME_EXCEPTION\"") == std::string::npos ||
        error.find("\"file\":\"vellum:\\/\\/app\\/src\\/App.tsx\"") ==
            std::string::npos ||
        error.find("\"line\":40") == std::string::npos) {
        std::cerr << "structured mapped exception failed: " << error << '\n';
        return 1;
    }

    auto missing_map = vellum::authoring::JsApplication::create(
        "globalThis.__vellum={protocol:'vellum.authoring-host.v1',"
        "renderJSON(){throw new Error('missing map')}}", &error);
    if (!missing_map || missing_map->render(rendered, &error) ||
        error.find("\"code\":\"VELLUM_SOURCE_MAP_MISSING\"") == std::string::npos) {
        std::cerr << "missing source map did not fail closed: " << error << '\n';
        return 1;
    }

    auto malformed_map = vellum::authoring::JsApplication::create(
        "globalThis.__vellumMapExceptionJSON=()=>'{bad';"
        "globalThis.__vellum={protocol:'vellum.authoring-host.v1',"
        "renderJSON(){throw new Error('bad map')}}", &error);
    if (!malformed_map || malformed_map->render(rendered, &error) ||
        error.find("\"code\":\"VELLUM_SOURCE_MAP_INVALID\"") == std::string::npos) {
        std::cerr << "malformed source map did not fail closed: " << error << '\n';
        return 1;
    }
    return 0;
}
