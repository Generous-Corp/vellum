#include "rendered_tree_materializer.hpp"

#include <cmath>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

bool near(float left, float right) {
    return std::abs(left - right) < 0.0001F;
}

std::string wrap_tree(std::string_view children) {
    return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
      "tree":{"type":"stack","id":"compositing-root","style":{"width":200,"height":200},
      "children":[)JSON") + std::string(children) + "]}}";
}

// Transform, opacity, and per-corner radius keys reach the scene, and invalid
// values fail materialization with a message naming the key.
bool compositing_keys() {
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto tree = wrap_tree(R"JSON(
      {"type":"view","id":"turned","style":{"width":40,"height":20,
        "backgroundColor":"#FF0000","transform":"rotate(90deg)","opacity":0.5,
        "borderRadius":3,"borderTopRightRadius":8,"borderBottomLeftRadius":-2},
        "children":[]},
      {"type":"view","id":"moved","style":{"width":10,"height":10,
        "backgroundColor":"#00FF00",
        "transform":"translate(4px, -2px) scale(2) skewX(0) matrix(1,0,0,1,0,0)",
        "opacity":7},"children":[]},
      {"type":"view","id":"identity","style":{"width":10,"height":10,
        "backgroundColor":"#0000FF","transform":"rotate(0) translate(0, 0)",
        "opacity":-1},"children":[]},
      {"type":"text","id":"label","text":"Hi","style":{"height":20,
        "transform":"none","opacity":0.25,"borderTopLeftRadius":4},"children":[]},
      {"type":"button","id":"spun","events":{"press":"spun:press"},
        "style":{"x":100,"y":100,"width":40,"height":20,"transform":"rotate(90deg)"},
        "children":[]}
    )JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "compositing keys: " << error << '\n';
        return false;
    }
    const auto* turned = vellum::graphics::find_node(rendered.scene, "turned");
    const auto* moved = vellum::graphics::find_node(rendered.scene, "moved");
    const auto* identity = vellum::graphics::find_node(rendered.scene, "identity");
    const auto* label = vellum::graphics::find_node(rendered.scene, "label");
    if (turned == nullptr || moved == nullptr || identity == nullptr || label == nullptr ||
        !turned->transform || !moved->transform || identity->transform ||
        label->transform) {
        std::cerr << "compositing keys: transforms were not lowered as expected\n";
        return false;
    }
    // rotate(90deg) about the centre (20, 10) of a 40 by 20 box: the origin
    // goes to (30, -10) and the far corner (40, 20) to (10, 30).
    const auto origin = vellum::graphics::apply(*turned->transform, {0.0F, 0.0F});
    const auto corner = vellum::graphics::apply(*turned->transform, {40.0F, 20.0F});
    // translate then scale 2, about the centre (5, 5): the origin goes to
    // (5 - 10 + 4, 5 - 10 - 2).
    const auto moved_origin = vellum::graphics::apply(*moved->transform, {0.0F, 0.0F});
    if (!near(origin.x, 30.0F) || !near(origin.y, -10.0F) ||
        !near(corner.x, 10.0F) || !near(corner.y, 30.0F) ||
        !near(moved_origin.x, -1.0F) || !near(moved_origin.y, -7.0F) ||
        !near(moved->transform->a, 2.0F) || !near(moved->transform->d, 2.0F)) {
        std::cerr << "compositing keys: transform geometry is wrong\n";
        return false;
    }
    if (turned->opacity != 0.5F || moved->opacity != 1.0F || identity->opacity != 0.0F ||
        label->opacity != 0.25F || !turned->corner_radii ||
        turned->corner_radii->top_left != 3.0F || turned->corner_radii->top_right != 8.0F ||
        turned->corner_radii->bottom_right != 3.0F ||
        turned->corner_radii->bottom_left != 0.0F || moved->corner_radii) {
        std::cerr << "compositing keys: opacity or corner radii are wrong\n";
        return false;
    }
    // Hit-testing and accessibility boxes enclose the node as drawn: the
    // 40 by 20 button at (100, 100) turned about its centre (120, 110).
    const auto frame_box = [](const vellum::graphics::Rect& box) {
        return near(box.x, 110.0F) && near(box.y, 90.0F) && near(box.width, 20.0F) &&
               near(box.height, 40.0F);
    };
    if (rendered.interactions.size() != 1U || rendered.interactions[0].node_id != "spun" ||
        !frame_box(rendered.interactions[0].bounds) ||
        rendered.accessibility_nodes.empty() ||
        rendered.accessibility_nodes.back().node_id != "spun" ||
        !frame_box(rendered.accessibility_nodes.back().bounds)) {
        std::cerr << "compositing keys: interaction bounds ignore the transform\n";
        return false;
    }
    // The corner key is dropped on a text node without a painted background,
    // exactly as borderRadius is; transform and opacity are consumed.
    if (rendered.diagnostics.size() != 1U ||
        rendered.diagnostics[0].node_id != "label" ||
        rendered.diagnostics[0].property != "borderTopLeftRadius" ||
        rendered.diagnostics[0].code != vellum::graphics::kStyleDroppedCode) {
        std::cerr << "compositing keys: unexpected diagnostics\n";
        return false;
    }

    const auto refuses = [&](std::string_view style, std::string_view expected) {
        vellum::authoring::RenderedApplication candidate;
        std::string why;
        const auto source = wrap_tree(
            std::string(R"JSON({"type":"view","id":"bad","style":{"width":10,"height":10,)JSON") +
            std::string(style) + "},\"children\":[]}");
        if (vellum::authoring::materialize_rendered_tree(source, candidate, &why) ||
            why.find(expected) == std::string::npos) {
            std::cerr << "compositing keys: " << style << " gave '" << why << "'\n";
            return false;
        }
        return true;
    };
    return refuses(R"S("transform":"rotate(45)")S", "rotate(45) is not supported") &&
           refuses(R"S("transform":"spin(1turn)")S", "spin(1turn) is not supported") &&
           refuses(R"S("transform":"translate(1em)")S", "is not supported") &&
           refuses(R"S("transform":"scale(1, 2, 3)")S", "is not supported") &&
           refuses(R"S("transform":"rotate(45deg")S", "at most 32 CSS transform functions") &&
           refuses(R"S("transform":4)S", "transform must be a string") &&
           refuses(R"S("transform":"scale(1e30) scale(1e30)")S", "finite matrix") &&
           refuses(R"S("opacity":"half")S", "opacity must be a finite number") &&
           refuses(R"S("borderBottomRightRadius":true)S",
               "borderBottomRightRadius must be a finite number");
}

constexpr std::string_view kRenderedTree = R"JSON({
  "protocol": "vellum.authoring-host.v2",
  "kind": "render-result",
  "revision": 4,
  "tree": {
    "type": "stack",
    "id": "screen",
    "accessibilityLabel": "Test screen",
    "style": {
      "width": 320,
      "height": 180,
      "backgroundColor": "#0F172A",
      "padding": 12,
      "gap": 8
    },
    "children": [
      {
        "type": "button",
        "id": "increment",
        "events": {"press": "counter:increment"},
        "style": {"width": 140, "height": 44},
        "children": [
          {
            "type": "text-run",
            "id": "increment/text",
            "text": "Increment",
            "children": []
          }
        ]
      },
      {
        "type": "custom",
        "id": "meter",
        "component": "level-meter",
        "properties": {"value": 0.75},
        "style": {"width": 200, "height": 40},
        "children": []
      }
    ]
  }
})JSON";

constexpr std::string_view kImplicitLayoutTree = R"JSON({
  "protocol": "vellum.authoring-host.v2",
  "kind": "render-result",
  "revision": 1,
  "tree": {
    "type": "stack",
    "id": "layout-root",
    "style": {"width": 300, "height": 100, "direction": "horizontal"},
    "children": [
      {
        "type": "button",
        "id": "default-button",
        "style": {"width": 60},
        "children": []
      },
      {
        "type": "text-input",
        "id": "default-input",
        "primitiveVersion": 1,
        "value": "",
        "events": {"change": "input:change"},
        "style": {"width": 60},
        "children": []
      },
      {
        "type": "view",
        "id": "default-generic",
        "style": {"width": 60},
        "children": []
      }
    ]
  }
})JSON";

constexpr std::string_view kStyledTree = R"JSON({
  "protocol": "vellum.authoring-host.v2",
  "kind": "render-result",
  "revision": 1,
  "tree": {
    "type": "stack",
    "id": "styled-root",
    "style": {"width": 320, "height": 180},
    "children": [
      {
        "type": "view",
        "id": "gradient-card",
        "style": {
          "width": 240,
          "height": 100,
          "borderRadius": 12,
          "backgroundLinearGradient": {
            "angle": 135,
            "repeating": true,
            "repeatLength": 40,
            "stops": [
              {"position": 0, "color": "#0F172A"},
              {"position": 1, "color": "#14B8A6"}
            ]
          },
          "boxShadow": {
            "offsetX": 4,
            "offsetY": 6,
            "blurRadius": 10,
            "spreadRadius": 3,
            "color": "#00000066"
          }
        },
        "children": []
      },
      {
        "type": "text",
        "id": "attributed",
        "style": {"width": 280, "fontFamily": "Jost", "fontWeight": 400},
        "children": [
          {"type": "text-run", "id": "attributed/regular", "text": "Regular ",
           "style": {"color": "#111827"}, "children": []},
          {"type": "text-run", "id": "attributed/accent", "text": "Accent",
           "style": {"fontWeight": 600, "fontSize": 30, "letterSpacing": 1.5,
                     "color": "#14B8A6",
                     "textDecoration": "underline line-through"}, "children": []}
        ]
      },
      {
        "type": "view",
        "id": "shadow-only",
        "style": {"width": 40, "height": 30,
          "boxShadow": {"spreadRadius": 2, "color": "#00000066"}},
        "children": []
      }
    ]
  }
})JSON";

}  // namespace

int main() {
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(
            kRenderedTree, rendered, &error)) {
        std::cerr << error << '\n';
        return 1;
    }

    const auto* custom = vellum::graphics::find_node(rendered.scene, "meter");
    if (rendered.scene.width != 320.0F ||
        rendered.scene.height != 180.0F ||
        rendered.interactions.size() != 1U ||
        rendered.interactions[0].node_id != "increment" ||
        rendered.interactions[0].action != "counter:increment" ||
        rendered.accessibility_nodes.size() != 2U ||
        custom == nullptr ||
        custom->kind != vellum::graphics::SceneNode::Kind::custom ||
        custom->custom_component != "level-meter") {
        std::cerr << "representative rendered tree was not materialized\n";
        return 1;
    }

    const float original_width = rendered.scene.width;
    if (vellum::authoring::materialize_rendered_tree(
            R"({"protocol":"unknown","tree":{}})", rendered, &error) ||
        error != "JavaScript authoring bridge protocol mismatch" ||
        rendered.scene.width != original_width) {
        std::cerr << "invalid envelope did not fail atomically\n";
        return 1;
    }

    if (!vellum::authoring::materialize_rendered_tree(
            kImplicitLayoutTree, rendered, &error)) {
        std::cerr << error << '\n';
        return 1;
    }
    const auto* button = vellum::graphics::find_node(rendered.scene, "default-button");
    const auto* input = vellum::graphics::find_node(rendered.scene, "default-input");
    const auto* generic = vellum::graphics::find_node(rendered.scene, "default-generic");
    if (button == nullptr || input == nullptr || generic == nullptr ||
        button->bounds.height != 44.0F ||
        input->bounds.height != 44.0F ||
        generic->bounds.height != 0.0F) {
        std::cerr << "implicit layout defaults diverged from the retained-tree contract\n";
        return 1;
    }

    if (!vellum::authoring::materialize_rendered_tree(kStyledTree, rendered, &error)) {
        std::cerr << error << '\n';
        return 1;
    }
    const auto* card = vellum::graphics::find_node(rendered.scene, "gradient-card");
    const auto* attributed = vellum::graphics::find_node(rendered.scene, "attributed");
    const auto* shadow_only = vellum::graphics::find_node(rendered.scene, "shadow-only");
    if (card == nullptr || card->kind != vellum::graphics::SceneNode::Kind::rectangle ||
        card->fill_gradients.size() != 1U ||
        !card->fill_gradients[0].repeating ||
        card->fill_gradients[0].repeat_length != 40.0F ||
        card->box_shadows.size() != 1U ||
        card->box_shadows[0].spread_radius != 3.0F || attributed == nullptr ||
        attributed->text_runs.size() != 2U ||
        attributed->text_runs[1].style.font_weight != 600 ||
        attributed->bounds.height != 42.0F ||
        attributed->text_runs[1].style.letter_spacing != 1.5F ||
        !attributed->text_runs[1].style.underline ||
        !attributed->text_runs[1].style.strikethrough || shadow_only == nullptr ||
        shadow_only->bounds.y != 142.0F ||
        shadow_only->kind != vellum::graphics::SceneNode::Kind::rectangle) {
        std::cerr << "styled tree did not preserve native paint and text runs\n";
        return 1;
    }

    const auto styled_width = rendered.scene.width;
    constexpr std::string_view kMalformedGradient = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"bad-gradient",
        "style":{"width":100,"height":100,"backgroundLinearGradient":{
          "repeating":1,"stops":[
            {"position":0,"color":"#000000"},{"position":1,"color":"#ffffff"}
          ]}},"children":[]}}
    )JSON";
    if (vellum::authoring::materialize_rendered_tree(
            kMalformedGradient, rendered, &error) ||
        error != "backgroundLinearGradient.repeating must be boolean" ||
        rendered.scene.width != styled_width) {
        std::cerr << "malformed gradient did not fail atomically\n";
        return 1;
    }

    constexpr std::string_view kMalformedShadow = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"bad-shadow",
        "style":{"width":100,"height":100,"boxShadow":{
          "color":"#000000","blurRadius":"wide"
        }},"children":[]}}
    )JSON";
    if (vellum::authoring::materialize_rendered_tree(
            kMalformedShadow, rendered, &error) ||
        error != "boxShadow.blurRadius must be a finite number" ||
        rendered.scene.width != styled_width) {
        std::cerr << "malformed shadow did not fail atomically\n";
        return 1;
    }

    constexpr std::string_view kBooleanShadow = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"boolean-shadow",
        "style":{"width":100,"height":100,"boxShadow":{
          "color":"#000000","blurRadius":true
        }},"children":[]}}
    )JSON";
    if (vellum::authoring::materialize_rendered_tree(
            kBooleanShadow, rendered, &error) ||
        error != "boxShadow.blurRadius must be a finite number" ||
        rendered.scene.width != styled_width) {
        std::cerr << "boolean shadow radius did not fail atomically\n";
        return 1;
    }

    constexpr std::string_view kAmbiguousText = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"text","id":"ambiguous-text","text":"Parent",
        "style":{"width":100,"height":30},
        "children":[{"type":"text-run","id":"child","text":"Child","children":[]}]}}
    )JSON";
    if (vellum::authoring::materialize_rendered_tree(
            kAmbiguousText, rendered, &error) ||
        error != "text authoring nodes accept text or attributed children, not both" ||
        rendered.scene.width != styled_width) {
        std::cerr << "ambiguous parent text did not fail atomically\n";
        return 1;
    }

    std::string excessive_stops = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"too-many-stops",
        "style":{"width":100,"height":100,"backgroundLinearGradient":{"stops":[
    )JSON";
    for (int index = 0; index < 65; ++index) {
        if (index != 0) excessive_stops += ',';
        excessive_stops += "{\"position\":" +
            std::to_string(static_cast<double>(index) / 64.0) +
            ",\"color\":\"#000000\"}";
    }
    excessive_stops += R"JSON(]}},"children":[]}})JSON";
    if (vellum::authoring::materialize_rendered_tree(
            excessive_stops, rendered, &error) ||
        error != "backgroundLinearGradient exceeds the 64-stop limit" ||
        rendered.scene.width != styled_width) {
        std::cerr << "excessive gradient stops did not fail atomically\n";
        return 1;
    }

    using vellum::graphics::LoweringDiagnostic;
    static_assert(vellum::graphics::kUnknownNodeTypeCode == "VELLUM_UNKNOWN_NODE_TYPE");
    static_assert(vellum::graphics::kStyleDroppedCode == "VELLUM_STYLE_DROPPED");
    static_assert(vellum::graphics::kNodeContentDroppedCode == "VELLUM_NODE_CONTENT_DROPPED");
    std::vector<LoweringDiagnostic> failure_diagnostics;
    if (!vellum::authoring::materialize_rendered_tree(
            kRenderedTree, rendered, &error, &failure_diagnostics) ||
        !rendered.diagnostics.empty() || !failure_diagnostics.empty() ||
        rendered.scene.scene_version != vellum::graphics::kSceneVersion) {
        std::cerr << "known-good tree reported lowering diagnostics\n";
        return 1;
    }

    const auto known_good_width = rendered.scene.width;
    constexpr std::string_view kUnknownType = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"unknown-root","style":{"width":100,"height":100},
        "children":[
          {"type":"view","id":"before","style":{"width":10,"height":10,"zIndex":1},
            "children":[]},
          {"type":"hologram","id":"future-node",
            "style":{"width":10,"height":10},"children":[]}]}}
    )JSON";
    if (vellum::authoring::materialize_rendered_tree(
            kUnknownType, rendered, &error, &failure_diagnostics) ||
        error != "authoring node future-node has unsupported type hologram" ||
        rendered.scene.width != known_good_width ||
        !rendered.diagnostics.empty() ||
        failure_diagnostics.size() != 2U ||
        failure_diagnostics[0].severity != LoweringDiagnostic::Severity::warning ||
        failure_diagnostics[0].code != vellum::graphics::kStyleDroppedCode ||
        failure_diagnostics[0].node_id != "before" ||
        failure_diagnostics[0].property != "zIndex" ||
        failure_diagnostics[1].severity != LoweringDiagnostic::Severity::error ||
        failure_diagnostics[1].disposition != LoweringDiagnostic::Disposition::unsupported ||
        failure_diagnostics[1].code != vellum::graphics::kUnknownNodeTypeCode ||
        failure_diagnostics[1].node_id != "future-node" ||
        failure_diagnostics[1].property != "type" ||
        failure_diagnostics[1].message != error ||
        vellum::graphics::find_node(rendered.scene, "future-node") != nullptr) {
        std::cerr << "unknown node type did not fail closed with a diagnostic\n";
        return 1;
    }

    constexpr std::string_view kDroppedStyle = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"dropped-root","style":{"width":200,"height":100},
        "children":[
          {"type":"view","id":"panel","style":{"width":50,"height":20,
            "backgroundColor":"#112233","zIndex":2,"gap":4},"children":[]},
          {"type":"text","id":"caption","text":"Hi",
            "style":{"height":20,"backgroundColor":"#FF0000","color":"#000000"},
            "children":[]},
          {"type":"image","id":"logo","style":{"width":10,"height":10},"children":[]}
        ]}}
    )JSON";
    if (!vellum::authoring::materialize_rendered_tree(
            kDroppedStyle, rendered, &error, &failure_diagnostics)) {
        std::cerr << error << '\n';
        return 1;
    }
    const auto* panel = vellum::graphics::find_node(rendered.scene, "panel");
    const auto* caption = vellum::graphics::find_node(rendered.scene, "caption");
    const auto expect = [&](std::size_t index, std::string_view node_id,
                            std::string_view code, std::string_view property,
                            LoweringDiagnostic::Disposition disposition) {
        const auto& diagnostic = rendered.diagnostics[index];
        return diagnostic.severity == LoweringDiagnostic::Severity::warning &&
               diagnostic.disposition == disposition && diagnostic.code == code &&
               diagnostic.node_id == node_id && diagnostic.property == property &&
               !diagnostic.message.empty();
    };
    if (panel == nullptr || panel->kind != vellum::graphics::SceneNode::Kind::rectangle ||
        caption == nullptr || caption->kind != vellum::graphics::SceneNode::Kind::text ||
        caption->text != "Hi" || !failure_diagnostics.empty() ||
        rendered.diagnostics.size() != 4U ||
        !expect(0U, "panel", vellum::graphics::kStyleDroppedCode, "gap",
            LoweringDiagnostic::Disposition::unsupported) ||
        !expect(1U, "panel", vellum::graphics::kStyleDroppedCode, "zIndex",
            LoweringDiagnostic::Disposition::unsupported) ||
        !expect(2U, "caption", vellum::graphics::kStyleDroppedCode, "backgroundColor",
            LoweringDiagnostic::Disposition::unsupported) ||
        !expect(3U, "logo", vellum::graphics::kNodeContentDroppedCode, "type",
            LoweringDiagnostic::Disposition::lowered) ||
        vellum::graphics::to_string(rendered.diagnostics[2].disposition) != "unsupported" ||
        vellum::graphics::to_string(rendered.diagnostics[3].disposition) != "lowered" ||
        vellum::graphics::to_string(rendered.diagnostics[0].severity) != "warning") {
        std::cerr << "dropped style was not reported as a lowering warning\n";
        return 1;
    }

    constexpr std::string_view kInertStyle = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"inert-root",
        "style":{"width":200,"height":120,"x":5,"y":5,"gap":2},
        "children":[
          {"type":"view","id":"leaf",
            "style":{"width":10,"height":10,"direction":"horizontal","padding":3},
            "children":[]},
          {"type":"text","id":"plain","text":"A",
            "style":{"height":20,"borderRadius":4,"padding":2},"children":[]},
          {"type":"text","id":"shaded","text":"B",
            "style":{"height":20,"borderRadius":4,"boxShadow":{"color":"#000000"}},
            "children":[]},
          {"type":"button","id":"press","style":{"width":40,"height":20},
            "children":[{"type":"text-run","id":"press/run","text":"Go",
              "style":{"color":"#FFFFFF","fontSize":18},"children":[]}]},
          {"type":"canvas","id":"sketch","style":{"width":10,"height":10},"children":[]}
        ]}}
    )JSON";
    if (!vellum::authoring::materialize_rendered_tree(kInertStyle, rendered, &error)) {
        std::cerr << error << '\n';
        return 1;
    }
    const auto* shaded = vellum::graphics::find_node(rendered.scene, "shaded");
    const auto* sketch = vellum::graphics::find_node(rendered.scene, "sketch");
    const auto unsupported = LoweringDiagnostic::Disposition::unsupported;
    if (shaded == nullptr || shaded->corner_radius != 4.0F || sketch == nullptr ||
        rendered.diagnostics.size() != 9U ||
        !expect(0U, "inert-root", vellum::graphics::kStyleDroppedCode, "x", unsupported) ||
        !expect(1U, "inert-root", vellum::graphics::kStyleDroppedCode, "y", unsupported) ||
        !expect(2U, "leaf", vellum::graphics::kStyleDroppedCode, "direction", unsupported) ||
        !expect(3U, "leaf", vellum::graphics::kStyleDroppedCode, "padding", unsupported) ||
        !expect(4U, "plain", vellum::graphics::kStyleDroppedCode, "borderRadius", unsupported) ||
        !expect(5U, "plain", vellum::graphics::kStyleDroppedCode, "padding", unsupported) ||
        !expect(6U, "press/run", vellum::graphics::kStyleDroppedCode, "color", unsupported) ||
        !expect(7U, "press/run", vellum::graphics::kStyleDroppedCode, "fontSize", unsupported) ||
        !expect(8U, "sketch", vellum::graphics::kNodeContentDroppedCode, "type",
            LoweringDiagnostic::Disposition::lowered)) {
        std::cerr << "inert layout, text and button-run style was not reported\n";
        return 1;
    }

    return compositing_keys() ? 0 : 1;
}
