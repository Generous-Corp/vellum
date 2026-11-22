#include "rendered_tree_materializer.hpp"
#include "token_slot_tree.hpp"

#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/token_receipts.hpp>
#include <vellum/graphics/hit_test.hpp>

#include <algorithm>
#include <clocale>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <optional>
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

bool box_material() {
    using namespace vellum::graphics;
    constexpr std::string_view content = R"JSON(
      {"type":"view","id":"material","style":{"width":80,"height":40,
        "backgroundColor":"#101928","borderRadius":6,
        "borderTopWidth":1,"borderTopColor":"#112233",
        "backgroundGradients":[
          {"kind":"radial","radiusX":0.8,"radiusY":0.6,
           "stops":[{"position":0,"color":"#00000000"},{"position":1,"color":"#ff0000"}]},
          {"kind":"linear","angle":180,
           "stops":[{"position":0,"color":"#00000000"},{"position":1,"color":"#0000ff"}]}]},
        "children":[]},
      {"type":"view","id":"control","style":{"width":80,"height":40},"children":[]}
      ,{"type":"view","id":"sides","style":{"width":80,"height":40,
        "borderRadius":12,
        "borderTopWidth":2,"borderTopColor":"#ff0000",
        "borderRightWidth":4,"borderRightColor":"#00ff00",
        "borderBottomWidth":3,"borderBottomColor":"#0000ff",
        "borderLeftWidth":8,"borderLeftColor":"#ffff00"},"children":[]}
      ,{"type":"view","id":"uniform-sides","style":{"width":80,"height":40,
        "borderRadius":12,
        "borderTopWidth":2,"borderTopColor":"#ff0000",
        "borderRightWidth":2,"borderRightColor":"#00ff00",
        "borderBottomWidth":2,"borderBottomColor":"#0000ff",
        "borderLeftWidth":2,"borderLeftColor":"#ffff00"},"children":[]}
    )JSON";
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(wrap_tree(content), rendered, &error)) {
        std::cerr << "box material: " << error << '\n';
        return false;
    }
    const auto& box = rendered.scene.root.children[0];
    const auto& control = rendered.scene.root.children[1];
    const auto& sides = rendered.scene.root.children[2];
    const auto& uniform_sides = rendered.scene.root.children[3];
    const auto has_elliptical_arc = [](const auto& child) {
        std::size_t offset = 0;
        for (const auto verb : child.path->verbs) {
            if (verb == PathVerb::arc && !near(child.path->values[offset], child.path->values[offset + 1])) {
                return true;
            }
            offset += path_verb_arity(verb);
        }
        return false;
    };
    const bool valid = box.kind == SceneNode::Kind::rectangle && box.fill.blue8() == 0x28 &&
        box.corner_radius == 6.0F && box.fill_gradients.size() == 2U &&
        box.fill_gradients[0].kind == GradientKind::radial &&
        box.fill_gradients[1].kind == GradientKind::linear &&
        box.children.size() == 1U && box.children[0].kind == SceneNode::Kind::path &&
        box.children[0].fill.blue8() == 0x33 && box.children[0].clip.has_value() &&
        control.kind == SceneNode::Kind::group && control.children.empty() &&
        control.fill_gradients.empty() && sides.children.size() == 4U &&
        std::all_of(sides.children.begin(), sides.children.end(), [](const auto& child) {
            return child.kind == SceneNode::Kind::path && child.path != nullptr &&
                std::count(child.path->verbs.begin(), child.path->verbs.end(), PathVerb::arc) >= 2;
        }) && has_elliptical_arc(sides.children[0]) && uniform_sides.children.size() == 4U;
    if (!valid) std::cerr << "box material: scene dropped a gradient or border\n";
    if (!valid) return false;
    const auto corner = Point{sides.bounds.x + 5.0F, sides.bounds.y + 3.0F};
    const auto hit = pick(rendered.scene, corner);
    if (!hit || hit->rfind("sides/border-", 0) != 0) {
        std::cerr << "box material: a rounded border corner was left unpainted\n";
        return false;
    }
    for (int y = 1; y <= 12; ++y) {
        for (int x = 1; x <= 12; ++x) {
            const float outer_dx = static_cast<float>(x) - 12.0F;
            const float outer_dy = static_cast<float>(y) - 12.0F;
            const float inner_dx = outer_dx / 4.0F;
            const float inner_dy = outer_dy / 10.0F;
            if (outer_dx * outer_dx + outer_dy * outer_dy > 144.0F ||
                inner_dx * inner_dx + inner_dy * inner_dy < 1.0F) {
                continue;
            }
            const auto sample = Point{sides.bounds.x + static_cast<float>(x),
                                      sides.bounds.y + static_cast<float>(y)};
            const auto sample_hit = pick(rendered.scene, sample);
            if (!sample_hit || sample_hit->rfind("sides/border-", 0) != 0) {
                std::cerr << "box material: rounded border hole at " << x << ',' << y << '\n';
                return false;
            }
        }
    }
    const auto uniform_corner = Point{uniform_sides.bounds.x + 5.0F,
                                      uniform_sides.bounds.y + 3.0F};
    const auto uniform_hit = pick(rendered.scene, uniform_corner);
    if (!uniform_hit || uniform_hit->rfind("uniform-sides/border-", 0) != 0) {
        std::cerr << "box material: the equal-width rounded corner was left unpainted\n";
        return false;
    }
    return true;
}

// A nonuniform border must paint the rounded ring at this exact corner pixel:
// with an 80x40 box, radius 12 and four 2px side colours, (5,3) is inside the
// outer curve and outside the inner curve.  The old straight side polygons
// left it unpainted.
bool rounded_border_corner_pixel_regression() {
    constexpr std::string_view tree = R"JSON(
      {"type":"view","id":"corner","style":{"width":80,"height":40,
        "borderRadius":12,
        "borderTopWidth":2,"borderTopColor":"#ff0000",
        "borderRightWidth":2,"borderRightColor":"#00ff00",
        "borderBottomWidth":2,"borderBottomColor":"#0000ff",
        "borderLeftWidth":2,"borderLeftColor":"#ffff00"},"children":[]}
    )JSON";
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(wrap_tree(tree), rendered, &error)) {
        std::cerr << "rounded border regression: " << error << '\n';
        return false;
    }
    const auto& corner = rendered.scene.root.children[0];
    const auto hit = pick(rendered.scene, {corner.bounds.x + 5.0F, corner.bounds.y + 3.0F});
    if (!hit || hit->rfind("corner/border-", 0) != 0) {
        std::cerr << "rounded border regression: (5,3) is unpainted\n";
        return false;
    }
    return true;
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
            "interpolation": "linear-srgb",
            "colorSpace": "display-p3",
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

bool image_nodes() {
    using vellum::graphics::ImageFit;
    using vellum::graphics::ImageSampling;
    using vellum::graphics::LoweringDiagnostic;
    using vellum::graphics::SceneNode;
    const std::string bare(64, 'A');
    const std::string canonical = "sha256:" + std::string(64, 'a');
    const std::string good = std::string(R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"stack","id":"art","style":{"width":200,"height":120},
        "children":[
          {"type":"image","id":"logo","source":")JSON") + bare + R"JSON(",
            "fit":"cover","sampling":"nearest",
            "sourceRect":{"x":1,"y":2,"width":3,"height":4},
            "style":{"width":40,"height":30,"backgroundColor":"#112233","borderRadius":4,
              "boxShadow":{"color":"#000000"},"opacity":0.5},
            "children":[]},
          {"type":"image","id":"plain","source":"sha256:)JSON" + std::string(64, 'b') +
                             R"JSON(","style":{"width":10,"height":10}},
          {"type":"image","id":"remote","source":"https://example.com/logo.png",
            "style":{"width":10,"height":10}},
          {"type":"image","id":"unsourced","style":{"width":10,"height":10}}
        ]}}
    )JSON";
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    std::vector<LoweringDiagnostic> failure_diagnostics;
    if (!vellum::authoring::materialize_rendered_tree(good, rendered, &error,
                                                      &failure_diagnostics)) {
        std::cerr << "image: " << error << '\n';
        return false;
    }
    const auto* logo = vellum::graphics::find_node(rendered.scene, "logo");
    const auto* plain = vellum::graphics::find_node(rendered.scene, "plain");
    const auto* remote = vellum::graphics::find_node(rendered.scene, "remote");
    const auto* unsourced = vellum::graphics::find_node(rendered.scene, "unsourced");
    if (logo == nullptr || logo->kind != SceneNode::Kind::image || !logo->image ||
        logo->image->content_hash != canonical || logo->image->fit != ImageFit::cover ||
        logo->image->sampling != ImageSampling::nearest || !logo->image->source_rect ||
        logo->image->source_rect->x != 1.0F || logo->image->source_rect->height != 4.0F ||
        logo->fill.alpha != 1.0F || logo->corner_radius != 4.0F || logo->opacity != 0.5F ||
        !logo->box_shadows.empty() || logo->bounds.width != 40.0F) {
        std::cerr << "image: a hashed image was not materialized\n";
        return false;
    }
    if (plain == nullptr || plain->kind != SceneNode::Kind::image ||
        plain->image->fit != ImageFit::fill ||
        plain->image->sampling != ImageSampling::linear || plain->image->source_rect ||
        remote == nullptr || remote->kind == SceneNode::Kind::image ||
        unsourced == nullptr || unsourced->kind == SceneNode::Kind::image) {
        std::cerr << "image: defaults or non-hash sources were not handled\n";
        return false;
    }
    const auto has = [&](std::string_view node, std::string_view code,
                         std::string_view property) {
        return std::any_of(rendered.diagnostics.begin(), rendered.diagnostics.end(),
            [&](const LoweringDiagnostic& diagnostic) {
                return diagnostic.node_id == node && diagnostic.code == code &&
                       diagnostic.property == property &&
                       diagnostic.severity == LoweringDiagnostic::Severity::warning;
            });
    };
    if (rendered.diagnostics.size() != 3U ||
        !has("logo", vellum::graphics::kStyleDroppedCode, "boxShadow") ||
        !has("remote", vellum::graphics::kNodeContentDroppedCode, "source") ||
        !has("unsourced", vellum::graphics::kNodeContentDroppedCode, "type") ||
        !failure_diagnostics.empty()) {
        std::cerr << "image: diagnostics were not reported as expected ("
                  << rendered.diagnostics.size() << ")\n";
        return false;
    }
    // The materialized scene draws at the current version; at version 3 the
    // image node is refused with a version-mismatch loss.
    const auto commands = vellum::graphics::make_paint_commands(rendered.scene);
    const auto missing = vellum::graphics::scene_loss_diagnostics(rendered.scene);
    auto older = rendered.scene;
    older.scene_version = 3U;
    const auto refused = vellum::graphics::scene_loss_diagnostics(older);
    if (std::count_if(commands.begin(), commands.end(), [](const auto& command) {
            return command.kind == vellum::graphics::PaintCommand::Kind::image;
        }) != 2 ||
        missing.size() != 2U || missing[0].code != vellum::graphics::kImageAssetMissingCode ||
        refused.size() != 1U || refused[0].code != vellum::graphics::kSceneVersionMismatchCode ||
        refused[0].node_id != "logo") {
        std::cerr << "image: the materialized scene did not version as expected\n";
        return false;
    }

    const auto rejected = [&](std::string_view attributes, std::string_view expected_error,
                              std::string_view property) {
        const std::string tree =
            std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
              "tree":{"type":"stack","id":"art","style":{"width":100,"height":100},
              "children":[{"type":"image","id":"bad","source":")JSON") + canonical + "\"" +
            std::string(attributes) + "}]}}";
        vellum::authoring::RenderedApplication kept = rendered;
        std::string why;
        std::vector<LoweringDiagnostic> diagnostics;
        const bool ok = !vellum::authoring::materialize_rendered_tree(
                            tree, kept, &why, &diagnostics) &&
                        why == expected_error && diagnostics.size() == 1U &&
                        diagnostics[0].severity == LoweringDiagnostic::Severity::error &&
                        diagnostics[0].code == vellum::graphics::kInvalidImageCode &&
                        diagnostics[0].node_id == "bad" && diagnostics[0].property == property &&
                        kept.scene.width == rendered.scene.width;
        if (!ok) std::cerr << "image was not rejected as expected: " << why << '\n';
        return ok;
    };
    static_assert(vellum::graphics::kInvalidImageCode == "VELLUM_INVALID_IMAGE");
    return rejected(R"(,"fit":"stretch")", "image bad fit must be fill, contain, cover or none",
                    "fit") &&
           rejected(R"(,"sampling":"cubic")", "image bad sampling must be nearest or linear",
                    "sampling") &&
           rejected(R"(,"sourceRect":[0,0,1,1])", "image bad sourceRect must be an object",
                    "sourceRect") &&
           rejected(R"(,"sourceRect":{"x":0,"y":0,"width":-1,"height":1})",
                    "image bad sourceRect needs finite x, y and non-negative width and height",
                    "sourceRect") &&
           rejected(R"(,"children":[{"type":"view","id":"inner"}])",
                    "image bad does not accept children", "children");
}

std::string token_tree(std::string_view accent, std::string_view canvas,
                       std::string_view extra_attributes = {},
                       std::string_view table_override = {}) {
    const std::string table = table_override.empty()
        ? std::string(R"({"path":"main.color.accent","layer":"primitive","value":")") +
              std::string(accent) +
              R"("},{"path":"main.color.canvas","layer":"primitive","value":")" +
              std::string(canvas) + R"("})"
        : std::string(table_override);
    return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
      "tree":{"type":"stack","id":"themed","style":{"width":200,"height":200,
        "backgroundColor":")JSON") + std::string(canvas) +
        R"JSON("},"tokenRefs":{"backgroundColor":1},"tokens":[)JSON" + table + R"JSON(],
      "children":[
        {"type":"button","id":"go","text":"Go","tokenRefs":{"backgroundColor":0,"color":0},
          "style":{"width":80,"height":30,"backgroundColor":")JSON" + std::string(accent) +
        R"JSON(","color":")JSON" + std::string(accent) + R"JSON("}},
        {"type":"view","id":"card","tokenRefs":{"backgroundColor":0},
          "style":{"width":40,"height":40,"backgroundColor":")JSON" + std::string(accent) +
        R"JSON("}},
        {"type":"text","id":"title","text":"Board","tokenRefs":{"color":1},
          "style":{"height":20,"color":")JSON" + std::string(canvas) + R"JSON("}},
        {"type":"view","id":"literal","style":{"width":40,"height":40,
          "backgroundColor":"#123456"}})JSON" + std::string(extra_attributes) + R"JSON(]}}
    )JSON";
}

bool token_receipts() {
    using vellum::graphics::find_node;
    using vellum::graphics::same_token_color;
    vellum::authoring::RenderedApplication light;
    std::string error;
    if (!vellum::authoring::materialize_rendered_tree(
            token_tree("#14b8a6", "#0f172a"), light, &error)) {
        std::cerr << "token tree was not materialized: " << error << '\n';
        return false;
    }
    const auto& scene = light.scene;
    const auto fill_token = [&](std::string_view id) {
        const auto* node = find_node(scene, id);
        return node != nullptr && node->fill_token ? static_cast<int>(*node->fill_token) : -1;
    };
    if (scene.tokens.size() != 2U || scene.tokens[0].path != "main.color.accent" ||
        scene.tokens[0].layer != "primitive" || scene.tokens[1].path != "main.color.canvas" ||
        scene.scene_version != vellum::graphics::kSceneVersion || fill_token("themed") != 1 ||
        !scene.background_token || *scene.background_token != 1U ||
        fill_token("go") != 0 || fill_token("go/label") != 0 || fill_token("card") != 0 ||
        fill_token("title") != 1 || fill_token("literal") != -1 ||
        vellum::graphics::find_invalid_token_reference(scene).has_value() ||
        !vellum::graphics::accept_scene(scene).accepted) {
        std::cerr << "token receipts did not round trip into the scene\n";
        return false;
    }

    vellum::authoring::RenderedApplication dark;
    if (!vellum::authoring::materialize_rendered_tree(
            token_tree("#ef4444", "#f8fafc"), dark, &error)) {
        std::cerr << error << '\n';
        return false;
    }
    auto switched = light.scene;
    const auto result = vellum::graphics::apply_token_values(switched, dark.scene.tokens);
    bool same = result.applied && result.changed.size() == 2U && result.nodes_updated == 5U &&
                same_token_color(switched.background, dark.scene.background);
    for (const auto* id : {"themed", "go", "go/label", "card", "title", "literal"}) {
        const auto* left = find_node(switched, id);
        const auto* right = find_node(dark.scene, id);
        same = same && left != nullptr && right != nullptr &&
               same_token_color(left->fill, right->fill) && left->fill_token == right->fill_token;
    }
    if (!same) {
        std::cerr << "a theme switch did not equal materializing the new theme\n";
        return false;
    }

    vellum::authoring::RenderedApplication equal_literal;
    const auto literal_tree = token_tree(
        "#14b8a6", "#0f172a",
        R"(,{"type":"text","id":"equal","style":{"width":80,"height":20,"color":"#0f172a"},"children":[{"type":"text-run","id":"equal/run","text":"=","style":{"color":"#0f172a"}}]})");
    if (!vellum::authoring::materialize_rendered_tree(literal_tree, equal_literal, &error)) {
        std::cerr << "equal-colour literal tree was not materialized: " << error << '\n';
        return false;
    }
    auto equal_switched = equal_literal.scene;
    (void)vellum::graphics::apply_token_values(equal_switched, dark.scene.tokens);
    const auto* equal_node = find_node(equal_switched, "equal");
    if (equal_node == nullptr || equal_node->text_runs.size() != 1U ||
        !same_token_color(equal_node->text_runs[0].style.color,
                          vellum::graphics::Color::hex(0x0F172A))) {
        std::cerr << "an explicit run equal to its parent colour was rewritten\n";
        return false;
    }

    const auto root_text_tree = [](std::string_view color) {
        return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":"text","id":"root-text","style":{"width":80,"height":20,
            "color":")JSON") + std::string(color) + R"JSON("},"tokenRefs":{"color":0},
          "tokens":[{"path":"root.text","layer":"theme","value":")JSON" +
            std::string(color) + R"JSON("}],"children":[]}})JSON";
    };
    vellum::authoring::RenderedApplication root_text;
    if (!vellum::authoring::materialize_rendered_tree(root_text_tree("#f8fafc"), root_text,
                                                      &error) ||
        root_text.scene.background_token) {
        std::cerr << "a root text token was mistaken for the scene background: " << error << '\n';
        return false;
    }
    vellum::authoring::RenderedApplication root_text_dark;
    if (!vellum::authoring::materialize_rendered_tree(root_text_tree("#2563eb"),
                                                      root_text_dark, &error)) {
        std::cerr << "the root text theme control was not materialized: " << error << '\n';
        return false;
    }
    auto root_text_switched = root_text.scene;
    (void)vellum::graphics::apply_token_values(root_text_switched, root_text_dark.scene.tokens);
    if (!same_token_color(root_text_switched.background,
                          vellum::graphics::Color::hex(0xF8FAFC)) ||
        !same_token_color(root_text_switched.root.fill, root_text_dark.scene.root.fill)) {
        std::cerr << "a root text token switch changed the clear colour incorrectly\n";
        return false;
    }

    const auto placeholder_tree = [](std::string_view color) {
        return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":"stack","id":"placeholder-root","style":{"width":120,"height":60},
          "tokens":[{"path":"input.color","layer":"theme","value":")JSON") +
            std::string(color) + R"JSON("}],"children":[{"type":"text-input","id":"input",
            "primitiveVersion":1,"value":"","placeholder":"Hint","tokenRefs":{"color":0},
            "events":{"change":"input:change"},"style":{"width":100,"height":30,"color":")JSON" +
            std::string(color) + R"JSON("}}]}})JSON";
    };
    vellum::authoring::RenderedApplication placeholder_light;
    vellum::authoring::RenderedApplication placeholder_dark;
    if (!vellum::authoring::materialize_rendered_tree(
            placeholder_tree("#94a3b8"), placeholder_light, &error) ||
        !vellum::authoring::materialize_rendered_tree(
            placeholder_tree("#2563eb"), placeholder_dark, &error)) {
        std::cerr << "the tokenized placeholder control was not materialized: " << error << '\n';
        return false;
    }
    const auto* placeholder = find_node(placeholder_light.scene, "input/value");
    const auto* expected_placeholder = find_node(placeholder_dark.scene, "input/value");
    auto placeholder_switched = placeholder_light.scene;
    (void)vellum::graphics::apply_token_values(
        placeholder_switched, placeholder_dark.scene.tokens);
    const auto* switched_placeholder = find_node(placeholder_switched, "input/value");
    if (placeholder == nullptr || expected_placeholder == nullptr || switched_placeholder == nullptr ||
        !placeholder->fill_token || !same_token_color(switched_placeholder->fill,
                                                        expected_placeholder->fill)) {
        std::cerr << "an empty text-input placeholder did not follow its colour token\n";
        return false;
    }

    auto older = light.scene;
    older.scene_version = 6U;
    const auto refused = vellum::graphics::accept_scene(older);
    if (refused.accepted || refused.diagnostics.size() != 1U ||
        refused.diagnostics[0].code != vellum::graphics::kSceneVersionMismatchCode) {
        std::cerr << "a version 6 scene using the token table was not refused\n";
        return false;
    }

    const auto rejected = [&](const std::string& tree, std::string_view expected) {
        auto kept = light;
        std::string why;
        const bool failed = !vellum::authoring::materialize_rendered_tree(tree, kept, &why);
        if (!failed || why.find(expected) == std::string::npos ||
            kept.scene.tokens.size() != light.scene.tokens.size()) {
            std::cerr << "token tree was not rejected as expected: " << why << '\n';
            return false;
        }
        return true;
    };
    constexpr std::string_view kStale = R"JSON(,{"type":"view","id":"stale","tokenRefs":{"backgroundColor":0},
        "style":{"width":4,"height":4,"backgroundColor":"#000000"}})JSON";
    constexpr std::string_view kPast = R"JSON(,{"type":"view","id":"past","tokenRefs":{"backgroundColor":2},
        "style":{"width":4,"height":4,"backgroundColor":"#14b8a6"}})JSON";
    constexpr std::string_view kFraction = R"JSON(,{"type":"view","id":"half","tokenRefs":{"backgroundColor":0.5},
        "style":{"width":4,"height":4,"backgroundColor":"#14b8a6"}})JSON";
    constexpr std::string_view kShape = R"JSON(,{"type":"view","id":"shape","tokenRefs":[0],
        "style":{"width":4,"height":4,"backgroundColor":"#14b8a6"}})JSON";
    constexpr std::string_view kEmptyInput = R"JSON(,{"type":"text-input","id":"empty-input",
        "primitiveVersion":1,"value":"","tokenRefs":{"color":1e30},"events":{"change":"input:change"},
        "style":{"width":100,"height":30,"color":"#112233"}})JSON";
    constexpr std::string_view kEmptyInputStale = R"JSON(,{"type":"text-input","id":"empty-input",
        "primitiveVersion":1,"value":"","tokenRefs":{"color":0},"events":{"change":"input:change"},
        "style":{"width":100,"height":30,"color":"#112233"}})JSON";
    constexpr std::string_view kEmptyButton = R"JSON(,{"type":"button","id":"empty-button",
        "tokenRefs":{"color":0},"events":{"press":"go"},
        "style":{"width":40,"height":20,"color":"#112233"}})JSON";
    constexpr std::string_view kEmptyButtonPast = R"JSON(,{"type":"button","id":"empty-button",
        "tokenRefs":{"color":9},"events":{"press":"go"},
        "style":{"width":40,"height":20,"color":"#14b8a6"}})JSON";
    constexpr std::string_view kEmptyButtonOk = R"JSON(,{"type":"button","id":"empty-button",
        "tokenRefs":{"color":0},"events":{"press":"go"},
        "style":{"width":40,"height":20,"color":"#14b8a6"}})JSON";
    constexpr std::string_view kEmptyButtonNoColor = R"JSON(,{"type":"button","id":"empty-button",
        "tokenRefs":{"color":0},"events":{"press":"go"},"style":{"width":40,"height":20}})JSON";
    constexpr std::string_view kDroppedColor = R"JSON(,{"type":"view","id":"dropped",
        "tokenRefs":{"color":7},"style":{"width":4,"height":4,"color":"#14b8a6"}})JSON";
    constexpr std::string_view kDroppedStale = R"JSON(,{"type":"view","id":"dropped",
        "tokenRefs":{"color":1},"style":{"width":4,"height":4,"color":"#14b8a6"}})JSON";
    {
        vellum::authoring::RenderedApplication control;
        std::string control_error;
        if (!vellum::authoring::materialize_rendered_tree(
                token_tree("#14b8a6", "#0f172a", kEmptyButtonOk), control, &control_error)) {
            std::cerr << "control: an empty button with a matching colour token was refused: "
                      << control_error << '\n';
            return false;
        }
        const bool reported = std::any_of(
            control.diagnostics.begin(), control.diagnostics.end(), [](const auto& item) {
                return item.code == vellum::graphics::kStyleDroppedCode &&
                       item.node_id == "empty-button" && item.property == "tokenRefs.color";
            });
        if (!reported) {
            std::cerr << "an empty button's colour reference was dropped without a diagnostic\n";
            return false;
        }
    }
    const std::string repeated =
        R"({"path":"a","layer":"primitive","value":"#14b8a6"},)"
        R"({"path":"a","layer":"primitive","value":"#0f172a"})";
    const std::string not_a_colour =
        R"({"path":"a","layer":"primitive","value":"red"},)"
        R"({"path":"b","layer":"primitive","value":"#0f172a"})";
    return rejected(token_tree("#14b8a6", "#0f172a", kStale), "disagrees with its resolved colour") &&
           rejected(token_tree("#14b8a6", "#0f172a", kPast), "must name an entry of the token table") &&
           rejected(token_tree("#14b8a6", "#0f172a", kFraction), "must name an entry of the token table") &&
           rejected(token_tree("#14b8a6", "#0f172a", kShape), "tokenRefs of shape must be an object") &&
           rejected(token_tree("#14b8a6", "#0f172a", kEmptyInput),
                    "must name an entry of the token table") &&
           rejected(token_tree("#14b8a6", "#0f172a", kEmptyInputStale),
                    "disagrees with its resolved colour") &&
           rejected(token_tree("#14b8a6", "#0f172a", kEmptyButton),
                    "disagrees with its resolved colour") &&
           rejected(token_tree("#14b8a6", "#0f172a", kEmptyButtonPast),
                    "must name an entry of the token table") &&
           rejected(token_tree("#14b8a6", "#0f172a", kEmptyButtonNoColor),
                    "names a colour the node does not have") &&
           rejected(token_tree("#14b8a6", "#0f172a", kDroppedColor),
                    "must name an entry of the token table") &&
           rejected(token_tree("#14b8a6", "#0f172a", kDroppedStale),
                    "disagrees with its resolved colour") &&
           rejected(token_tree("#14b8a6", "#0f172a", {}, repeated), "repeats a path") &&
           rejected(token_tree("#14b8a6", "#0f172a", {}, not_a_colour), "has no colour value");
}

// A plot node carries its channel binding and drawing fields into a plot spec
// with no samples; its background is the box's backgroundColor. Bad values
// fail atomically with an invalid-plot error, and a version 11 scene refuses
// the plot.
bool plot_nodes() {
    using vellum::graphics::LoweringDiagnostic;
    using vellum::graphics::PlotChannelShape;
    using vellum::graphics::PlotStyle;
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto tree = wrap_tree(R"JSON(
        {"type":"plot","id":"scope","plot":"line","channel":"output","source":"scalar",
         "slots":240,"history":"sweep","min":-1,"max":1,"lineWidth":2,"lineColor":"#F97316",
         "style":{"width":120,"height":60,"backgroundColor":"#111827","borderRadius":4}},
        {"type":"plot","id":"level","plot":"meter","channel":"meters","audioChannel":1,
         "orientation":"horizontal","peakWidth":3,"fillColor":"#22C55E","peakColor":"#FFFFFF",
         "loudness":"lufs","style":{"width":100,"height":12}})JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "plot: " << error << '\n';
        return false;
    }
    const auto* scope = vellum::graphics::find_node(rendered.scene, "scope");
    const auto* level = vellum::graphics::find_node(rendered.scene, "level");
    if (scope == nullptr || level == nullptr || scope->kind != vellum::graphics::SceneNode::Kind::plot ||
        !scope->plot || !level->plot || scope->plot->style != PlotStyle::line ||
        scope->plot->channel != "output" || scope->plot->shape != PlotChannelShape::scalar ||
        scope->plot->slot_count != 240U ||
        scope->plot->history != vellum::graphics::PlotHistory::sweep ||
        scope->plot->value_min != -1.0F || scope->plot->value_max != 1.0F ||
        scope->plot->line_width != 2.0F || scope->plot->line_color.red8() != 0xF9U ||
        !scope->plot->samples.empty() || scope->fill.red8() != 0x11U ||
        level->plot->style != PlotStyle::level_meter ||
        level->plot->shape != PlotChannelShape::meter || level->plot->audio_channel != 1U ||
        level->plot->orientation != vellum::graphics::PlotOrientation::horizontal ||
        level->plot->peak_width != 3.0F || level->plot->peak_color.blue8() != 0xFFU) {
        std::cerr << "plot: the plot fields were not materialized\n";
        return false;
    }
    const auto reported = [&](std::string_view node, std::string_view property) {
        return std::any_of(rendered.diagnostics.begin(), rendered.diagnostics.end(),
            [&](const LoweringDiagnostic& diagnostic) {
                return diagnostic.node_id == node && diagnostic.property == property &&
                       diagnostic.code == vellum::graphics::kStyleDroppedCode;
            });
    };
    if (!reported("scope", "borderRadius") || !reported("level", "loudness") ||
        reported("scope", "backgroundColor")) {
        std::cerr << "plot: dropped keys were not reported as expected\n";
        return false;
    }
    if (!vellum::graphics::accept_scene(rendered.scene).accepted) {
        std::cerr << "plot: the materialized scene was refused\n";
        return false;
    }
    auto older = rendered.scene;
    older.scene_version = 11U;
    const auto refused = vellum::graphics::accept_scene(older);
    if (refused.accepted || refused.diagnostics.size() != 1U ||
        refused.diagnostics[0].code != vellum::graphics::kSceneVersionMismatchCode ||
        refused.diagnostics[0].node_id != "scope") {
        std::cerr << "plot: a version 11 scene did not refuse the plot\n";
        return false;
    }
    const auto rejected = [&](std::string_view attributes, std::string_view expected_error,
                              std::string_view property) {
        const auto bad = wrap_tree(std::string(R"JSON({"type":"plot","id":"bad")JSON") +
                                   std::string(attributes) +
                                   R"JSON(,"style":{"width":10,"height":10}})JSON");
        vellum::authoring::RenderedApplication kept = rendered;
        std::string why;
        std::vector<LoweringDiagnostic> diagnostics;
        const bool ok = !vellum::authoring::materialize_rendered_tree(
                            bad, kept, &why, &diagnostics) &&
                        why == expected_error && !diagnostics.empty() &&
                        diagnostics.back().severity == LoweringDiagnostic::Severity::error &&
                        diagnostics.back().code == vellum::graphics::kInvalidPlotCode &&
                        diagnostics.back().node_id == "bad" &&
                        diagnostics.back().property == property &&
                        kept.scene.width == rendered.scene.width;
        if (!ok) std::cerr << "plot was not rejected as expected: " << why << '\n';
        return ok;
    };
    static_assert(vellum::graphics::kInvalidPlotCode == "VELLUM_INVALID_PLOT");
    return rejected(R"(,"plot":"pie","channel":"c","slots":4)",
                    "plot bad plot must be line, area, bar or meter", "plot") &&
           rejected(R"(,"plot":"line","slots":4)",
                    "plot bad requires the name of a value-feed channel in channel", "channel") &&
           rejected(R"(,"plot":"line","channel":"c","slots":2.5)",
                    "plot bad slots must be a whole number of at most 4194304", "slots") &&
           rejected(R"(,"plot":"bar","channel":"c","source":"scalar","slots":4)",
                    "plot bad cannot be drawn: a bar plot reads a vector channel", "plot") &&
           rejected(R"(,"plot":"line","channel":"c")",
                    "plot bad cannot be drawn: the slot count is not between 1 and kMaxPlotSlots",
                    "plot") &&
           rejected(R"(,"plot":"area","channel":"c","slots":4,"min":1,"max":1)",
                    "plot bad cannot be drawn: the value range is not finite or is empty",
                    "plot") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"min":"low")",
                    "plot bad min must be a finite number", "min") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"lineColor":"red")",
                    "plot bad lineColor must be a colour", "lineColor") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"lineColor":"#0x12AB")",
                    "plot bad lineColor must be a colour", "lineColor") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4194304.0000001)",
                    "plot bad slots must be a whole number of at most 4194304", "slots") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"min":1,"max":1.00000001)",
                    "plot bad cannot be drawn: the value range is not finite or is empty",
                    "plot") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"max":1e100)",
                    "plot bad max must be a finite number", "max") &&
           rejected(R"(,"plot":"line","channel":"c","slots":4,"children":[{"type":"view","id":"x"}])",
                    "plot bad does not accept children", "children");
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
      {"type":"text","id":"shadowed","text":"Hi","style":{"height":20,"overflow":"hidden",
        "boxShadow":{"color":"#000000","blurRadius":2}},"children":[]},
      {"type":"path","id":"clipped-path","d":"M0 0 L50 50","stroke":"#FF0000",
        "style":{"width":20,"height":20,"overflow":"hidden"}},
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
    const auto* clipped_path = vellum::graphics::find_node(rendered.scene, "clipped-path");
    if (caption == nullptr || !caption->clip || !near(caption->clip->radii.bottom_left, 5.0F) ||
        !caption->clip->clips_own_paint || clipped_path == nullptr || !clipped_path->clip ||
        !clipped_path->clip->clips_own_paint) {
        std::cerr << "grouping keys: an overflow clip does not cut text or path content\n";
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
    if (rendered.diagnostics.size() != 3U || reported("shaped", "overflow") != 1 ||
        reported("scroller", "overflow") != 1 || reported("shadowed", "boxShadow") != 1) {
        std::cerr << "grouping keys: expected three grouping warnings, got "
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

// A vector node draws only its asset: a background colour on it is reported
// as dropped and leaves the node's fill transparent. vellum-ui sends no token
// receipt for it, so only the literal colour reaches this check.
bool vector_background_dropped() {
    {
        const auto tree = wrap_tree(std::string(R"JSON({"type":"vector","id":"icon",
          "style":{"x":0,"y":0,"width":10,"height":10,"backgroundColor":"#14b8a6"},)JSON") +
          R"JSON("asset":{"contentHash":"sha256:)JSON" + std::string(64, 'd') +
          R"JSON(","viewBox":[0,0,10,10],"ops":[]},"children":[]})JSON");
        vellum::authoring::RenderedApplication rendered;
        std::string error;
        if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
            std::cerr << "vector background: " << error << '\n';
            return false;
        }
        const auto* node = vellum::graphics::find_node(rendered.scene, "icon");
        const bool reported = std::any_of(
            rendered.diagnostics.begin(), rendered.diagnostics.end(), [](const auto& d) {
                return d.code == vellum::graphics::kStyleDroppedCode &&
                       d.node_id == "icon" && d.property == "backgroundColor";
            });
        if (node == nullptr || node->fill.alpha != 0.0F || !reported) {
            std::cerr << "vector background: a background colour was not dropped and reported\n";
            return false;
        }
    }
    return true;
}

// The cases the JavaScript vector validator reads too: each gradient is
// materialized inside a compiled vector asset and must be accepted or refused
// as its verdict says.
bool gradient_parity() {
    std::ifstream file(std::string(VELLUM_VECTOR_FIXTURE_DIR) + "/gradient-parity.cases");
    if (!file) {
        std::cerr << "gradient parity: the case file is missing\n";
        return false;
    }
    const std::string hash = "sha256:" + std::string(64, 'd');
    std::string line;
    int cases = 0;
    while (std::getline(file, line)) {
        if (line.empty()) continue;
        const auto first = line.find('\t');
        const auto second = line.find('\t', first + 1);
        const std::string verdict = line.substr(0, first);
        const std::string name = line.substr(first + 1, second - first - 1);
        const std::string gradient = line.substr(second + 1);
        const auto tree = wrap_tree(std::string(R"JSON({"type":"vector","id":"icon",
          "style":{"x":0,"y":0,"width":10,"height":10},"asset":{"contentHash":")JSON") + hash +
          R"JSON(","viewBox":[0,0,10,10],"ops":[{"op":"path","d":"M0 0H10V10Z","fill":{"gradient":)JSON" +
          gradient + R"JSON(}}]},"children":[]})JSON");
        vellum::authoring::RenderedApplication rendered;
        std::string error;
        const bool accepted = vellum::authoring::materialize_rendered_tree(tree, rendered, &error);
        if (accepted != (verdict == "valid")) {
            std::cerr << "gradient parity: " << name << " was "
                      << (accepted ? "accepted" : "refused: " + error) << ", expected " << verdict
                      << '\n';
            return false;
        }
        ++cases;
    }
    if (cases < 30) {
        std::cerr << "gradient parity: too few cases\n";
        return false;
    }
    return true;
}

// A compiled vector asset materializes into one vector node whose display
// list carries groups, clips, solid and gradient paint with a gradient
// transform, and stroke geometry; a malformed asset is refused with the
// vector code.
bool vector_nodes() {
    const std::string hash = "sha256:" + std::string(64, 'd');
    const auto tree = [&](std::string_view ops, std::string_view extra = {}) {
        return wrap_tree(std::string(R"JSON({"type":"vector","id":"icon",
          "style":{"x":10,"y":20,"width":100,"height":50},)JSON") + std::string(extra) +
          R"JSON("asset":{"contentHash":")JSON" + hash +
          R"JSON(","viewBox":[0,0,200,100],"ops":[)JSON" + std::string(ops) + "]},\"children\":[]}");
    };
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto valid = tree(R"JSON(
        {"op":"group","transform":[1,0,0,1,5,5],"opacity":0.5,
         "clip":{"d":"M0 0H50V50Z","rule":"evenodd"}},
        {"op":"path","d":"M0 0H10V10Z","fill":"#ff000080","stroke":"#00ff00","strokeWidth":3,
         "strokeLineJoin":"round","strokeLineCap":"square","strokeMiterLimit":2,
         "strokeDasharray":[2,1],"strokeDashoffset":1},
        {"op":"end"},
        {"op":"path","d":"M0 0H10V10Z","fillRule":"evenodd","transform":[2,0,0,2,0,0],
         "fill":{"gradient":{"kind":"two-circle-radial","stops":[
            {"position":0,"color":"#000000"},{"position":0,"color":"#ffffff"},
            {"position":1,"color":"#0000ff"}]},"transform":[10,0,0,5,1,2]},
         "stroke":"none"})JSON");
    if (!vellum::authoring::materialize_rendered_tree(valid, rendered, &error)) {
        std::cerr << "vector: " << error << '\n';
        return false;
    }
    const auto* node = vellum::graphics::find_node(rendered.scene, "icon");
    if (node == nullptr || node->kind != vellum::graphics::SceneNode::Kind::vector ||
        !node->vector || node->vector->content_hash != hash || node->vector->ops.size() != 4U ||
        !near(node->bounds.x, 10.0F) || !near(node->vector->view_box.width, 200.0F) ||
        !rendered.diagnostics.empty()) {
        std::cerr << "vector: the compiled asset was not materialized\n";
        return false;
    }
    using vellum::graphics::VectorOp;
    const auto& ops = node->vector->ops;
    const auto& group = ops[0];
    const auto& stroked = ops[1];
    const auto& shaded = ops[3];
    if (group.kind != VectorOp::Kind::push_group || !near(group.opacity, 0.5F) ||
        !group.clip || group.clip->fill_rule != vellum::graphics::FillRule::evenodd ||
        !near(group.transform.tx, 5.0F) || stroked.kind != VectorOp::Kind::path ||
        !near(stroked.fill.color.alpha, 128.0F / 255.0F) || !near(stroked.stroke.color.green, 1.0F) ||
        !near(stroked.path->stroke.width, 3.0F) ||
        stroked.path->stroke.join != vellum::graphics::StrokeJoin::round ||
        stroked.path->stroke.cap != vellum::graphics::StrokeCap::square ||
        stroked.path->stroke.dash_array.size() != 2U || ops[2].kind != VectorOp::Kind::pop_group ||
        !shaded.fill.gradient ||
        shaded.fill.gradient->kind != vellum::graphics::GradientKind::two_circle_radial ||
        shaded.fill.gradient->stops.size() != 3U || !near(shaded.fill.gradient_transform.a, 10.0F) ||
        !near(shaded.fill.gradient_transform.ty, 2.0F) || !near(shaded.transform.a, 2.0F) ||
        shaded.path->fill_rule != vellum::graphics::FillRule::evenodd ||
        shaded.stroke.color.alpha != 0.0F) {
        std::cerr << "vector: an operation lost a field\n";
        return false;
    }
    const std::vector<std::pair<std::string, std::string>> refusals{
        {"an open group", tree(R"JSON({"op":"group"})JSON")},
        {"an unknown op", tree(R"JSON({"op":"image"})JSON")},
        {"bad path data", tree(R"JSON({"op":"path","d":"Q"})JSON")},
        {"a bad paint", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","fill":"red"})JSON")},
        {"a singular gradient transform", tree(R"JSON({"op":"path","d":"M0 0H1V1Z",
            "fill":{"gradient":{"stops":[{"position":0,"color":"#000000"},
            {"position":1,"color":"#ffffff"}]},"transform":[0,0,0,0,0,0]}})JSON")},
        {"an opacity over one", tree(R"JSON({"op":"group","opacity":2},{"op":"end"})JSON")},
        {"opacity on a path", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","opacity":0.5})JSON")},
        {"a clip on a path", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","clip":{"d":"M0 0H1V1Z"}})JSON")},
        {"a field on a group end", tree(R"JSON({"op":"group"},{"op":"end","opacity":1})JSON")},
        {"a transform on a clip", tree(R"JSON({"op":"group","clip":{"d":"M0 0H1V1Z","transform":[1,0,0,1,0,0]}},{"op":"end"})JSON")},
        {"an extra key on a paint", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","fill":{"gradient":{"stops":[{"position":0,"color":"#000000"},{"position":1,"color":"#ffffff"}]},"opacity":1}})JSON")},
        {"an extra key on a gradient", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","fill":{"gradient":{"units":"user","stops":[{"position":0,"color":"#000000"},{"position":1,"color":"#ffffff"}]}}})JSON")},
        {"a miter limit under one", tree(R"JSON({"op":"path","d":"M0 0H10V10Z","strokeMiterLimit":0})JSON")},
        {"an extra key on a stop", tree(R"JSON({"op":"path","d":"M0 0H1V1Z","fill":{"gradient":{"stops":[{"position":0,"color":"#000000","opacity":1},{"position":1,"color":"#ffffff"}]}}})JSON")},
    };
    for (const auto& [what, json] : refusals) {
        std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
        if (vellum::authoring::materialize_rendered_tree(json, rendered, &error, &diagnostics) ||
            diagnostics.empty() ||
            diagnostics.back().code != vellum::graphics::kInvalidVectorAssetCode ||
            diagnostics.back().node_id != "icon") {
            std::cerr << "vector: " << what << " was not refused with the vector code: " << error
                      << '\n';
            return false;
        }
    }
    const auto extra_asset_key = wrap_tree(R"JSON({"type":"vector","id":"icon","style":{"width":1,"height":1},
        "asset":{"contentHash":"sha256:)JSON" + std::string(64, 'd') +
        R"JSON(","viewBox":[0,0,1,1],"ops":[],"units":"px"},"children":[]})JSON");
    {
        std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
        if (vellum::authoring::materialize_rendered_tree(extra_asset_key, rendered, &error, &diagnostics) ||
            diagnostics.empty() || diagnostics.back().code != vellum::graphics::kInvalidVectorAssetCode) {
            std::cerr << "vector: an extra key on the asset was not refused\n";
            return false;
        }
    }
    const std::string accepted_controls[] = {
        tree(R"JSON({"op":"group","clip":{"d":"M0 0H1V1Z","rule":"evenodd"}},{"op":"end"})JSON"),
        tree(R"JSON({"op":"path","d":"M0 0H1V1Z","fill":{"gradient":{"kind":"linear","angle":90,"stops":[{"position":0,"color":"#000000"},{"position":1,"color":"#ffffff"}]},"transform":[1,0,0,1,0,0]}})JSON"),
    };
    for (const auto& json : accepted_controls) {
        if (!vellum::authoring::materialize_rendered_tree(json, rendered, &error)) {
            std::cerr << "vector: a well-formed nested object was refused: " << error << '\n';
            return false;
        }
    }
    const auto bad_hash = wrap_tree(R"JSON({"type":"vector","id":"icon","style":{"width":1,"height":1},
        "asset":{"contentHash":"md5:1","viewBox":[0,0,1,1],"ops":[]},"children":[]})JSON");
    if (vellum::authoring::materialize_rendered_tree(bad_hash, rendered, &error)) {
        std::cerr << "vector: a malformed content hash was accepted\n";
        return false;
    }
    if (!vellum::authoring::materialize_rendered_tree(
            tree("", R"JSON("extra":1,)JSON"), rendered, &error) ||
        rendered.diagnostics.size() != 1U ||
        rendered.diagnostics[0].code != vellum::graphics::kStyleDroppedCode ||
        rendered.diagnostics[0].property != "extra") {
        std::cerr << "vector: an unknown attribute was not reported\n";
        return false;
    }
    return true;
}

bool expect(bool ok, const std::string& what) {
    if (!ok) std::cerr << "FAILED: " << what << std::endl;
    return ok;
}

// Every token slot kind reaches the scene from the retained tree, and a theme
// switch re-resolves each one to what materializing the new theme yields.
bool token_slots() {
    using vellum::authoring::testing::SlotBindings;
    using vellum::authoring::testing::SlotTheme;
    using vellum::authoring::testing::slot_tree;
    using vellum::graphics::TokenSlot;
    using vellum::graphics::find_node;
    using vellum::graphics::same_token_color;
    bool ok = true;
    const auto materialize = [](const std::string& json, vellum::authoring::RenderedApplication& app,
                                std::string& why,
                                std::vector<vellum::graphics::LoweringDiagnostic>* failure =
                                    nullptr) {
        return vellum::authoring::materialize_rendered_tree(json, app, &why, failure);
    };
    vellum::authoring::RenderedApplication light;
    std::string error;
    if (!materialize(slot_tree({}), light, error)) {
        std::cerr << "slot tree was not materialized: " << error << '\n';
        return false;
    }
    const auto slot_of = [](const vellum::graphics::Scene& scene, std::string_view id)
        -> std::optional<TokenSlot> {
        const auto* node = find_node(scene, id);
        if (node == nullptr || node->color_tokens.size() != 1U) return std::nullopt;
        return node->color_tokens[0];
    };
    const auto is_slot = [&](std::string_view id, TokenSlot::Kind kind, std::uint32_t stop,
                             std::uint32_t token) {
        const auto slot = slot_of(light.scene, id);
        return slot && slot->kind == kind && slot->index == 0U && slot->stop == stop &&
               slot->token == token;
    };
    const auto* fill = find_node(light.scene, "fill");
    ok &= expect(fill != nullptr && fill->fill_token == 0U &&
                     is_slot("label", TokenSlot::Kind::text_run, 0U, 1U) &&
                     is_slot("shadow", TokenSlot::Kind::box_shadow, 0U, 2U) &&
                     is_slot("gradient", TokenSlot::Kind::gradient_stop, 0U, 3U) &&
                     is_slot("zig", TokenSlot::Kind::path_stroke, 0U, 4U) &&
                     vellum::graphics::accept_scene(light.scene).accepted,
                 "every token slot kind is bound from the retained tree");

    const SlotTheme dark_theme{.fill = "#ef4444", .run = "#38bdf8", .shadow = "#a3e635cc",
                               .stop = "#f97316", .stroke = "#eab308"};
    vellum::authoring::RenderedApplication dark;
    if (!materialize(slot_tree(dark_theme), dark, error)) {
        std::cerr << "dark slot tree was not materialized: " << error << '\n';
        return false;
    }
    auto switched = light.scene;
    const auto result = vellum::graphics::apply_token_values(switched, dark.scene.tokens);
    ok &= expect(result.applied && result.changed.size() == 5U && result.nodes_updated == 5U,
                 "a theme switch re-resolves every slot's receipt");
    for (const auto* id : {"fill", "label", "shadow", "gradient", "zig"}) {
        const auto* before = find_node(light.scene, id);
        const auto* after = find_node(switched, id);
        const auto* expected = find_node(dark.scene, id);
        const auto colour = [&](const vellum::graphics::SceneNode& node) {
            return node.color_tokens.empty()
                ? node.fill
                : *vellum::graphics::token_slot_color(node, node.color_tokens[0]);
        };
        ok &= expect(before != nullptr && after != nullptr && expected != nullptr &&
                         !same_token_color(colour(*before), colour(*after)) &&
                         same_token_color(colour(*after), colour(*expected)),
                     std::string("the switched ") + id +
                         " slot equals materializing the new theme");
    }
    ok &= expect(vellum::graphics::make_paint_commands(switched).size() ==
                     vellum::graphics::make_paint_commands(dark.scene).size(),
                 "the switched scene lowers like the new theme");

    vellum::authoring::RenderedApplication unbound;
    ok &= expect(materialize(slot_tree({}, SlotBindings{false, false, false, false, false}),
                             unbound, error) &&
                     vellum::graphics::apply_token_values(unbound.scene, dark.scene.tokens)
                             .nodes_updated == 0U,
                 "control: without tokenRefs a theme switch rewrites nothing");

    const auto refused = [&](const std::string& json, std::string_view expected,
                             std::string_view what) {
        vellum::authoring::RenderedApplication kept;
        std::string why;
        std::vector<vellum::graphics::LoweringDiagnostic> failure;
        const bool failed = !materialize(json, kept, why, &failure);
        const bool reported = std::any_of(failure.begin(), failure.end(), [](const auto& item) {
            return item.code == vellum::graphics::kInvalidTokenReferenceCode &&
                   item.severity == vellum::graphics::LoweringDiagnostic::Severity::error;
        });
        return expect(failed && reported && why.find(expected) != std::string::npos,
                      std::string(what) + ": " + why);
    };
    const auto replace = [](std::string text, std::string_view from, std::string_view to) {
        const auto at = text.find(from);
        if (at != std::string::npos) text.replace(at, from.size(), to);
        return text;
    };
    const std::string tree = slot_tree({});
    ok &= refused(replace(tree, R"("tokenRefs":{"stroke":4})", R"("tokenRefs":{"bogus":4})"),
                  "is not a token slot of a path node", "an unknown slot is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"backgroundColor":0})",
                          R"("tokenRefs":{"stroke":0})"),
                  "is not a token slot of a view node", "a path slot on a view is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"color":1})", R"("tokenRefs":{"color":0})"),
                  "disagrees with its resolved colour", "a stale run colour is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"color":1})",
                          R"("tokenRefs":{"backgroundColor":1})"),
                  "may only name color", "a run slot other than color is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"backgroundLinearGradient.stops.0":3})",
                          R"("tokenRefs":{"backgroundLinearGradient.stops.7":3})"),
                  "names a colour the node does not have", "a missing stop is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"backgroundLinearGradient.stops.0":3})",
                          R"("tokenRefs":{"backgroundLinearGradient.stops.00":3})"),
                  "is not a token slot", "a non-canonical stop name is refused");
    ok &= refused(replace(tree, R"("tokenRefs":{"backgroundColor":0})",
                          R"("tokenRefs":{"boxShadow":0})"),
                  "names a colour the node does not have", "a shadow slot without a shadow");
    ok &= refused(replace(tree, R"("tokenRefs":{"stroke":4})", R"("tokenRefs":{"stroke":0})"),
                  "disagrees with its resolved colour", "a stale stroke is refused");

    vellum::authoring::RenderedApplication short_hex;
    const std::string short_tree = replace(
        replace(tree, R"("value":"#14b8a6")", R"("value":"#ffffff")"),
        R"("backgroundColor":"#14b8a6")", R"("backgroundColor":"#fff")");
    ok &= expect(materialize(short_tree, short_hex, error) &&
                     find_node(short_hex.scene, "fill")->fill_token == 0U &&
                     same_token_color(find_node(short_hex.scene, "fill")->fill,
                                      vellum::graphics::Color::hex(0xFFFFFF)),
                 "a short hex colour binds to its long-form receipt: " + error);

    vellum::authoring::RenderedApplication image;
    const std::string image_tree = std::string(
        R"({"protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{"type":"view",)"
        R"("id":"root","style":{"width":40,"height":40},"tokens":[{"path":"s","layer":"theme",)"
        R"("value":"#000000"}],"children":[{"type":"image","id":"pic","source":"sha256:)") +
        std::string(64, 'a') +
        R"(","tokenRefs":{"boxShadow":0},"style":{"width":10,"height":10,)"
        R"("boxShadow":{"color":"#000000"}}}]}})";
    ok &= expect(materialize(image_tree, image, error) &&
                     std::any_of(image.diagnostics.begin(), image.diagnostics.end(),
                                 [](const auto& item) {
                                     return item.property == "tokenRefs.boxShadow" &&
                                            item.code == vellum::graphics::kStyleDroppedCode;
                                 }),
                 "a slot the node type does not draw is reported as dropped: " + error);
    return ok;
}

// The envelope may name the scene version its tree was written for; the scene
// is built at that version and refused when it carries a newer field.
bool scene_version_protocol() {
    using vellum::authoring::testing::SlotBindings;
    using vellum::authoring::testing::slot_tree;
    bool ok = true;
    vellum::authoring::RenderedApplication current;
    std::string error;
    ok &= expect(vellum::authoring::materialize_rendered_tree(slot_tree({}), current, &error) &&
                     current.scene.scene_version == vellum::graphics::kSceneVersion,
                 "a tree that names no version is built at the current version");
    vellum::authoring::RenderedApplication named;
    ok &= expect(vellum::authoring::materialize_rendered_tree(
                     slot_tree({}, {}, R"("sceneVersion":7,)"), named, &error) &&
                     named.scene.scene_version == 7U,
                 "a tree that names version 7 is built at version 7");
    const std::string plain = std::string(
        R"({"protocol":"vellum.authoring-host.v2","kind":"render-result","sceneVersion":5,)"
        R"("tree":{"type":"view","id":"root","style":{"width":40,"height":40},"children":[)"
        R"({"type":"view","id":"box","style":{"width":10,"height":10,)"
        R"("backgroundColor":"#123456"}}]}})");
    vellum::authoring::RenderedApplication old;
    ok &= expect(vellum::authoring::materialize_rendered_tree(plain, old, &error) &&
                     old.scene.scene_version == 5U &&
                     vellum::graphics::accept_scene(old.scene).accepted,
                 "control: a version 5 tree with version 5 fields is built at version 5");
    const auto refused = [&](const std::string& json, std::string_view property,
                             std::string_view what) {
        vellum::authoring::RenderedApplication kept;
        std::string why;
        std::vector<vellum::graphics::LoweringDiagnostic> failure;
        const bool failed = !vellum::authoring::materialize_rendered_tree(json, kept, &why, &failure);
        return expect(failed && failure.size() == 1U &&
                          failure[0].code == vellum::graphics::kSceneVersionMismatchCode &&
                          failure[0].property == property && !why.empty(),
                      std::string(what) + ": " + why);
    };
    ok &= refused(slot_tree({}, SlotBindings{false, false, false, false, false},
                            R"("sceneVersion":5,)"),
                  "tokens", "a version 5 tree carrying a token table is refused");
    ok &= refused(slot_tree({}, {}, R"("sceneVersion":6,)"), "fill_token",
                  "a version 6 tree carrying token references is refused");
    ok &= refused(slot_tree({}, {}, R"("sceneVersion":0,)"), "sceneVersion",
                  "version 0 is refused");
    ok &= refused(slot_tree({}, {}, R"("sceneVersion":99,)"), "sceneVersion",
                  "a future version is refused");
    ok &= refused(slot_tree({}, {}, R"("sceneVersion":"7",)"), "sceneVersion",
                  "a version that is not a number is refused");
    return ok;
}

// Tracks on a node become `SceneNode::tracks`, drawn at the scene clock; a
// malformed or invalid track fails materialization with the node named.
bool animation_tracks() {
    using vellum::graphics::AnimatedProperty;
    using vellum::graphics::BlendMode;
    using vellum::graphics::EasingKind;
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto tree = wrap_tree(R"JSON(
      {"type":"view","id":"pulse","style":{"x":10,"y":10,"width":40,"height":40,
         "backgroundColor":"#FFFFFF","boxShadow":{"color":"#000000","blurRadius":4},
         "mixBlendMode":"multiply"},
       "tracks":[
         {"property":"opacity","keyframes":[
           {"time":0,"value":0,"easing":"ease"},
           {"time":1,"value":1,"easing":{"type":"steps","steps":3,"position":"start"}},
           {"time":2,"value":0.5}]},
         {"property":"translateX","keyframes":[
           {"time":0,"value":0,"easing":{"type":"spring","stiffness":170,"damping":26}},
           {"time":4,"value":30}]},
         {"property":"fill","keyframes":[{"time":0,"value":"#FF0000"},{"time":1,"value":"#0000FF80"}]},
         {"property":"shadowColor","index":0,"keyframes":[{"time":0,"value":"#000000"}]}],
       "children":[]}
    )JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "tracks: " << error << '\n';
        return false;
    }
    const auto* pulse = vellum::graphics::find_node(rendered.scene, "pulse");
    if (pulse == nullptr || pulse->tracks.size() != 4U ||
        pulse->blend_mode != BlendMode::multiply ||
        pulse->tracks[0].property != AnimatedProperty::opacity ||
        pulse->tracks[0].keyframes.size() != 3U ||
        pulse->tracks[0].keyframes[0].easing.kind != EasingKind::cubic_bezier ||
        !near(pulse->tracks[0].keyframes[0].easing.x1, 0.25F) ||
        pulse->tracks[0].keyframes[1].easing.kind != EasingKind::steps ||
        pulse->tracks[0].keyframes[1].easing.steps != 3U ||
        pulse->tracks[0].keyframes[1].easing.step_position !=
            vellum::graphics::StepPosition::jump_start ||
        pulse->tracks[1].keyframes[0].easing.kind != EasingKind::spring ||
        pulse->tracks[1].keyframes[0].easing.stiffness != 170.0F ||
        pulse->tracks[2].keyframes[1].color.blue != 1.0F ||
        !near(pulse->tracks[2].keyframes[1].color.alpha, 128.0F / 255.0F) ||
        rendered.scene.scene_version != vellum::graphics::kSceneVersion) {
        std::cerr << "tracks: the node's tracks were not materialized as written\n";
        return false;
    }
    auto at = rendered.scene;
    at.clock = 1.5;
    const auto commands = vellum::graphics::make_paint_commands(at);
    const auto drawn = std::find_if(commands.begin(), commands.end(),
        [](const auto& command) { return command.node_id == "pulse"; });
    if (drawn == commands.end() || !near(drawn->opacity, 2.0F / 3.0F)) {
        std::cerr << "tracks: the materialized scene did not draw at its clock\n";
        return false;
    }
    auto older = rendered.scene;
    older.scene_version = 10U;
    const auto refused = vellum::graphics::scene_loss_diagnostics(older);
    if (refused.size() != 1U || refused[0].code != vellum::graphics::kSceneVersionMismatchCode ||
        refused[0].property != "tracks" || refused[0].node_id != "pulse") {
        std::cerr << "tracks: a version 10 copy was not refused for its tracks\n";
        return false;
    }

    const auto failing = [&](std::string_view tracks, std::string_view expected) {
        vellum::authoring::RenderedApplication kept;
        std::string why;
        const auto bad = wrap_tree(std::string(R"JSON({"type":"view","id":"bad",
            "style":{"width":10,"height":10},"children":[],"tracks":)JSON") +
            std::string(tracks) + "}");
        const bool ok = !vellum::authoring::materialize_rendered_tree(bad, kept, &why) &&
                        why == expected;
        if (!ok) std::cerr << "tracks: expected \"" << expected << "\", got \"" << why << "\"\n";
        return ok;
    };
    return failing(R"({"property":"opacity"})", "node bad tracks: must be an array") &&
           failing(R"([{"property":"blur","keyframes":[]}])",
                   "node bad tracks: a track has an unknown property") &&
           failing(R"([{"property":"fill","keyframes":[{"time":0,"value":0.5}]}])",
                   "node bad tracks: a colour keyframe value must be #rrggbb or #rrggbbaa") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":0,"value":0,"easing":"bounce"}]}])",
                   "node bad tracks: easing bounce is not linear, ease, ease-in, ease-out or "
                   "ease-in-out") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":1,"value":0},{"time":0,"value":1}]}])",
                   "node bad tracks: keyframe 1 does not come after the one before it") &&
           failing(R"([{"property":"shadowColor","index":2,"keyframes":[{"time":0,"value":"#000000"}]}])",
                   "node bad tracks: a shadow colour track names shadow 2, which the node does "
                   "not have") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":0,"value":0}]},
                       {"property":"opacity","keyframes":[{"time":0,"value":1}]}])",
                   "node bad tracks: two tracks animate the same property") &&
           failing(R"([{"property":"opacity","foo":1,"keyframes":[{"time":0,"value":0}]}])",
                   "node bad tracks: the opacity track does not take foo") &&
           failing(R"([{"property":"opacity","index":1,"keyframes":[{"time":0,"value":0}]}])",
                   "node bad tracks: the opacity track does not take index") &&
           failing(R"([{"property":"shadowColor","stop":1,"keyframes":[{"time":0,"value":"#000000"}]}])",
                   "node bad tracks: the shadowColor track does not take stop") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":0,"value":0,"clock":2}]}])",
                   "node bad tracks: a keyframe does not take clock") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":0,"value":0,
                       "easing":{"type":"spring","stiffnes":90}}]}])",
                   "node bad tracks: spring easing does not take stiffnes") &&
           failing(R"([{"property":"opacity","keyframes":[{"time":0,"value":0,
                       "easing":{"type":"spring","stiffness":100,"damping":10}},
                       {"time":1,"value":1}]}])",
                   "node bad tracks: keyframe 1 comes before the spring from keyframe 0 settles");
}

// `mixBlendMode` reaches the scene in the canonical and CSS spellings on every
// node type, and an unknown name fails the tree atomically.
bool blend_mode_key() {
    using vellum::graphics::BlendMode;
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    const auto tree = wrap_tree(R"JSON(
      {"type":"view","id":"multiplied","style":{"width":20,"height":20,
        "backgroundColor":"#FF0000","mixBlendMode":"multiply"},"children":[]},
      {"type":"view","id":"dodged","style":{"width":20,"height":20,
        "mixBlendMode":"color-dodge"},"children":[]},
      {"type":"view","id":"keyed","style":{"width":20,"height":20,
        "mixBlendMode":"destination_in"},"children":[]},
      {"type":"text","id":"caption","text":"Hi","style":{"height":20,
        "mixBlendMode":"difference"},"children":[]},
      {"type":"path","id":"shape","d":"M0 0 L10 10","stroke":"#FF0000",
        "style":{"width":20,"height":20,"mixBlendMode":"screen"}},
      {"type":"view","id":"plain","style":{"width":20,"height":20},"children":[]}
    )JSON");
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error)) {
        std::cerr << "blend mode: " << error << '\n';
        return false;
    }
    const auto mode = [&](std::string_view id) {
        const auto* node = vellum::graphics::find_node(rendered.scene, id);
        return node == nullptr ? BlendMode::clear : node->blend_mode;
    };
    if (mode("multiplied") != BlendMode::multiply || mode("dodged") != BlendMode::color_dodge ||
        mode("keyed") != BlendMode::destination_in || mode("caption") != BlendMode::difference ||
        mode("shape") != BlendMode::screen || mode("plain") != BlendMode::normal) {
        std::cerr << "blend mode: mixBlendMode did not reach the scene\n";
        return false;
    }
    if (rendered.scene.scene_version != vellum::graphics::kSceneVersion ||
        !vellum::graphics::scene_fields_match_version(rendered.scene) ||
        !rendered.diagnostics.empty()) {
        std::cerr << "blend mode: the scene is not a clean version " << vellum::graphics::kSceneVersion
                  << " scene\n";
        return false;
    }
    const auto commands = vellum::graphics::make_paint_commands(rendered.scene);
    const auto layer = std::find_if(commands.begin(), commands.end(), [](const auto& command) {
        return command.kind == vellum::graphics::PaintCommand::Kind::begin_layer &&
               command.node_id == "multiplied";
    });
    if (layer == commands.end() || layer->blend_mode != BlendMode::multiply) {
        std::cerr << "blend mode: the blend did not reach a paint-command layer\n";
        return false;
    }
    const auto width_before = rendered.scene.width;
    for (const char* bad : {R"("mixBlendMode":"plus-lighter")", R"("mixBlendMode":"Multiply")",
                            R"("mixBlendMode":3)", R"("mixBlendMode":"")"}) {
        const auto refused = wrap_tree(std::string(R"JSON({"type":"view","id":"bad","style":{)JSON") +
                                       bad + R"JSON(},"children":[]})JSON");
        if (vellum::authoring::materialize_rendered_tree(refused, rendered, &error) ||
            error != "node bad mixBlendMode must be a blend mode name" ||
            rendered.scene.width != width_before) {
            std::cerr << "blend mode: '" << bad << "' gave '" << error << "'\n";
            return false;
        }
    }
    const auto masked = [](std::string_view blend) {
        return wrap_tree(std::string(R"JSON(
          {"type":"view","id":"content","style":{"width":20,"height":20},"children":[]},
          {"type":"view","id":"cutout","style":{"width":10,"height":10,"maskMode":"alpha",
            "mixBlendMode":")JSON") + std::string(blend) + R"JSON("},
            "tracks":[{"property":"opacity","keyframes":[
              {"time":0,"value":0},{"time":1,"value":1}]}],"children":[]})JSON");
    };
    if (vellum::authoring::materialize_rendered_tree(masked("multiply"), rendered, &error) ||
        error != "node cutout mixBlendMode cannot apply to a mask" ||
        rendered.scene.width != width_before) {
        std::cerr << "blend mode: a blended mask gave '" << error << "'\n";
        return false;
    }
    if (!vellum::authoring::materialize_rendered_tree(masked("normal"), rendered, &error)) {
        std::cerr << "blend mode: a mask with the normal mode was refused: " << error << '\n';
        return false;
    }
    const auto* animated_mask = vellum::graphics::find_node(rendered.scene, "cutout");
    if (animated_mask == nullptr || animated_mask->tracks.size() != 1U) {
        std::cerr << "blend mode: a normal mask did not retain its animation track\n";
        return false;
    }
    return true;
}

// Protocol integers are checked as doubles before any integer conversion.
bool protocol_numbers() {
    bool ok = true;
    std::uint64_t value = 7;
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double infinity = std::numeric_limits<double>::infinity();
    for (const double bad : {1.0e30, -1.0, 9007199254740992.0, nan, infinity, -infinity, 1.5,
                             1.00000001, 4.0}) {
        value = 7;
        ok &= expect(!vellum::authoring::protocol_integer(bad, 4U, value) && value == 7U,
                     "protocol integer " + std::to_string(bad) + " below 4 is refused");
    }
    ok &= expect(vellum::authoring::protocol_integer(3.0, 4U, value) && value == 3U &&
                     vellum::authoring::protocol_integer(0.0, 1U, value) && value == 0U &&
                     vellum::authoring::protocol_integer(4294967295.0, 4294967296ULL, value) &&
                     value == 4294967295ULL,
                 "control: integers below the limit are read exactly");

    const auto token_index = [](std::string_view index) {
        return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":"view","id":"root","style":{"width":40,"height":40},
            "tokens":[{"path":"a","layer":"theme","value":"#112233"},
                      {"path":"b","layer":"theme","value":"#445566"}],
            "children":[{"type":"view","id":"box","tokenRefs":{"backgroundColor":)JSON") +
               std::string(index) +
               R"JSON(},"style":{"width":10,"height":10,"backgroundColor":"#445566"}}]}})JSON";
    };
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    ok &= expect(vellum::authoring::materialize_rendered_tree(token_index("1"), rendered, &error) &&
                     vellum::graphics::find_node(rendered.scene, "box")->fill_token == 1U,
                 "control: a whole token index binds: " + error);
    for (const auto* bad : {"1e30", "-1", "9007199254740992", "1.00000001", "1.5", "2"}) {
        ok &= expect(!vellum::authoring::materialize_rendered_tree(token_index(bad), rendered,
                                                                   &error) &&
                         error.find("must name an entry of the token table") != std::string::npos,
                     std::string("token index ") + bad + " is refused: " + error);
    }

    const auto versioned = [](std::string_view version) {
        return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "sceneVersion":)JSON") + std::string(version) +
               R"JSON(,"tree":{"type":"view","id":"root","style":{"width":40,"height":40},
            "children":[]}})JSON";
    };
    ok &= expect(vellum::authoring::materialize_rendered_tree(versioned("5"), rendered, &error) &&
                     rendered.scene.scene_version == 5U,
                 "control: a whole scene version is read: " + error);
    for (const auto* bad : {"7.00000001", "4294967295", "4294967296", "1e30", "-1",
                            "9007199254740992", "0"}) {
        std::vector<vellum::graphics::LoweringDiagnostic> failure;
        ok &= expect(!vellum::authoring::materialize_rendered_tree(versioned(bad), rendered,
                                                                   &error, &failure) &&
                         failure.size() == 1U && failure[0].property == "sceneVersion",
                     std::string("scene version ") + bad + " is refused: " + error);
    }

    const auto input = [](std::string_view fields) {
        return wrap_tree(std::string(R"JSON({"type":"text-input","id":"field","value":"abc",)JSON") +
                         std::string(fields) +
                         R"JSON(,"events":{"change":"field:change"},"style":{"width":80,"height":20}})JSON");
    };
    ok &= expect(vellum::authoring::materialize_rendered_tree(
                     input(R"("primitiveVersion":1,"selection":{"start":1,"end":3})"), rendered,
                     &error) &&
                     rendered.text_inputs.size() == 1U &&
                     rendered.text_inputs[0].selection_start == 1U &&
                     rendered.text_inputs[0].selection_end == 3U,
                 "control: a whole selection inside the value is read: " + error);
    for (const auto* bad : {R"("primitiveVersion":1.5)", R"("primitiveVersion":true)",
                            R"("primitiveVersion":1,"selection":{"start":1e30,"end":1e30})",
                            R"("primitiveVersion":1,"selection":{"start":-1,"end":2})",
                            R"("primitiveVersion":1,"selection":{"start":0.5,"end":2})",
                            R"("primitiveVersion":1,"selection":{"start":0,"end":4})"}) {
        ok &= expect(!vellum::authoring::materialize_rendered_tree(input(bad), rendered, &error),
                     std::string("text input ") + bad + " is refused");
    }

    ok &= expect(vellum::authoring::materialize_rendered_tree(
                     wrap_tree(R"({"type":"text","id":"heavy","text":"w",)"
                               R"("style":{"width":40,"height":20,"fontWeight":1e30}})"),
                     rendered, &error) &&
                     vellum::graphics::find_node(rendered.scene, "heavy")->font_weight == 900,
                 "a huge font weight clamps to 900 before rounding: " + error);
    return ok;
}

// The root's backgroundColor is the scene's clear colour whatever the root's
// kind, so its receipt follows a theme switch for every kind.
bool root_background_receipts() {
    bool ok = true;
    const auto root = [](std::string_view type, std::string_view extra, std::string_view color) {
        return std::string(R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":")JSON") + std::string(type) + R"JSON(","id":"root",)JSON" +
               std::string(extra) + R"JSON("style":{"width":80,"height":40,"backgroundColor":")JSON" +
               std::string(color) + R"JSON("},"tokenRefs":{"backgroundColor":0},
            "tokens":[{"path":"canvas","layer":"theme","value":")JSON" + std::string(color) +
               R"JSON("}],"children":[]}})JSON";
    };
    struct Kind final {
        const char* type;
        const char* extra;
    };
    for (const Kind kind : {Kind{"text", R"("text":"hello",)"}, Kind{"view", ""},
                            Kind{"custom", R"("component":"meter","properties":{},)"},
                            Kind{"path", R"("d":"M0 0 L10 10",)"}}) {
        vellum::authoring::RenderedApplication light;
        vellum::authoring::RenderedApplication dark;
        std::string error;
        const bool built =
            vellum::authoring::materialize_rendered_tree(root(kind.type, kind.extra, "#0f172a"),
                                                         light, &error) &&
            vellum::authoring::materialize_rendered_tree(root(kind.type, kind.extra, "#f8fafc"),
                                                         dark, &error);
        if (!expect(built, std::string(kind.type) + " root with a background token: " + error)) {
            ok = false;
            continue;
        }
        auto switched = light.scene;
        const auto result = vellum::graphics::apply_token_values(switched, dark.scene.tokens);
        ok &= expect(light.scene.background_token == 0U &&
                         vellum::graphics::accept_scene(light.scene).accepted &&
                         result.applied &&
                         vellum::graphics::same_token_color(switched.background,
                                                            dark.scene.background) &&
                         !vellum::graphics::same_token_color(switched.background,
                                                             light.scene.background),
                     std::string("a ") + kind.type +
                         " root keeps its background receipt through a theme switch");
    }
    return ok;
}

// Every node kind that draws or drops a fill, shadow, gradient stop or text
// colour validates its receipt the same way: a reference that disagrees with
// its authored colour is refused whether the kind keeps the paint or drops it.
bool token_kind_audit() {
    using vellum::graphics::find_node;
    using vellum::graphics::same_token_color;
    bool ok = true;
    const std::string hash = "sha256:" + std::string(64, 'a');
    const auto plot_extra = std::string(
        R"("plot":"line","channel":"c","source":"scalar","slots":8,)");
    struct Kind final {
        std::string type;
        std::string extra;
    };
    const std::vector<Kind> kinds = {
        {"view", ""},
        {"stack", ""},
        {"text", R"("text":"hi",)"},
        {"button", R"("text":"Go","events":{"press":"go"},)"},
        {"text-input", R"("primitiveVersion":1,"value":"x","events":{"change":"input:change"},)"},
        {"image", std::string(R"("source":")") + hash + R"(",)"},
        {"plot", plot_extra},
        {"canvas", ""},
        {"custom", R"("component":"meter","properties":{},)"},
    };
    const auto tree = [](const Kind& kind, std::string_view slot, std::string_view color,
                         std::string_view receipt, bool root) {
        const std::string style = std::string(R"("style":{"width":40,"height":20,)") +
            R"("color":")" + std::string(color) + R"(","backgroundColor":")" + std::string(color) +
            R"(","boxShadow":{"color":")" + std::string(color) +
            R"("},"backgroundLinearGradient":{"angle":90,"stops":[{"position":0,"color":")" +
            std::string(color) + R"("},{"position":1,"color":"#ffffff"}]}})";
        const std::string body = std::string(R"({"type":")") + kind.type + R"(","id":"n",)" +
            kind.extra + R"("tokenRefs":{")" + std::string(slot) + R"(":0},)" + style;
        const std::string node = body + "}";
        const std::string table = std::string(R"("tokens":[{"path":"t","layer":"theme","value":")") +
            std::string(receipt) + R"("}])";
        if (root) {
            return std::string(R"({"protocol":"vellum.authoring-host.v2","kind":"render-result",
              "tree":)") + body + "," + table + R"(,"children":[]}})";
        }
        return std::string(R"({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":"stack","id":"r","style":{"width":100,"height":100},)") + table +
            R"(,"children":[)" + node + "]}}";
    };
    for (const auto& kind : kinds) {
        for (const char* slot :
             {"backgroundColor", "boxShadow", "backgroundLinearGradient.stops.0", "color"}) {
            if (kind.type == "text" && std::string_view(slot) != "color" &&
                std::string_view(slot) != "backgroundColor") {
                continue;
            }
            vellum::authoring::RenderedApplication good;
            vellum::authoring::RenderedApplication stale;
            std::string error;
            const std::string label = kind.type + " " + slot;
            const bool accepted = vellum::authoring::materialize_rendered_tree(
                tree(kind, slot, "#445566", "#445566", false), good, &error);
            ok &= expect(accepted, "control: " + label + " with a matching receipt: " + error);
            const bool refused = !vellum::authoring::materialize_rendered_tree(
                tree(kind, slot, "#112233", "#445566", false), stale, &error);
            ok &= expect(refused, label + " with a stale receipt is refused");
        }
    }
    for (const auto& kind : kinds) {
        if (kind.type != "image" && kind.type != "plot") continue;
        vellum::authoring::RenderedApplication missing;
        std::string error;
        ok &= expect(!vellum::authoring::materialize_rendered_tree(
                         tree(kind, "backgroundLinearGradient.stops.7", "#445566", "#445566",
                              false),
                         missing, &error),
                     kind.type + " dropped gradient reference to a missing stop is refused");
    }
    for (const char* type : {"plot"}) {
        for (const bool root : {false, true}) {
            const Kind kind{type, plot_extra};
            const auto build = [&](std::string_view color) {
                vellum::authoring::RenderedApplication app;
                std::string error;
                const bool built = vellum::authoring::materialize_rendered_tree(
                    tree(kind, "backgroundColor", color, color, root), app, &error);
                return std::make_pair(built, std::move(app));
            };
            auto light = build("#445566");
            auto dark = build("#aabbcc");
            if (!expect(light.first && dark.first,
                        std::string(root ? "root" : "non-root") + " plot with a background token")) {
                ok = false;
                continue;
            }
            const auto* before = find_node(light.second.scene, "n");
            auto switched = light.second.scene;
            const auto result =
                vellum::graphics::apply_token_values(switched, dark.second.scene.tokens);
            const auto* after = find_node(switched, "n");
            const auto* expected = find_node(dark.second.scene, "n");
            ok &= expect(before != nullptr && before->fill_token && result.applied &&
                             after != nullptr && expected != nullptr &&
                             same_token_color(after->fill, expected->fill) &&
                             !same_token_color(after->fill, before->fill),
                         std::string(root ? "a root" : "a non-root") +
                             " plot background follows its token through a theme switch");
        }
    }
    const auto with_run = [](std::string_view run_attributes, std::string_view button_color) {
        return std::string(R"({"protocol":"vellum.authoring-host.v2","kind":"render-result",
          "tree":{"type":"stack","id":"r","style":{"width":100,"height":100},
          "tokens":[{"path":"t","layer":"theme","value":"#445566"}],"children":[
          {"type":"button","id":"b","events":{"press":"go"},"style":{"width":60,"height":20)") +
            std::string(button_color) + R"(},"children":[{"type":"text-run","id":"b/run","text":"Go",)" +
            std::string(run_attributes) + "}]}]}}";
    };
    const auto try_run = [&](std::string_view attributes, std::string_view color) {
        vellum::authoring::RenderedApplication app;
        std::string error;
        const bool built = vellum::authoring::materialize_rendered_tree(
            with_run(attributes, color), app, &error);
        return std::make_pair(built, std::move(app));
    };
    const auto control = try_run(R"("tokenRefs":{"color":0},"style":{"color":"#445566"})", "");
    ok &= expect(control.first && std::any_of(control.second.diagnostics.begin(),
                                              control.second.diagnostics.end(), [](const auto& item) {
                     return item.node_id == "b/run" && item.property == "tokenRefs.color" &&
                            item.code == vellum::graphics::kStyleDroppedCode;
                 }),
                 "control: a button text-run with a matching colour token is dropped with a diagnostic");
    ok &= expect(!try_run(R"("tokenRefs":{"color":1e30},"style":{"color":"#445566"})", "").first,
                 "a button text-run reference past the table is refused");
    ok &= expect(!try_run(R"("tokenRefs":{"color":0},"style":{"color":"#112233"})", "").first,
                 "a button text-run with a stale receipt is refused");
    ok &= expect(!try_run(R"("tokenRefs":{"bogus":0},"style":{"color":"#445566"})", "").first,
                 "a button text-run slot other than color is refused");
    ok &= expect(try_run(R"("tokenRefs":{"color":0})", R"(,"color":"#445566")").first,
                 "control: a button text-run inherits the button colour for its receipt");
    ok &= expect(!try_run(R"("tokenRefs":{"color":0})", R"(,"color":"#112233")").first,
                 "a button text-run inheriting a different colour is refused");
    return ok;
}

bool structured_box_paint() {
    constexpr std::string_view tree = R"JSON({"protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
      "type":"view","id":"paint","style":{"width":40,"height":30,
      "border":{"width":2,"style":"solid","color":"#112233"},"borderTopLeftRadius":3,
      "borderTopRightRadius":5,"borderBottomRightRadius":7,"borderBottomLeftRadius":9,
      "boxShadow":[{"offsetX":1,"offsetY":2,"blurRadius":3,"color":"#00000080"},
      {"offsetX":-1,"offsetY":-2,"blurRadius":1,"color":"#ffffff40"}]},"children":[]}})JSON";
    vellum::authoring::RenderedApplication rendered;
    std::string error;
    std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
    if (!vellum::authoring::materialize_rendered_tree(tree, rendered, &error, &diagnostics)) {
        std::cerr << error << '\n';
        return false;
    }
    const auto* node = vellum::graphics::find_node(rendered.scene.root, "paint");
    return node && node->border_width == 2.0F && node->box_shadows.size() == 2U &&
           node->corner_radii && node->corner_radii->top_left == 3.0F &&
           node->corner_radii->bottom_left == 9.0F;
}

int main() {
    if (!vector_nodes()) return 1;
    if (!gradient_parity()) return 1;
    if (!vector_background_dropped()) return 1;
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
        card->fill_gradients[0].interpolation !=
            vellum::graphics::GradientInterpolation::linear_srgb ||
        card->fill_gradients[0].color_space != "display-p3" ||
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

    const auto gradient_tree = [](std::string_view fields) {
        return std::string(R"JSON({
      "protocol":"vellum.authoring-host.v2","kind":"render-result","tree":{
        "type":"view","id":"gradient-fields",
        "style":{"width":100,"height":60,"backgroundGradient":{)JSON") +
               std::string(fields) + R"JSON(,"stops":[
            {"position":0,"color":"#000000"},{"position":1,"color":"#ffffff"}
          ]}},"children":[]}})JSON";
    };
    const std::pair<std::string_view, std::string_view> malformed_gradients[]{
        {R"("kind":7)", "backgroundGradient.kind must be linear, radial, two-circle-radial or conic"},
        {R"("kind":"spiral")",
         "backgroundGradient.kind must be linear, radial, two-circle-radial or conic"},
        {R"("spread":1)", "backgroundGradient.spread must be pad, repeat or reflect"},
        {R"("spread":"wrap")", "backgroundGradient.spread must be pad, repeat or reflect"},
        {R"("spread":"pad","repeating":true)",
         "backgroundGradient.repeating contradicts backgroundGradient.spread; repeating is true "
         "exactly when spread is repeat or reflect"},
        {R"("spread":"reflect","repeating":false)",
         "backgroundGradient.repeating contradicts backgroundGradient.spread; repeating is true "
         "exactly when spread is repeat or reflect"},
        {R"("kind":"radial","radiusX":-0.5)", "backgroundGradient.radiusX must be non-negative"},
        {R"("kind":"radial","radiusY":-1)", "backgroundGradient.radiusY must be non-negative"},
        {R"("kind":"two-circle-radial","startRadius":-0.1)",
         "backgroundGradient.startRadius must be non-negative"},
        {R"("kind":"two-circle-radial","endRadius":-0.1)",
         "backgroundGradient.endRadius must be non-negative"},
        {R"("kind":"radial","angle":30)",
         "backgroundGradient.angle does not apply to a radial gradient"},
        {R"("kind":"two-circle-radial","angle":30)",
         "backgroundGradient.angle does not apply to a two-circle-radial gradient"},
        {R"("angle":90,"centerX":0.2)",
         "backgroundGradient.centerX does not apply to a linear gradient"},
        {R"("kind":"conic","focalY":0.2)",
         "backgroundGradient.focalY does not apply to a conic gradient"},
        {R"("kind":"radial","focalX":0.2)",
         "backgroundGradient.focalX does not apply to a radial gradient"},
    };
    for (const auto& [fields, expected] : malformed_gradients) {
        if (vellum::authoring::materialize_rendered_tree(gradient_tree(fields), rendered,
                                                         &error) ||
            error != expected || rendered.scene.width != styled_width) {
            std::cerr << "malformed gradient " << fields << " gave: " << error << "\n";
            return 1;
        }
    }
    if (!vellum::authoring::materialize_rendered_tree(
            gradient_tree(R"("kind":"two-circle-radial","focalX":0.25,"focalY":0.5,)"
                          R"("startRadius":0,"endRadius":0.75,"spread":"reflect","repeating":true)"),
            rendered, &error)) {
        std::cerr << "two-circle gradient did not materialize: " << error << "\n";
        return 1;
    }
    {
        const auto* node = vellum::graphics::find_node(rendered.scene, "gradient-fields");
        if (node == nullptr || node->fill_gradients.size() != 1U ||
            node->fill_gradients[0].kind != vellum::graphics::GradientKind::two_circle_radial ||
            node->fill_gradients[0].start_radius != 0.0F ||
            node->fill_gradients[0].end_radius != 0.75F ||
            node->fill_gradients[0].focal_x != 0.25F ||
            node->fill_gradients[0].spread != vellum::graphics::GradientSpread::reflect ||
            node->fill_gradients[0].radius_x != 0.0F) {
            std::cerr << "two-circle gradient fields were not carried\n";
            return 1;
        }
    }
    if (!vellum::authoring::materialize_rendered_tree(
            kRenderedTree, rendered, &error)) {
        std::cerr << "known-good tree did not rematerialize: " << error << "\n";
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



    return box_material() && rounded_border_corner_pixel_regression() && compositing_keys() &&
           transform_pivots_once() && transform_ignores_locale() &&
           image_nodes() && path_nodes() && plot_nodes() && grouping_keys() &&
           animation_tracks() && blend_mode_key() && token_receipts() && token_slots() &&
           scene_version_protocol() && protocol_numbers() && root_background_receipts() &&
           token_kind_audit() && structured_box_paint()
               ? 0
               : 1;
}
