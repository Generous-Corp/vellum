#include "rendered_tree_materializer.hpp"

#include <vellum/graphics/paint_command.hpp>

#include <algorithm>
#include <clocale>
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

// rotate(90deg) on a 40 by 20 box at (10, 20) inside a parent at (30, 40):
// the absolute box (40, 60, 40, 20) turns about its own centre (60, 70) once,
// to (50, 50, 20, 40). A second pivot would move it.
bool transform_pivots_once() {
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto tree = wrap_tree(R"JSON(
      {"type":"view","id":"frame","style":{"x":30,"y":40,"width":100,"height":100},
        "children":[
          {"type":"view","id":"spinner","events":{"press":"spinner:press"},
            "style":{"x":10,"y":20,"width":40,"height":20,
              "backgroundColor":"#FF0000","transform":"rotate(90deg)"},
            "children":[]}]}
    )JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "pivot: " << error << '\n';
        return false;
    }
    const auto commands = vellum::graphics::make_paint_commands(rendered.scene);
    const auto expected = [](const vellum::graphics::Rect& box) {
        return near(box.x, 50.0F) && near(box.y, 50.0F) && near(box.width, 20.0F) &&
               near(box.height, 40.0F);
    };
    const auto spinner = std::find_if(commands.begin(), commands.end(),
        [](const auto& command) { return command.node_id == "spinner"; });
    if (spinner == commands.end() ||
        !near(spinner->bounds.x, 40.0F) || !near(spinner->bounds.y, 60.0F) ||
        !expected(vellum::graphics::transformed_bounds(spinner->transform, spinner->bounds)) ||
        rendered.interactions.size() != 1U || !expected(rendered.interactions[0].bounds)) {
        std::cerr << "pivot: the rotated box is not where one pivot puts it\n";
        return false;
    }
    return true;
}

// Transform arguments parse the same under a comma-decimal locale.
bool transform_ignores_locale() {
    const std::string previous = std::setlocale(LC_NUMERIC, nullptr);
    const char* comma = std::setlocale(LC_NUMERIC, "de_DE.UTF-8");
    if (comma == nullptr) comma = std::setlocale(LC_NUMERIC, "fr_FR.UTF-8");
    if (comma == nullptr || std::localeconv()->decimal_point[0] != ',') {
        std::setlocale(LC_NUMERIC, previous.c_str());
        std::cerr << "locale: no comma-decimal locale is installed\n";
        return false;
    }
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const bool materialized = vellum::authoring::materialize_rendered_tree(
        wrap_tree(R"JSON({"type":"view","id":"scaled","style":{"width":10,"height":10,
          "transform":"scale(1.5) translate(+2.5px, 0)"},"children":[]})JSON"),
        rendered, &error);
    std::setlocale(LC_NUMERIC, previous.c_str());
    const auto* scaled = vellum::graphics::find_node(rendered.scene, "scaled");
    if (!materialized || scaled == nullptr || !scaled->transform ||
        !near(scaled->transform->a, 1.5F) || !rendered.diagnostics.empty()) {
        std::cerr << "locale: transform parsing changed under a comma locale: " << error << '\n';
        return false;
    }
    return true;
}

// Transform, opacity, and per-corner radius keys reach the scene; an
// unparseable transform is dropped with a warning and a non-finite one fails.
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
        "children":[]},
      {"type":"view","id":"percent","style":{"width":10,"height":20,
        "transform":"TRANSLATE(50%, -10%) Rotate(0DEG) translateY(25%)"},"children":[]},
      {"type":"button","id":"flat","events":{"press":"flat:press"},
        "style":{"width":40,"height":20,"transform":"scale(0)"},"children":[]},
      {"type":"text-input","id":"flat-input","primitiveVersion":1,"value":"x",
        "events":{"change":"flat:change"},
        "style":{"width":40,"height":20,"transform":"scaleX(0)"},"children":[]},
      {"type":"text","id":"runs","children":[
        {"type":"text-run","id":"runs/faded","text":"A","style":{"opacity":0.5},
          "children":[]}]}
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
    const auto origin = vellum::graphics::apply(*turned->transform, {0.0F, 0.0F});
    const auto corner = vellum::graphics::apply(*turned->transform, {40.0F, 20.0F});
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
    const auto frame_box = [](const vellum::graphics::Rect& box) {
        return near(box.x, 110.0F) && near(box.y, 90.0F) && near(box.width, 20.0F) &&
               near(box.height, 40.0F);
    };
    const auto* percent = vellum::graphics::find_node(rendered.scene, "percent");
    if (percent == nullptr || !percent->transform || !near(percent->transform->tx, 5.0F) ||
        !near(percent->transform->ty, 3.0F) || !near(percent->transform->a, 1.0F)) {
        std::cerr << "compositing keys: percentage or mixed-case transforms are wrong\n";
        return false;
    }
    const auto has_accessibility = [&](std::string_view id) {
        for (const auto& node : rendered.accessibility_nodes) {
            if (node.node_id == id) return true;
        }
        return false;
    };
    if (!has_accessibility("flat") || !has_accessibility("flat-input") ||
        !rendered.text_inputs.empty()) {
        std::cerr << "compositing keys: a non-invertible control kept a hit box\n";
        return false;
    }
    if (rendered.interactions.size() != 1U || rendered.interactions[0].node_id != "spun" ||
        !frame_box(rendered.interactions[0].bounds) ||
        std::none_of(rendered.accessibility_nodes.begin(), rendered.accessibility_nodes.end(),
            [&](const auto& node) { return node.node_id == "spun" && frame_box(node.bounds); })) {
        std::cerr << "compositing keys: interaction bounds ignore the transform\n";
        return false;
    }
    if (rendered.diagnostics.size() != 2U ||
        rendered.diagnostics[0].node_id != "label" ||
        rendered.diagnostics[0].property != "borderTopLeftRadius" ||
        rendered.diagnostics[0].code != vellum::graphics::kStyleDroppedCode ||
        rendered.diagnostics[1].node_id != "runs/faded" ||
        rendered.diagnostics[1].property != "opacity" ||
        rendered.diagnostics[1].code != vellum::graphics::kStyleDroppedCode) {
        std::cerr << "compositing keys: unexpected diagnostics\n";
        for (const auto& diagnostic : rendered.diagnostics) {
            std::cerr << "  " << diagnostic.node_id << ' ' << diagnostic.property << '\n';
        }
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
    const auto drops = [&](std::string_view style, std::string_view expected) {
        vellum::authoring::RenderedApplication candidate;
        std::string why;
        const auto source = wrap_tree(
            std::string(R"JSON({"type":"view","id":"bad","style":{"width":10,"height":10,)JSON") +
            std::string(style) + "},\"children\":[]}");
        if (!vellum::authoring::materialize_rendered_tree(source, candidate, &why)) {
            std::cerr << "compositing keys: " << style << " failed: " << why << '\n';
            return false;
        }
        const auto* bad = vellum::graphics::find_node(candidate.scene, "bad");
        if (bad == nullptr || bad->transform || candidate.diagnostics.size() != 1U ||
            candidate.diagnostics[0].property != "transform" ||
            candidate.diagnostics[0].code != vellum::graphics::kStyleDroppedCode ||
            candidate.diagnostics[0].message.find(expected) == std::string::npos) {
            std::cerr << "compositing keys: " << style << " was not dropped with '"
                      << expected << "'\n";
            return false;
        }
        return true;
    };
    return drops(R"S("transform":"rotate(45)")S", "rotate(45) is not supported") &&
           drops(R"S("transform":"spin(1turn)")S", "spin(1turn) is not supported") &&
           drops(R"S("transform":"translate(1em)")S", "is not supported") &&
           drops(R"S("transform":"scale(1, 2, 3)")S", "is not supported") &&
           drops(R"S("transform":"rotate(45deg")S", "not a function call: rotate(45deg") &&
           drops(R"S("transform":"rotate(0) garbage")S", "not a function call: garbage") &&
           drops(R"S("transform":"rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)rotate(0)")S",
               "more than 32 functions") &&
           drops(R"S("transform":4)S", "not a string") &&
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

bool path_nodes() {
    using vellum::graphics::LoweringDiagnostic;
    using vellum::graphics::PathVerb;
    using vellum::graphics::SceneNode;
    constexpr std::string_view kGoodPath = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"art","style":{"width":200,"height":120},
        "children":[
          {"type":"path","id":"star","d":"M10 0 l10 30 h-20 z",
            "fillRule":"evenodd","stroke":"#112233","strokeWidth":3,
            "strokeLineJoin":"round","strokeLineCap":"square","strokeMiterLimit":2,
            "strokeDasharray":[4,2],"strokeDashoffset":1,
            "strokeOpacity":0.5,"fillOpacity":1,"text":"ignored",
            "style":{"width":40,"height":40,"backgroundColor":"#FF0000",
              "boxShadow":{"color":"#000000"},"borderRadius":3},
            "children":[]},
          {"type":"path","id":"wave","d":"M0 0 Q5 10 10 0 T20 0",
            "fill":{"angle":90,"stops":[{"position":0,"color":"#FF0000"},
              {"position":1,"color":"#0000FF"}]},
            "style":{"width":20,"height":20,"opacity":0.5,"transform":"rotate(90deg)"}},
          {"type":"path","id":"outline","d":"M0 0 L5 5","fill":"none",
            "style":{"height":10}}
        ]}}
    )JSON";
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    std::vector<LoweringDiagnostic> failure_diagnostics;
    if (!vellum::authoring::materialize_rendered_tree(
            kGoodPath, rendered, &error, &failure_diagnostics)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto* star = vellum::graphics::find_node(rendered.scene, "star");
    const auto* wave = vellum::graphics::find_node(rendered.scene, "wave");
    const auto* outline = vellum::graphics::find_node(rendered.scene, "outline");
    if (star == nullptr || star->kind != SceneNode::Kind::path ||
        star->path->verbs != std::vector<PathVerb>{
            PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close} ||
        star->path->values != std::vector<float>{10, 0, 20, 30, 0, 30} ||
        star->path->fill_rule != vellum::graphics::FillRule::evenodd ||
        star->fill.red != 0.0F || star->fill.alpha != 1.0F ||
        !star->fill_gradients.empty() || !star->box_shadows.empty() ||
        star->corner_radius != 0.0F ||
        star->path->stroke.color.blue != static_cast<float>(0x33) / 255.0F ||
        star->path->stroke.width != 3.0F ||
        star->path->stroke.join != vellum::graphics::StrokeJoin::round ||
        star->path->stroke.cap != vellum::graphics::StrokeCap::square ||
        star->path->stroke.miter_limit != 2.0F ||
        star->path->stroke.dash_array != std::vector<float>{4, 2} ||
        star->path->stroke.dash_offset != 1.0F ||
        star->bounds.width != 40.0F || star->bounds.height != 40.0F) {
        std::cerr << "a good path was not materialized\n";
        return false;
    }
    if (wave == nullptr || wave->kind != SceneNode::Kind::path ||
        wave->path->verbs.size() != 3U || wave->fill.alpha != 0.0F ||
        wave->fill_gradients.size() != 1U || wave->fill_gradients[0].angle_degrees != 90.0F ||
        wave->path->stroke.color.alpha != 0.0F || wave->path->stroke.width != 1.0F ||
        wave->opacity != 0.5F || !wave->transform ||
        outline == nullptr || outline->fill.alpha != 0.0F ||
        outline->path->fill_rule != vellum::graphics::FillRule::nonzero) {
        std::cerr << "path paint defaults or gradient fill were not materialized\n";
        return false;
    }
    const auto dropped = [&](std::size_t index, std::string_view property) {
        const auto& diagnostic = rendered.diagnostics[index];
        return diagnostic.code == vellum::graphics::kStyleDroppedCode &&
               diagnostic.severity == LoweringDiagnostic::Severity::warning &&
               diagnostic.node_id == "star" && diagnostic.property == property;
    };
    if (rendered.diagnostics.size() != 6U || !dropped(0U, "backgroundColor") ||
        !dropped(1U, "borderRadius") || !dropped(2U, "boxShadow") ||
        !dropped(3U, "fillOpacity") || !dropped(4U, "strokeOpacity") || !dropped(5U, "text") ||
        rendered.diagnostics[4].message !=
            "attribute strokeOpacity is not supported on path nodes and was ignored" ||
        !failure_diagnostics.empty()) {
        std::cerr << "path style keys were not reported as dropped\n";
        return false;
    }

    const auto rejected = [&](std::string_view node, std::string_view expected_error,
                              std::string_view property) {
        const std::string tree =
            std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
              "tree":{"type":"stack","id":"art","style":{"width":100,"height":100},
              "children":[)JSON") + std::string(node) + "]}}";
        vellum::authoring::RenderedApplication kept = rendered;
        std::string why;
        std::vector<LoweringDiagnostic> diagnostics;
        const bool ok = !vellum::authoring::materialize_rendered_tree(
                            tree, kept, &why, &diagnostics) &&
                        why == expected_error && kept.scene.width == rendered.scene.width &&
                        diagnostics.size() == 1U &&
                        diagnostics[0].severity == LoweringDiagnostic::Severity::error &&
                        diagnostics[0].disposition ==
                            LoweringDiagnostic::Disposition::unsupported &&
                        diagnostics[0].code == vellum::graphics::kInvalidPathCode &&
                        diagnostics[0].node_id == "bad" && diagnostics[0].property == property &&
                        diagnostics[0].message == why;
        if (!ok) std::cerr << "path was not rejected as expected: " << why << '\n';
        return ok;
    };
    static_assert(vellum::graphics::kInvalidPathCode == "VELLUM_INVALID_PATH");
    return rejected(R"({"type":"path","id":"bad","d":"M0 0 L1"})",
                    "path bad has invalid path data: path data: expected a number at byte 7",
                    "d") &&
           rejected(R"({"type":"path","id":"bad","d":"Q1 1 2 2"})",
                    "path bad has invalid path data: path data must start with a move command",
                    "d") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0 K1 1"})",
                    "path bad has invalid path data: path data: unknown command 'K' at byte 5",
                    "d") &&
           rejected(R"({"type":"path","id":"bad"})", "path bad requires string path data in d",
                    "d") &&
           rejected(R"({"type":"path","id":"bad","d":7})",
                    "path bad requires string path data in d", "d") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","fillRule":"winding"})",
                    "path bad fillRule must be nonzero or evenodd", "fillRule") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","fill":"red"})",
                    "path bad fill must be a colour, none or a linear gradient", "fill") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","fill":{"angle":0,"stops":[]}})",
                    "path bad fill requires at least two stops", "fill") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","stroke":true})",
                    "path bad stroke must be a colour or none", "stroke") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeWidth":-1})",
                    "path bad strokeWidth must be a finite non-negative number", "strokeWidth") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeMiterLimit":0.5})",
                    "path bad strokeMiterLimit must be a finite number of at least 1",
                    "strokeMiterLimit") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeDashoffset":"1"})",
                    "path bad strokeDashoffset must be a finite number", "strokeDashoffset") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeLineJoin":"arcs"})",
                    "path bad strokeLineJoin must be miter, round or bevel", "strokeLineJoin") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeLineCap":"flat"})",
                    "path bad strokeLineCap must be butt, round or square", "strokeLineCap") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeDasharray":[1,-2]})",
                    "path bad strokeDasharray entries must be finite non-negative numbers",
                    "strokeDasharray") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","strokeDasharray":[0,0]})",
                    "path bad cannot be drawn: the stroke dash array sums to zero",
                    "strokeDasharray") &&
           rejected(
               R"({"type":"path","id":"bad","d":"M0 0 L100 0","strokeDasharray":[0.0001,0.0001]})",
               "path bad cannot be drawn: the stroke dash pattern would draw more than 100000 "
               "dashes",
               "strokeDasharray") &&
           rejected(R"({"type":"path","id":"bad","d":"M0 0","children":[
                      {"type":"view","id":"inner","children":[]}]})",
                    "path bad does not accept children", "children");
}

// overflow, clipPath, maskMode and isolation reach the scene; bad values fail
// atomically, and keys that cannot apply are reported.
bool grouping_keys() {
    using vellum::graphics::LoweringDiagnostic;
    using vellum::graphics::MaskMode;
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    std::vector<LoweringDiagnostic> failure_diagnostics;
    const auto tree = wrap_tree(R"JSON(
      {"type":"view","id":"window","style":{"width":40,"height":30,"overflow":"hidden",
        "borderRadius":4,"borderTopRightRadius":9,"isolation":"isolate"},
        "children":[
          {"type":"view","id":"window/content","style":{"width":40,"height":30,
            "backgroundColor":"#FF0000"},"children":[]},
          {"type":"view","id":"window/mask","style":{"width":20,"height":30,
            "backgroundColor":"#FFFFFF","maskMode":"luminance"},"children":[]}]},
      {"type":"text","id":"caption","text":"Hi","style":{"height":20,
        "overflow":"hidden","borderRadius":5},"children":[]},
      {"type":"view","id":"shaped","style":{"width":20,"height":20,"overflow":"hidden",
        "clipPath":"M0 0 h20 v20 z","isolation":"auto"},"children":[]},
      {"type":"view","id":"visible","style":{"width":20,"height":20,"overflow":"visible",
        "maskMode":"alpha"},"children":[]},
      {"type":"view","id":"clipped","style":{"width":20,"height":20,"overflow":"clip"},
        "children":[]},
      {"type":"view","id":"scroller","style":{"width":20,"height":20,"overflow":"scroll"},
        "children":[]}
    )JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error,
                                                      &failure_diagnostics)) {
        std::cerr << "grouping keys: " << error << '\n';
        return false;
    }
    const auto* window = vellum::graphics::find_node(rendered.scene, "window");
    const auto* mask = vellum::graphics::find_node(rendered.scene, "window/mask");
    const auto* caption = vellum::graphics::find_node(rendered.scene, "caption");
    const auto* shaped = vellum::graphics::find_node(rendered.scene, "shaped");
    const auto* visible = vellum::graphics::find_node(rendered.scene, "visible");
    if (window == nullptr || !window->clip || window->clip->path ||
        !near(window->clip->rect.width, 40.0F) || !near(window->clip->rect.height, 30.0F) ||
        !near(window->clip->radii.top_left, 4.0F) || !near(window->clip->radii.top_right, 9.0F) ||
        window->clip->clips_own_paint || !window->isolate || window->mask) {
        std::cerr << "grouping keys: overflow hidden or isolation did not reach the window\n";
        return false;
    }
    if (mask == nullptr || mask->mask != MaskMode::luminance || mask->clip ||
        visible == nullptr || visible->mask != MaskMode::alpha || visible->clip) {
        std::cerr << "grouping keys: maskMode did not reach the scene\n";
        return false;
    }
    if (caption == nullptr || !caption->clip || !near(caption->clip->radii.bottom_left, 5.0F)) {
        std::cerr << "grouping keys: a clipped text node lost its border radius\n";
        return false;
    }
    const auto* clipped = vellum::graphics::find_node(rendered.scene, "clipped");
    const auto* scroller = vellum::graphics::find_node(rendered.scene, "scroller");
    if (clipped == nullptr || !clipped->clip || scroller == nullptr || !scroller->clip) {
        std::cerr << "grouping keys: overflow clip or scroll did not clip\n";
        return false;
    }
    if (shaped == nullptr || !shaped->clip || !shaped->clip->path ||
        !shaped->clip->clips_own_paint || shaped->clip->path->verbs.size() != 4U ||
        shaped->isolate) {
        std::cerr << "grouping keys: clipPath did not reach the scene\n";
        return false;
    }
    const auto reported = [&](std::string_view node, std::string_view property) {
        return std::count_if(rendered.diagnostics.begin(), rendered.diagnostics.end(),
            [&](const LoweringDiagnostic& diagnostic) {
                return diagnostic.node_id == node && diagnostic.property == property &&
                       diagnostic.code == vellum::graphics::kStyleDroppedCode;
            });
    };
    if (rendered.diagnostics.size() != 2U || reported("shaped", "overflow") != 1 ||
        reported("scroller", "overflow") != 1) {
        std::cerr << "grouping keys: expected two overflow warnings, got "
                  << rendered.diagnostics.size() << " diagnostics\n";
        return false;
    }
    if (rendered.scene.scene_version != vellum::graphics::kSceneVersion ||
        !vellum::graphics::scene_fields_match_version(rendered.scene)) {
        std::cerr << "grouping keys: the scene does not carry its own version\n";
        return false;
    }
    const auto commands = vellum::graphics::make_paint_commands(rendered.scene);
    if (std::none_of(commands.begin(), commands.end(), [](const auto& command) {
            return command.kind == vellum::graphics::PaintCommand::Kind::begin_mask &&
                   command.node_id == "window/mask";
        })) {
        std::cerr << "grouping keys: the mask did not lower to a mask pair\n";
        return false;
    }

    // The root has no siblings to mask, so its maskMode is reported and dropped.
    constexpr std::string_view kRootMask = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"lonely","style":{"width":10,"height":10,"maskMode":"alpha"},
        "children":[]}}
    )JSON";
    if (!vellum::authoring::materialize_rendered_tree(kRootMask, rendered, &error) ||
        rendered.scene.root.mask || reported("lonely", "maskMode") != 1) {
        std::cerr << "grouping keys: a root maskMode was not dropped with a warning\n";
        return false;
    }

    const auto width_before = rendered.scene.width;
    const auto refuses = [&](std::string_view style, const std::string& expected,
                             bool path_diagnostic) {
        std::vector<LoweringDiagnostic> diagnostics;
        const auto bad = wrap_tree(std::string(R"JSON({"type":"view","id":"bad","style":{)JSON") +
                                   std::string(style) + R"JSON(},"children":[]})JSON");
        if (vellum::authoring::materialize_rendered_tree(bad, rendered, &error, &diagnostics) ||
            error.rfind(expected, 0) != 0 || rendered.scene.width != width_before) {
            std::cerr << "grouping keys: '" << style << "' gave '" << error << "'\n";
            return false;
        }
        const bool coded = std::any_of(diagnostics.begin(), diagnostics.end(),
            [](const LoweringDiagnostic& diagnostic) {
                return diagnostic.code == vellum::graphics::kInvalidPathCode &&
                       diagnostic.property == "clipPath" &&
                       diagnostic.severity == LoweringDiagnostic::Severity::error;
            });
        if (coded != path_diagnostic) {
            std::cerr << "grouping keys: '" << style << "' path diagnostic mismatch\n";
            return false;
        }
        return true;
    };
    return refuses(R"("overflow":"overlay")",
                   "node bad overflow must be visible, hidden, clip, auto or scroll", false) &&
           refuses(R"("maskMode":"inverted")", "node bad maskMode must be alpha or luminance",
                   false) &&
           refuses(R"("isolation":true)", "node bad isolation must be auto or isolate", false) &&
           refuses(R"("clipPath":7)", "node bad clipPath must be SVG path data", true) &&
           refuses(R"("clipPath":"M0 0 L")", "node bad has an invalid clipPath: ", true);
}

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

    error = "stale failure";
    if (!vellum::authoring::materialize_rendered_tree(kRenderedTree, rendered, &error) ||
        !error.empty()) {
        std::cerr << "a successful materialization kept a stale error\n";
        return 1;
    }

    const auto run_rejected = [&](std::string_view tree, const std::string& expected,
                                  std::string_view node_id, std::string_view property) {
        const auto width_before = rendered.scene.width;
        std::vector<LoweringDiagnostic> diagnostics;
        if (vellum::authoring::materialize_rendered_tree(tree, rendered, &error, &diagnostics) ||
            error != expected || rendered.scene.width != width_before ||
            diagnostics.empty()) {
            std::cerr << "unexpected text-run result: " << error << '\n';
            return false;
        }
        const auto& last = diagnostics.back();
        return last.severity == LoweringDiagnostic::Severity::error &&
               last.disposition == LoweringDiagnostic::Disposition::unsupported &&
               last.code == vellum::graphics::kInvalidTextRunCode &&
               last.node_id == node_id && last.property == property && last.message == error;
    };
    static_assert(vellum::graphics::kInvalidTextRunCode == "VELLUM_INVALID_TEXT_RUN");
    constexpr std::string_view kRunWithoutText = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"text","id":"label","style":{"width":100,"height":30},
        "children":[
          {"type":"text-run","id":"label/first","text":"Ok","children":[]},
          {"type":"text-run","id":"label/second","text":7,"children":[]}]}}
    )JSON";
    constexpr std::string_view kRunWithoutId = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"button","id":"go","style":{"width":100,"height":30},
        "children":[{"type":"text-run","text":"Go","children":[]}]}}
    )JSON";
    constexpr std::string_view kRunWithChildren = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"text","id":"nested","style":{"width":100,"height":30},
        "children":[{"type":"text-run","id":"nested/run","text":"A",
          "children":[{"type":"view","id":"hidden","children":[]}]}]}}
    )JSON";
    constexpr std::string_view kRunWithBadStyle = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"text","id":"styled","style":{"width":100,"height":30},
        "children":[{"type":"text-run","id":"styled/run","text":"A","style":"bold",
          "children":[]}]}}
    )JSON";
    constexpr std::string_view kDuplicateRunId = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"dup-root","style":{"width":100,"height":60},
        "children":[
          {"type":"text","id":"dup-text","style":{"height":20},"children":[
            {"type":"text-run","id":"dup","text":"A","children":[]},
            {"type":"text-run","id":"dup","text":"B","children":[]}]}]}}
    )JSON";
    constexpr std::string_view kRunIdShadowsNode = R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"shadow-root","style":{"width":100,"height":60},
        "children":[
          {"type":"text","id":"shadow-text","style":{"height":20},"children":[
            {"type":"text-run","id":"shadow-root","text":"A","children":[]}]}]}}
    )JSON";
    if (!run_rejected(kRunWithoutText, "text-run label/second requires string text",
            "label/second", "text") ||
        !run_rejected(kRunWithoutId, "text-run under go requires a non-empty string id",
            "go", "id") ||
        !run_rejected(kRunWithChildren, "text-run nested/run does not accept children",
            "nested/run", "children") ||
        !run_rejected(kRunWithBadStyle, "text-run styled/run style must be an object",
            "styled/run", "style") ||
        !run_rejected(kDuplicateRunId,
            "authoring tree contains an empty or duplicate node id: dup", "dup", "id") ||
        !run_rejected(kRunIdShadowsNode,
            "authoring tree contains an empty or duplicate node id: shadow-root",
            "shadow-root", "id")) {
        std::cerr << "a malformed attributed text-run did not fail closed\n";
        return 1;
    }



    return compositing_keys() && transform_pivots_once() && transform_ignores_locale() &&
                   path_nodes() && grouping_keys()
               ? 0
               : 1;
}
