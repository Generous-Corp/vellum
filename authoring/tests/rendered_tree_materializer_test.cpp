#include "rendered_tree_materializer.hpp"

#include <iostream>
#include <string>

namespace {

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

    return 0;
}
