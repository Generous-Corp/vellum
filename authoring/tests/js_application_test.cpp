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
        rendered.interactions.size() != (external_bundle ? 3U : 1U) ||
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
         rendered.text_inputs[0].submit_action.empty())) {
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
    return 0;
}
