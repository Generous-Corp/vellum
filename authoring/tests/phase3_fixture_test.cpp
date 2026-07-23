#include <vellum/authoring/js_application.hpp>

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

const vellum::authoring::Interaction *
interaction(const vellum::authoring::RenderedApplication &application,
            std::string_view node, std::string_view event) {
  for (const auto &item : application.interactions) {
    if (item.node_id == node && item.event == event)
      return &item;
  }
  return nullptr;
}

bool contains_text(const vellum::graphics::SceneNode &node,
                   std::string_view text) {
  if (node.text == text)
    return true;
  for (const auto &child : node.children) {
    if (contains_text(child, text))
      return true;
  }
  return false;
}

void append_text(const vellum::graphics::SceneNode& node, std::string& output) {
  if (!node.text.empty()) {
    if (!output.empty()) output += " | ";
    output += node.text;
  }
  for (const auto& child : node.children) append_text(child, output);
}

std::string service_host(std::string_view clipboard,
                         std::string_view open_url) {
  return "globalThis.__vellumServiceHost={capabilities:{commands:'v1',"
         "files:'denied',clipboard:'" +
         std::string(clipboard) + "',open_url:'" + std::string(open_url) +
         "',persistence:'state-v1'},requests:[],request(request){"
         "this.requests.push(request);return Promise.resolve({"
         "protocol:'vellum.services.v1',kind:'response',id:request.id,"
         "ok:true,value:null});}};\n";
}

} // namespace

int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "usage: vellum-authoring-phase3-fixture-test BUNDLE\n";
    return 2;
  }
  std::ifstream input(argv[1], std::ios::binary);
  std::ostringstream source;
  source << input.rdbuf();
  if (!input || source.str().empty()) {
    std::cerr << "could not read Phase 3 fixture bundle\n";
    return 1;
  }

  std::string error;
  const std::string enabled_source =
      service_host("text-v1", "external-v1") + source.str();
  auto application =
      vellum::authoring::JsApplication::create(enabled_source, &error);
  if (!application) {
    std::cerr << error << '\n';
    return 1;
  }
  vellum::authoring::RenderedApplication rendered;
  if (!application->render(rendered, &error) ||
      rendered.scene.width != 800.0F || rendered.scene.height != 600.0F ||
      interaction(rendered, "phase3/imported-add", "press") == nullptr ||
      interaction(rendered, "mapped-error", "press") == nullptr ||
      vellum::graphics::find_node(rendered.scene, "title-input") == nullptr ||
      vellum::graphics::find_node(rendered.scene, "item-list") == nullptr) {
    std::cerr << (error.empty() ? "unchanged fixture did not materialize"
                                : error)
              << '\n';
    return 1;
  }

  vellum::authoring::PumpResult pump;
  if (!application->wait_for_idle(32, rendered, pump, &error) || !pump.idle ||
      !contains_text(rendered.scene.root, "timer-complete")) {
    std::cerr << (error.empty() ? "fixture effects did not settle" : error)
              << '\n';
    return 1;
  }

  const auto *add = interaction(rendered, "phase3/imported-add", "press");
  if (add == nullptr ||
      !application->dispatch(add->action,
                             R"({"pointerType":"touch","x":20,"y":20})",
                             rendered, &error) ||
      !contains_text(rendered.scene.root, "Board: Roadmap")) {
    std::cerr << (error.empty() ? "fixture touch action did not update state"
                                : error)
              << '\n';
    return 1;
  }

  std::string snapshot;
  if (!application->snapshot_state(snapshot, &error)) {
    std::cerr << "fixture state snapshot failed: " << error << '\n';
    return 1;
  }
  auto reloaded =
      vellum::authoring::JsApplication::create(enabled_source, &error);
  if (!reloaded || !reloaded->render(rendered, &error) ||
      !reloaded->restore_state(snapshot, rendered, &error) ||
      !contains_text(rendered.scene.root, "Board: Roadmap")) {
    std::cerr << "fixture state continuity failed: " << error << '\n';
    return 1;
  }
  application = std::move(reloaded);

  for (const auto* target : {"copy", "docs"}) {
    const auto* service = interaction(rendered, target, "press");
    if (service == nullptr ||
        !application->dispatch(service->action, "null", rendered, &error) ||
        !application->wait_for_idle(32, rendered, pump, &error)) {
      std::cerr << "fixture enabled service failed for " << target << ": "
                << error << '\n';
      return 1;
    }
  }
  if (!contains_text(rendered.scene.root, "url-complete")) {
    std::cerr << "fixture URL service did not complete\n";
    return 1;
  }

  auto denied = vellum::authoring::JsApplication::create(
      service_host("denied", "denied") + source.str(), &error);
  if (!denied || !denied->render(rendered, &error) ||
      !denied->wait_for_idle(32, rendered, pump, &error)) {
    std::cerr << "fixture denied service setup failed: " << error << '\n';
    return 1;
  }
  for (const auto& denied_service :
       {std::pair{"copy", "clipboard-capability-denied"},
        std::pair{"docs", "url-capability-denied"}}) {
    const auto* denied_action =
        interaction(rendered, denied_service.first, "press");
    if (denied_action == nullptr ||
        !denied->dispatch(denied_action->action, "null", rendered, &error) ||
        !denied->wait_for_idle(32, rendered, pump, &error) ||
        !contains_text(rendered.scene.root, denied_service.second)) {
      std::string texts;
      append_text(rendered.scene.root, texts);
      std::cerr << "fixture denied service did not fail closed for "
                << denied_service.first << ": " << error
                << " tree=" << texts << '\n';
      return 1;
    }
  }

  const auto *mapped = interaction(rendered, "mapped-error", "press");
  if (mapped == nullptr ||
      application->dispatch(mapped->action, "null", rendered, &error) ||
      error.find("\"code\":\"VELLUM_RUNTIME_EXCEPTION\"") ==
          std::string::npos ||
      error.find("vellum:\\/\\/app\\/src\\/App.tsx") == std::string::npos) {
    std::cerr << "fixture source-mapped error proof failed: " << error << '\n';
    return 1;
  }
  std::cout
      << R"({"fixture":"authoring-phase3/src/App.tsx","runtime":"native-javascriptcore","status":"passed"})"
      << '\n';
  return 0;
}
