#include <vellum/graphics/hit_test.hpp>

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

void check(bool condition, const char* expression, int line) {
    if (condition) return;
    ++failures;
    std::cerr << "hit_test_test.cpp:" << line << ": check failed: " << expression << "\n";
}

#define CHECK(expression) check((expression), #expression, __LINE__)

const Color kRed = Color::hex(0xFF0000);
const Color kClear = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);

Scene make_scene() {
    Scene scene;
    scene.width = 400.0F;
    scene.height = 400.0F;
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, 400.0F, 400.0F};
    return scene;
}

SceneNode rect(std::string id, Rect bounds, Color fill = kRed) {
    SceneNode node;
    node.id = std::move(id);
    node.kind = SceneNode::Kind::rectangle;
    node.bounds = bounds;
    node.fill = fill;
    return node;
}

Affine2D rotation(float degrees) {
    const float radians = degrees * 3.14159265358979F / 180.0F;
    return {.a = std::cos(radians), .b = std::sin(radians), .c = -std::sin(radians),
            .d = std::cos(radians)};
}

std::optional<NodeId> pick_at(const Scene& scene, float x, float y, float tolerance = 0.0F) {
    return pick(scene, {x, y}, {.tolerance = tolerance});
}

std::optional<NodeId> pick_interactive(
    const Scene& scene, float x, float y, const std::unordered_set<NodeId>& ids) {
    return pick(scene, {x, y}, {.tolerance = 0.0F, .interactive = &ids});
}

std::shared_ptr<PathGeometry> make_path(std::vector<PathVerb> verbs, std::vector<float> values) {
    auto path = std::make_shared<PathGeometry>();
    path->verbs = std::move(verbs);
    path->values = std::move(values);
    return path;
}

std::shared_ptr<PathGeometry> circle(float cx, float cy, float radius) {
    return make_path(
        {PathVerb::move, PathVerb::arc, PathVerb::arc, PathVerb::close},
        {cx - radius, cy, radius, radius, 0.0F, 1.0F, 0.0F, cx + radius, cy, radius, radius,
         0.0F, 1.0F, 0.0F, cx - radius, cy});
}

std::shared_ptr<PathGeometry> square(float x0, float y0, float x1, float y1) {
    return make_path({PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line,
                      PathVerb::close},
                     {x0, y0, x1, y0, x1, y1, x0, y1});
}

SceneNode path_node(std::string id, Rect bounds, std::shared_ptr<const PathGeometry> path,
                    Color fill) {
    SceneNode node;
    node.id = std::move(id);
    node.kind = SceneNode::Kind::path;
    node.bounds = bounds;
    node.fill = fill;
    node.path = std::move(path);
    return node;
}

void rotated_rectangle() {
    auto scene = make_scene();
    auto node = rect("r", {100.0F, 100.0F, 100.0F, 40.0F});
    node.transform = rotation(45.0F);
    scene.root.children.push_back(node);
    const float c = std::cos(0.785398F);
    CHECK(pick_at(scene, 100.0F + c * 50.0F - c * 20.0F, 100.0F + c * 50.0F + c * 20.0F) == "r");
    CHECK(!pick_at(scene, 195.0F, 101.0F));
    CHECK(!pick_at(scene, 190.0F, 138.0F));
    CHECK(!pick_at(scene, 150.0F, 120.0F));
    const std::unordered_set<NodeId> ids{"r"};
    CHECK(pick_interactive(scene, 100.0F + c * 50.0F, 100.0F + c * 50.0F, ids) == "r");
    CHECK(!pick_interactive(scene, 195.0F, 101.0F, ids));
}

void nested_transform() {
    auto scene = make_scene();
    SceneNode parent;
    parent.id = "parent";
    parent.bounds = {50.0F, 50.0F, 200.0F, 200.0F};
    parent.transform = Affine2D{.a = 2.0F, .d = 2.0F};
    parent.children.push_back(rect("child", {10.0F, 10.0F, 20.0F, 20.0F}));
    scene.root.children.push_back(parent);
    CHECK(pick_at(scene, 100.0F, 100.0F) == "child");
    CHECK(pick_at(scene, 109.0F, 71.0F) == "child");
    CHECK(!pick_at(scene, 115.0F, 100.0F));
    CHECK(!pick_at(scene, 65.0F, 65.0F));

    auto inner = make_scene();
    SceneNode outer;
    outer.id = "outer";
    outer.bounds = {100.0F, 0.0F, 100.0F, 100.0F};
    outer.transform = rotation(90.0F);
    SceneNode middle;
    middle.id = "middle";
    middle.bounds = {10.0F, 0.0F, 80.0F, 80.0F};
    middle.transform = Affine2D{.a = 0.5F, .d = 0.5F};
    middle.children.push_back(rect("leaf", {0.0F, 0.0F, 40.0F, 10.0F}));
    outer.children.push_back(middle);
    inner.root.children.push_back(outer);
    CHECK(pick_at(inner, 100.0F - 2.0F, 5.0F + 10.0F) == "leaf");
    CHECK(!pick_at(inner, 100.0F + 2.0F, 5.0F + 10.0F));
    CHECK(!pick_at(inner, 100.0F - 8.0F, 5.0F + 10.0F));
}

void clipped_child() {
    auto scene = make_scene();
    SceneNode parent;
    parent.id = "parent";
    parent.bounds = {0.0F, 0.0F, 100.0F, 100.0F};
    parent.clip = NodeClip{.rect = {0.0F, 0.0F, 50.0F, 50.0F}};
    parent.children.push_back(rect("child", {0.0F, 0.0F, 100.0F, 100.0F}));
    scene.root.children.push_back(parent);
    CHECK(pick_at(scene, 30.0F, 30.0F) == "child");
    CHECK(!pick_at(scene, 80.0F, 80.0F));
    CHECK(!pick_at(scene, 30.0F, 80.0F));

    auto subtree_only = make_scene();
    auto own = rect("own", {0.0F, 0.0F, 100.0F, 100.0F});
    own.clip = NodeClip{.rect = {0.0F, 0.0F, 50.0F, 50.0F}, .clips_own_paint = false};
    own.children.push_back(rect("kid", {0.0F, 0.0F, 100.0F, 100.0F}));
    subtree_only.root.children.push_back(own);
    CHECK(pick_at(subtree_only, 30.0F, 30.0F) == "kid");
    CHECK(pick_at(subtree_only, 80.0F, 80.0F) == "own");

    auto rounded = make_scene();
    auto round_parent = rect("round", {0.0F, 0.0F, 100.0F, 100.0F});
    round_parent.clip = NodeClip{.rect = {0.0F, 0.0F, 100.0F, 100.0F},
                                 .radii = uniform_corner_radii(50.0F)};
    rounded.root.children.push_back(round_parent);
    CHECK(pick_at(rounded, 50.0F, 50.0F) == "round");
    CHECK(!pick_at(rounded, 5.0F, 5.0F));

    auto path_clipped = make_scene();
    auto shaped = rect("shaped", {0.0F, 0.0F, 100.0F, 100.0F});
    shaped.clip = NodeClip{.path = make_path({PathVerb::move, PathVerb::line, PathVerb::line,
                                              PathVerb::close},
                                             {0.0F, 0.0F, 100.0F, 0.0F, 0.0F, 100.0F})};
    path_clipped.root.children.push_back(shaped);
    CHECK(pick_at(path_clipped, 20.0F, 20.0F) == "shaped");
    CHECK(!pick_at(path_clipped, 80.0F, 80.0F));

    auto transformed = make_scene();
    auto moved = rect("moved", {0.0F, 0.0F, 100.0F, 100.0F});
    moved.transform = translation(200.0F, 0.0F);
    moved.clip = NodeClip{.rect = {0.0F, 0.0F, 50.0F, 50.0F}};
    transformed.root.children.push_back(moved);
    CHECK(pick_at(transformed, 220.0F, 20.0F) == "moved");
    CHECK(!pick_at(transformed, 20.0F, 20.0F));
    CHECK(!pick_at(transformed, 280.0F, 80.0F));
}

void circle_path() {
    auto scene = make_scene();
    scene.root.children.push_back(path_node("circle", {20.0F, 20.0F, 100.0F, 100.0F},
                                            circle(50.0F, 50.0F, 50.0F), kRed));
    CHECK(pick_at(scene, 70.0F, 70.0F) == "circle");
    CHECK(pick_at(scene, 70.0F, 22.0F) == "circle");
    CHECK(!pick_at(scene, 25.0F, 25.0F));
    CHECK(!pick_at(scene, 115.0F, 25.0F));
    CHECK(!pick_at(scene, 25.0F, 115.0F));
    CHECK(!pick_at(scene, 115.0F, 115.0F));
    CHECK(!pick_at(scene, 70.0F, 121.5F));
}

void even_odd_holes() {
    auto holes = std::make_shared<PathGeometry>();
    holes->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line, PathVerb::close,
                    PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line, PathVerb::close};
    holes->values = {0, 0, 100, 0, 100, 100, 0, 100, 25, 25, 75, 25, 75, 75, 25, 75};
    holes->fill_rule = FillRule::evenodd;
    auto scene = make_scene();
    scene.root.children.push_back(path_node("ring", {0.0F, 0.0F, 100.0F, 100.0F}, holes, kRed));
    CHECK(pick_at(scene, 10.0F, 10.0F) == "ring");
    CHECK(!pick_at(scene, 50.0F, 50.0F));
    CHECK(!pick_at(scene, 40.0F, 60.0F));

    auto solid = std::make_shared<PathGeometry>(*holes);
    solid->fill_rule = FillRule::nonzero;
    auto filled = make_scene();
    filled.root.children.push_back(path_node("solid", {0.0F, 0.0F, 100.0F, 100.0F}, solid, kRed));
    CHECK(pick_at(filled, 50.0F, 50.0F) == "solid");
}

void stroke_only_line() {
    auto line = make_path({PathVerb::move, PathVerb::line}, {0.0F, 0.0F, 100.0F, 0.0F});
    line->stroke.width = 10.0F;
    line->stroke.color = Color::hex(0x000000);
    auto scene = make_scene();
    scene.root.children.push_back(path_node("line", {20.0F, 50.0F, 100.0F, 0.0F}, line, kClear));
    CHECK(pick_at(scene, 70.0F, 50.0F) == "line");
    CHECK(pick_at(scene, 70.0F, 54.0F) == "line");
    CHECK(pick_at(scene, 70.0F, 46.0F) == "line");
    CHECK(!pick_at(scene, 70.0F, 56.0F));
    CHECK(!pick_at(scene, 70.0F, 44.0F));
    CHECK(!pick_at(scene, 122.0F, 50.0F));
    CHECK(!pick_at(scene, 18.0F, 50.0F));
    CHECK(pick_at(scene, 70.0F, 55.3F, 0.5F) == "line");
    CHECK(!pick_at(scene, 70.0F, 56.0F, 0.5F));

    auto square_cap = std::make_shared<PathGeometry>(*line);
    square_cap->stroke.cap = StrokeCap::square;
    auto capped = make_scene();
    capped.root.children.push_back(path_node("line", {20.0F, 50.0F, 100.0F, 0.0F}, square_cap, kClear));
    CHECK(pick_at(capped, 123.0F, 53.0F) == "line");
    CHECK(!pick_at(capped, 126.0F, 50.0F));

    auto round_cap = std::make_shared<PathGeometry>(*line);
    round_cap->stroke.cap = StrokeCap::round;
    auto rounded = make_scene();
    rounded.root.children.push_back(path_node("line", {20.0F, 50.0F, 100.0F, 0.0F}, round_cap, kClear));
    CHECK(pick_at(rounded, 123.0F, 50.0F) == "line");
    CHECK(!pick_at(rounded, 123.0F, 54.5F));

    auto diagonal = make_path({PathVerb::move, PathVerb::line}, {0.0F, 0.0F, 100.0F, 100.0F});
    diagonal->stroke.width = 4.0F;
    diagonal->stroke.color = Color::hex(0x000000);
    auto slanted = make_scene();
    slanted.root.children.push_back(path_node("d", {0.0F, 0.0F, 100.0F, 100.0F}, diagonal, kClear));
    CHECK(pick_at(slanted, 50.0F, 50.0F) == "d");
    CHECK(!pick_at(slanted, 90.0F, 10.0F));
    CHECK(!pick_at(slanted, 10.0F, 90.0F));

    auto polyline = make_path({PathVerb::move, PathVerb::line, PathVerb::line},
                              {0.0F, 0.0F, 100.0F, 0.0F, 100.0F, 100.0F});
    polyline->stroke.width = 10.0F;
    polyline->stroke.color = Color::hex(0x000000);
    polyline->stroke.join = StrokeJoin::round;
    auto joined = make_scene();
    joined.root.children.push_back(path_node("p", {0.0F, 0.0F, 100.0F, 100.0F}, polyline, kClear));
    CHECK(pick_at(joined, 100.0F, 60.0F) == "p");
    CHECK(pick_at(joined, 103.0F, -3.0F) == "p");
    CHECK(!pick_at(joined, 50.0F, 50.0F));
    CHECK(!pick_at(joined, 104.0F, -4.0F));
}

void vector_node() {
    auto asset = std::make_shared<VectorAsset>();
    asset->view_box = {0.0F, 0.0F, 10.0F, 10.0F};
    VectorOp disc;
    disc.kind = VectorOp::Kind::path;
    disc.path = circle(5.0F, 5.0F, 5.0F);
    disc.fill.color = kRed;
    asset->ops.push_back(disc);
    SceneNode node;
    node.id = "vec";
    node.kind = SceneNode::Kind::vector;
    node.bounds = {100.0F, 100.0F, 100.0F, 100.0F};
    node.vector = asset;
    auto scene = make_scene();
    scene.root.children.push_back(node);
    CHECK(pick_at(scene, 150.0F, 150.0F) == "vec");
    CHECK(!pick_at(scene, 105.0F, 105.0F));
    CHECK(!pick_at(scene, 195.0F, 105.0F));

    auto grouped = std::make_shared<VectorAsset>();
    grouped->view_box = {0.0F, 0.0F, 10.0F, 10.0F};
    VectorOp open;
    open.kind = VectorOp::Kind::push_group;
    open.transform = translation(2.0F, 0.0F);
    open.clip = square(0.0F, 0.0F, 5.0F, 10.0F);
    grouped->ops.push_back(open);
    VectorOp fill;
    fill.kind = VectorOp::Kind::path;
    fill.path = square(0.0F, 0.0F, 10.0F, 10.0F);
    fill.fill.color = kRed;
    grouped->ops.push_back(fill);
    VectorOp close;
    close.kind = VectorOp::Kind::pop_group;
    grouped->ops.push_back(close);
    node.vector = grouped;
    auto clipped = make_scene();
    clipped.root.children.push_back(node);
    CHECK(pick_at(clipped, 130.0F, 150.0F) == "vec");
    CHECK(!pick_at(clipped, 180.0F, 150.0F));
    CHECK(!pick_at(clipped, 110.0F, 150.0F));

    auto stroked = std::make_shared<VectorAsset>();
    stroked->view_box = {0.0F, 0.0F, 10.0F, 10.0F};
    VectorOp line;
    line.kind = VectorOp::Kind::path;
    auto line_path = make_path({PathVerb::move, PathVerb::line}, {0.0F, 5.0F, 10.0F, 5.0F});
    line_path->stroke.width = 1.0F;
    line.path = line_path;
    line.stroke.color = Color::hex(0x000000);
    stroked->ops.push_back(line);
    node.vector = stroked;
    auto lined = make_scene();
    lined.root.children.push_back(node);
    CHECK(pick_at(lined, 150.0F, 150.0F) == "vec");
    CHECK(!pick_at(lined, 150.0F, 130.0F));
}

void zero_scale_and_hidden() {
    auto scene = make_scene();
    auto node = rect("flat", {100.0F, 100.0F, 100.0F, 100.0F});
    node.transform = Affine2D{.a = 0.0F, .d = 0.0F};
    node.children.push_back(rect("kid", {0.0F, 0.0F, 100.0F, 100.0F}));
    scene.root.children.push_back(node);
    CHECK(!pick_at(scene, 100.0F, 100.0F));
    CHECK(!pick_at(scene, 150.0F, 150.0F));
    const std::unordered_set<NodeId> ids{"flat", "kid"};
    CHECK(!pick_interactive(scene, 150.0F, 150.0F, ids));

    auto faded = make_scene();
    auto hidden = rect("hidden", {0.0F, 0.0F, 100.0F, 100.0F});
    hidden.opacity = 0.0F;
    faded.root.children.push_back(hidden);
    CHECK(!pick_at(faded, 50.0F, 50.0F));

    auto sheer = make_scene();
    sheer.root.children.push_back(rect("clear", {0.0F, 0.0F, 100.0F, 100.0F}, kClear));
    CHECK(!pick_at(sheer, 50.0F, 50.0F));
    const std::unordered_set<NodeId> clear_ids{"clear"};
    CHECK(pick_interactive(sheer, 50.0F, 50.0F, clear_ids) == "clear");
}

void z_order() {
    auto scene = make_scene();
    scene.root.children.push_back(rect("back", {0.0F, 0.0F, 100.0F, 100.0F}));
    scene.root.children.push_back(rect("front", {50.0F, 50.0F, 100.0F, 100.0F}));
    CHECK(pick_at(scene, 75.0F, 75.0F) == "front");
    CHECK(pick_at(scene, 25.0F, 25.0F) == "back");
    CHECK(pick_at(scene, 125.0F, 125.0F) == "front");

    const std::unordered_set<NodeId> back_only{"back"};
    CHECK(!pick_interactive(scene, 75.0F, 75.0F, back_only));
    CHECK(pick_interactive(scene, 25.0F, 25.0F, back_only) == "back");

    auto nested = make_scene();
    SceneNode button = rect("button", {10.0F, 10.0F, 100.0F, 40.0F});
    SceneNode label;
    label.id = "label";
    label.kind = SceneNode::Kind::text;
    label.bounds = {5.0F, 5.0F, 50.0F, 20.0F};
    label.text = "Go";
    button.children.push_back(label);
    nested.root.children.push_back(button);
    CHECK(pick_at(nested, 20.0F, 20.0F) == "label");
    CHECK(pick_at(nested, 100.0F, 45.0F) == "button");
    const std::unordered_set<NodeId> ids{"button"};
    CHECK(pick_interactive(nested, 20.0F, 20.0F, ids) == "button");
    CHECK(pick_interactive(nested, 100.0F, 45.0F, ids) == "button");
    CHECK(!pick_interactive(nested, 200.0F, 200.0F, ids));
}

void masks() {
    auto scene = make_scene();
    SceneNode parent;
    parent.id = "parent";
    parent.bounds = {0.0F, 0.0F, 200.0F, 200.0F};
    parent.children.push_back(rect("content", {0.0F, 0.0F, 100.0F, 100.0F}));
    auto mask = rect("mask", {0.0F, 0.0F, 50.0F, 50.0F});
    mask.mask = MaskMode::alpha;
    parent.children.push_back(mask);
    scene.root.children.push_back(parent);
    CHECK(pick_at(scene, 25.0F, 25.0F) == "content");
    CHECK(!pick_at(scene, 75.0F, 75.0F));
    CHECK(!pick_at(scene, 25.0F, 75.0F));
}

void filled_group_is_not_paint() {
    auto scene = make_scene();
    SceneNode group;
    group.id = "group";
    group.bounds = {0.0F, 0.0F, 100.0F, 100.0F};
    group.fill = kRed;
    scene.root.children.push_back(group);
    CHECK(!pick_at(scene, 50.0F, 50.0F));

    auto masked = make_scene();
    SceneNode parent;
    parent.id = "parent";
    parent.bounds = {0.0F, 0.0F, 200.0F, 200.0F};
    parent.children.push_back(rect("content", {0.0F, 0.0F, 100.0F, 100.0F}));
    SceneNode mask = group;
    mask.id = "mask";
    mask.mask = MaskMode::alpha;
    parent.children.push_back(mask);
    masked.root.children.push_back(parent);
    CHECK(!pick_at(masked, 50.0F, 50.0F));
}

void nonuniform_scale_tolerance() {
    auto line = make_path({PathVerb::move, PathVerb::line}, {0.0F, 0.0F, 1.0F, 0.0F});
    line->stroke.width = 1.0F;
    line->stroke.color = Color::hex(0x000000);
    auto scene = make_scene();
    auto node = path_node("wide", {0.0F, 50.0F, 1.0F, 0.0F}, line, kClear);
    node.transform = Affine2D{.a = 100.0F};
    scene.root.children.push_back(node);
    CHECK(pick_at(scene, 50.0F, 50.4F) == "wide");
    CHECK(!pick_at(scene, 50.0F, 51.25F));
    CHECK(pick_at(scene, 50.0F, 51.25F, 1.0F) == "wide");
    CHECK(!pick_at(scene, 50.0F, 52.0F, 1.0F));
}

void large_arcs() {
    auto scene = make_scene();
    scene.root.children.push_back(path_node("big", {0.0F, 0.0F, 2000.0F, 2000.0F},
                                            circle(1000.0F, 1000.0F, 1000.0F), kRed));
    auto clipped = make_scene();
    auto fill = rect("clipped", {0.0F, 0.0F, 2000.0F, 2000.0F});
    fill.clip = NodeClip{.path = circle(1000.0F, 1000.0F, 1000.0F)};
    clipped.root.children.push_back(fill);
    for (int step = 0; step < 240; ++step) {
        const float angle = static_cast<float>(step) * 0.0261F;
        const float dx = std::cos(angle);
        const float dy = std::sin(angle);
        CHECK(pick_at(scene, 1000.0F + 998.0F * dx, 1000.0F + 998.0F * dy) == "big");
        CHECK(!pick_at(scene, 1000.0F + 1002.0F * dx, 1000.0F + 1002.0F * dy));
        CHECK(pick_at(clipped, 1000.0F + 998.0F * dx, 1000.0F + 998.0F * dy) == "clipped");
        CHECK(!pick_at(clipped, 1000.0F + 1002.0F * dx, 1000.0F + 1002.0F * dy));
    }
}

void tracks() {
    auto scene = make_scene();
    auto node = rect("moving", {0.0F, 0.0F, 50.0F, 50.0F});
    AnimationTrack track;
    track.property = AnimatedProperty::translate_x;
    track.keyframes = {{.time = 0.0, .value = 0.0F}, {.time = 1.0, .value = 100.0F}};
    node.tracks.push_back(track);
    scene.root.children.push_back(node);
    scene.clock = 0.0;
    CHECK(pick_at(scene, 25.0F, 25.0F) == "moving");
    CHECK(!pick_at(scene, 125.0F, 25.0F));
    scene.clock = 1.0;
    CHECK(!pick_at(scene, 25.0F, 25.0F));
    CHECK(pick_at(scene, 125.0F, 25.0F) == "moving");
}

void meshes() {
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {{.x = 0.0F, .y = 0.0F}, {.x = 100.0F, .y = 0.0F}, {.x = 0.0F, .y = 100.0F}};
    mesh->indices = {0, 1, 2};
    SceneNode node;
    node.id = "mesh";
    node.kind = SceneNode::Kind::mesh;
    node.bounds = {10.0F, 10.0F, 100.0F, 100.0F};
    node.mesh = mesh;
    auto scene = make_scene();
    scene.root.children.push_back(node);
    CHECK(pick_at(scene, 30.0F, 30.0F) == "mesh");
    CHECK(!pick_at(scene, 100.0F, 100.0F));
}

void rounded_boxes() {
    auto scene = make_scene();
    auto node = rect("pill", {0.0F, 0.0F, 100.0F, 100.0F});
    node.corner_radius = 50.0F;
    scene.root.children.push_back(node);
    CHECK(pick_at(scene, 50.0F, 50.0F) == "pill");
    CHECK(!pick_at(scene, 5.0F, 5.0F));
}

void invalid_input() {
    auto scene = make_scene();
    scene.root.children.push_back(rect("r", {0.0F, 0.0F, 100.0F, 100.0F}));
    CHECK(!pick_at(scene, std::nanf(""), 10.0F));
    auto bad = make_scene();
    auto node = rect("bad", {0.0F, 0.0F, 100.0F, 100.0F});
    node.transform = Affine2D{.a = std::nanf("")};
    bad.root.children.push_back(node);
    CHECK(!pick_at(bad, 10.0F, 10.0F));
}

}  // namespace

int main() {
    rotated_rectangle();
    nested_transform();
    clipped_child();
    circle_path();
    even_odd_holes();
    stroke_only_line();
    vector_node();
    zero_scale_and_hidden();
    z_order();
    masks();
    filled_group_is_not_paint();
    nonuniform_scale_tolerance();
    large_arcs();
    tracks();
    meshes();
    rounded_boxes();
    invalid_input();
    if (failures != 0) {
        std::cerr << failures << " checks failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "hit_test_test passed\n";
    return EXIT_SUCCESS;
}
