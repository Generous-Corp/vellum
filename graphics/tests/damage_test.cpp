#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <optional>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using vellum::graphics::BoxShadow;
using vellum::graphics::Color;
using vellum::graphics::DamagePolicy;
using vellum::graphics::DamagePropagation;
using vellum::graphics::DamageRegion;
using vellum::graphics::DamageTracker;
using vellum::graphics::InvalidationReason;
using vellum::graphics::PixelRect;
using vellum::graphics::Rect;
using vellum::graphics::Scene;
using vellum::graphics::SceneNode;
using vellum::graphics::TextRun;
using vellum::graphics::TriangleMesh;
using vellum::graphics::PathGeometry;
using vellum::graphics::PathVerb;
using vellum::graphics::StrokeCap;
using vellum::graphics::StrokeJoin;

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAILED: %s\n", what);
        ++failures;
    }
}

DamagePolicy exact_policy(std::size_t max_rects = 16) {
    return {.max_rects = max_rects, .full_repaint_fraction = 2.0, .antialias_margin = 0};
}

bool disjoint(const DamageRegion& region) {
    const auto& rects = region.rects();
    for (std::size_t a = 0; a < rects.size(); ++a) {
        for (std::size_t b = a + 1; b < rects.size(); ++b) {
            if (vellum::graphics::overlaps(rects[a], rects[b])) return false;
        }
    }
    return true;
}

bool covers(const DamageRegion& region, const PixelRect& rect) {
    DamageRegion probe = region;
    probe.intersect(rect);
    return probe.area() == rect.area();
}

SceneNode rectangle(std::string id, Rect bounds, Color fill = Color::hex(0xFF0000)) {
    SceneNode node;
    node.id = std::move(id);
    node.kind = SceneNode::Kind::rectangle;
    node.bounds = bounds;
    node.fill = fill;
    return node;
}

SceneNode* child(Scene& scene, const std::string& id) {
    return const_cast<SceneNode*>(vellum::graphics::find_node(scene, id));
}

Scene base_scene() {
    Scene scene;
    scene.width = 200.0F;
    scene.height = 100.0F;
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, 200.0F, 100.0F};
    scene.root.children.push_back(rectangle("a", {10.0F, 10.0F, 20.0F, 20.0F}));
    scene.root.children.push_back(rectangle("b", {100.0F, 10.0F, 20.0F, 20.0F}));
    return scene;
}

DamageRegion diff(const Scene& before, const Scene& after, float scale = 1.0F,
                  DamagePolicy policy = exact_policy(),
                  const DamagePropagation* propagation = nullptr) {
    return vellum::graphics::compute_scene_damage(before, after, scale, policy, propagation);
}

void geometry() {
    using vellum::graphics::bounding_union;
    using vellum::graphics::intersection;
    using vellum::graphics::overlaps;
    check(intersection({0, 0, 10, 10}, {5, 5, 15, 15}) == PixelRect{5, 5, 10, 10}, "rect intersection");
    check(intersection({0, 0, 10, 10}, {10, 0, 20, 10}).empty(), "touching rects do not intersect");
    check(!overlaps({0, 0, 10, 10}, {10, 0, 20, 10}), "touching rects do not overlap");
    check(bounding_union({0, 0, 10, 10}, {20, 5, 30, 8}) == PixelRect{0, 0, 30, 10}, "bounding union");
    check(bounding_union({}, {1, 2, 3, 4}) == PixelRect{1, 2, 3, 4}, "bounding union ignores empty");

    const auto pixels = vellum::graphics::to_pixel_rect({1.25F, 2.5F, 3.0F, 1.0F}, 2.0F);
    check(pixels && *pixels == PixelRect{2, 5, 9, 7}, "logical to device pixels rounds outward");
    check(!vellum::graphics::to_pixel_rect({0.0F, 0.0F, std::numeric_limits<float>::infinity(), 1.0F}, 1.0F),
          "non-finite rect has no pixel rect");
    Scene scene;
    scene.width = 100.5F;
    scene.height = 50.0F;
    check(vellum::graphics::frame_rect(scene, 2.0F) == PixelRect{0, 0, 201, 100}, "frame rect");
}

void union_and_intersection() {
    DamageRegion region({0, 0, 100, 100}, exact_policy());
    region.add({0, 0, 10, 10});
    region.add({5, 5, 15, 15});
    check(region.area() == 175, "overlapping union counts shared area once");
    check(disjoint(region), "union keeps rects disjoint");
    region.add({0, 0, 15, 15});
    check(region.area() == 225, "union fills the gaps of a covering rect");
    check(disjoint(region), "covering union stays disjoint");
    region.add({2, 2, 4, 4});
    check(region.area() == 225, "contained rect adds nothing");
    region.add({-50, -50, 5, 5});
    check(region.area() == 225, "rects are clipped to the frame");
    region.add({90, 90, 150, 150});
    check(region.area() == 325 && region.bounds() == PixelRect{0, 0, 100, 100},
          "out-of-frame part is dropped");

    region.intersect(PixelRect{0, 0, 8, 8});
    check(region.area() == 64 && region.rects().size() >= 1, "intersection with a rect");

    DamageRegion left({0, 0, 100, 100}, exact_policy());
    left.add({0, 0, 10, 10});
    left.add({20, 0, 30, 10});
    DamageRegion right({0, 0, 100, 100}, exact_policy());
    right.add({5, 5, 25, 15});
    DamageRegion both = left;
    both.intersect(right);
    check(both.area() == 50 && disjoint(both), "intersection of two regions");
    DamageRegion merged = left;
    merged.add(right);
    check(merged.area() == 200 + 200 - 50 && disjoint(merged), "union of two regions");
}

void coalescing_at_budget() {
    DamageRegion region({0, 0, 100, 100}, exact_policy(4));
    const std::vector<PixelRect> inputs{
        {0, 0, 2, 2}, {10, 0, 12, 2}, {20, 0, 22, 2}, {30, 0, 32, 2}, {90, 90, 92, 92}};
    for (std::size_t i = 0; i < 4; ++i) region.add(inputs[i]);
    check(region.rects().size() == 4 && region.stats().coalescing_passes == 0,
          "no coalescing at the budget");
    region.add(inputs[4]);
    const auto stats = region.stats();
    check(region.rects().size() == 4, "coalescing keeps the budget");
    check(stats.rects_before_coalescing == 5 && stats.rects_after_coalescing == 4,
          "telemetry reports rect counts around coalescing");
    check(stats.coalescing_passes == 1 && stats.input_rects == 5, "coalescing telemetry");
    check(disjoint(region), "coalesced rects stay disjoint");
    for (const auto& input : inputs) check(covers(region, input), "coalescing keeps every input covered");
    check(!region.intersects({50, 50, 60, 60}), "coalescing merged the cheapest pair only");
}

void threshold_collapse() {
    const DamagePolicy policy{.max_rects = 16, .full_repaint_fraction = 0.5, .antialias_margin = 0};
    DamageRegion region({0, 0, 100, 100}, policy);
    region.add({0, 0, 50, 40});
    check(!region.is_full_frame() && !region.stats().collapsed_to_full_frame, "below the threshold");
    check(region.stats().frame_fraction == 0.2, "frame fraction telemetry");
    region.add({50, 40, 100, 100});
    const auto stats = region.stats();
    check(region.is_full_frame() && region.rects().size() == 1, "threshold collapses to the frame");
    check(stats.collapsed_to_full_frame && stats.area_pixels == 10000 && stats.frame_fraction == 1.0,
          "collapse telemetry");
    region.add({1, 1, 2, 2});
    check(region.rects().size() == 1, "full frame absorbs later damage");

    DamageRegion lenient({0, 0, 100, 100},
                         {.max_rects = 16, .full_repaint_fraction = 0.9, .antialias_margin = 0});
    lenient.add({0, 0, 50, 40});
    lenient.add({50, 40, 100, 100});
    check(!lenient.is_full_frame() && lenient.area() == 5000, "threshold is configurable");

    region.clear();
    check(region.empty() && !region.stats().collapsed_to_full_frame, "clear resets telemetry");
}

void many_small_rects() {
    DamageRegion region({0, 0, 1000, 1000}, exact_policy(8));
    std::uint32_t seed = 12345U;
    std::vector<PixelRect> inputs;
    for (int i = 0; i < 10000; ++i) {
        seed = seed * 1664525U + 1013904223U;
        const auto x = static_cast<std::int32_t>((seed >> 8U) % 999U);
        seed = seed * 1664525U + 1013904223U;
        const auto y = static_cast<std::int32_t>((seed >> 8U) % 999U);
        const PixelRect rect{x, y, x + 1, y + 1};
        inputs.push_back(rect);
        region.add(rect);
        if (region.rects().size() > 8) {
            check(false, "pathological input stays within the budget");
            break;
        }
    }
    check(region.stats().input_rects == 10000, "every small rect is counted");
    check(disjoint(region), "pathological input stays disjoint");
    bool all_covered = true;
    for (const auto& input : inputs) all_covered = all_covered && region.intersects(input);
    check(all_covered, "pathological input stays covered");
}

void scene_diff() {
    const Scene before = base_scene();
    check(diff(before, before).empty(), "identical scenes have no damage");

    Scene added = before;
    added.root.children.push_back(rectangle("c", {50.0F, 50.0F, 10.0F, 10.0F}));
    auto damage = diff(before, added);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{50, 50, 60, 60}, "added node");

    Scene removed = before;
    removed.root.children.pop_back();
    damage = diff(before, removed);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{100, 10, 120, 30}, "removed node");

    Scene moved = before;
    child(moved, "a")->bounds.x = 40.0F;
    damage = diff(before, moved);
    check(damage.area() == 800 && covers(damage, {10, 10, 30, 30}) && covers(damage, {40, 10, 60, 30}),
          "moved node damages old and new bounds");

    Scene resized = before;
    child(resized, "a")->bounds.width = 30.0F;
    damage = diff(before, resized);
    check(damage.area() == 600 && damage.bounds() == PixelRect{10, 10, 40, 30}, "resized node");

    Scene recolored = before;
    child(recolored, "a")->fill = Color::hex(0x00FF00);
    damage = diff(before, recolored);
    check(damage.area() == 400 && damage.bounds() == PixelRect{10, 10, 30, 30}, "recolored node");
    check(!damage.intersects({100, 10, 120, 30}), "recolor leaves other nodes alone");

    Scene shadowed = before;
    child(shadowed, "a")->box_shadows.push_back(
        BoxShadow{.offset_x = 4.0F, .offset_y = 6.0F, .blur_radius = 2.0F, .spread_radius = 1.0F,
                  .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.5F)});
    damage = diff(before, shadowed);
    check(damage.bounds() == PixelRect{10, 10, 38, 40}, "box shadow expands damage by offset, spread and blur");

    Scene scaled = recolored;
    damage = diff(before, scaled, 2.0F,
                  {.max_rects = 16, .full_repaint_fraction = 2.0, .antialias_margin = 1});
    check(damage.bounds() == PixelRect{19, 19, 61, 61}, "damage scales to device pixels with a margin");

    Scene reordered = before;
    child(reordered, "b")->bounds.x = 20.0F;
    Scene swapped = reordered;
    std::swap(swapped.root.children[0], swapped.root.children[1]);
    damage = diff(reordered, swapped);
    check(damage.bounds() == PixelRect{10, 10, 40, 30} && damage.area() == 600, "z-order change damages both nodes");

    Scene background = before;
    background.background = Color::hex(0xFFFFFF);
    check(diff(before, background).is_full_frame(), "background change repaints the frame");
    Scene bigger = before;
    bigger.width = 300.0F;
    damage = diff(before, bigger);
    check(damage.is_full_frame() && damage.frame() == PixelRect{0, 0, 300, 100}, "resized scene repaints the frame");
}

void group_diff() {
    Scene before;
    before.width = 200.0F;
    before.height = 100.0F;
    SceneNode group;
    group.id = "g";
    group.bounds = {0.0F, 0.0F, 200.0F, 100.0F};
    group.children.push_back(rectangle("a", {10.0F, 10.0F, 20.0F, 20.0F}));
    group.children.push_back(rectangle("b", {100.0F, 10.0F, 20.0F, 20.0F}));
    before.root.children.push_back(group);

    Scene shifted = before;
    child(shifted, "g")->bounds.x = 5.0F;
    const auto damage = diff(before, shifted);
    check(covers(damage, {10, 10, 35, 30}) && covers(damage, {100, 10, 125, 30}),
          "moving a group damages its descendants at their absolute bounds");
    check(damage.area() == 2 * 500, "group move damage covers only the descendants");

    Scene reparented = before;
    auto moved = child(reparented, "g")->children.back();
    child(reparented, "g")->children.pop_back();
    reparented.root.children.push_back(moved);
    const auto reparent_damage = diff(before, reparented);
    check(reparent_damage.rects().size() == 1 &&
              reparent_damage.rects()[0] == PixelRect{100, 10, 120, 30},
          "reparented node damages exactly its area");
}

void text_diff() {
    Scene before;
    before.width = 200.0F;
    before.height = 100.0F;
    SceneNode label;
    label.id = "t";
    label.kind = SceneNode::Kind::text;
    label.bounds = {10.0F, 50.0F, 100.0F, 20.0F};
    label.text = "Hello";
    label.font_size = 10.0F;
    before.root.children.push_back(label);

    check(diff(before, before).empty(), "unchanged text has no damage");
    Scene changed = before;
    child(changed, "t")->text = "World";
    auto damage = diff(before, changed);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{0, 40, 200, 80},
          "text change damages to the frame's right edge with overhang");

    Scene narrow = before;
    child(narrow, "t")->bounds.width = 20.0F;
    child(narrow, "t")->text = "A label far wider than its box";
    Scene narrow_changed = narrow;
    child(narrow_changed, "t")->text = "Another label far wider than its box";
    damage = diff(narrow, narrow_changed);
    check(covers(damage, {30, 50, 200, 70}), "text wider than its box damages the overflow");

    Scene tall = before;
    tall.height = 200.0F;
    child(tall, "t")->text = "one\ntwo\nthree";
    Scene tall_changed = tall;
    child(tall_changed, "t")->text = "one\ntwo\nfour";
    damage = diff(tall, tall_changed);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{0, 40, 200, 102},
          "line breaks extend text damage below its box");

    Scene runs = before;
    child(runs, "t")->text_runs.push_back(TextRun{.text = "Hi", .style = {}});
    Scene restyled = runs;
    child(restyled, "t")->text_runs[0].style.underline = true;
    check(!diff(runs, restyled).empty(), "text run style change is damage");

    Scene weighted = before;
    child(weighted, "t")->font_weight = 700;
    check(!diff(before, weighted).empty(), "text style change is damage");
}

void mesh_diff() {
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {{.x = 0.0F, .y = 0.0F}, {.x = 10.0F, .y = 0.0F}, {.x = 0.0F, .y = 10.0F}};
    mesh->indices = {0, 1, 2};
    Scene before;
    before.width = 200.0F;
    before.height = 100.0F;
    SceneNode node;
    node.id = "m";
    node.kind = SceneNode::Kind::mesh;
    node.bounds = {20.0F, 60.0F, 0.0F, 0.0F};
    node.mesh = mesh;
    before.root.children.push_back(node);

    Scene same = before;
    check(diff(before, same).empty(), "same mesh pointer has no damage");

    auto clipped = std::make_shared<TriangleMesh>(*mesh);
    clipped->clip_enabled = true;
    clipped->clip = {0.0F, 0.0F, 5.0F, 5.0F};
    Scene replaced = before;
    child(replaced, "m")->mesh = clipped;
    auto damage = diff(before, replaced);
    check(damage.bounds() == PixelRect{19, 59, 31, 71}, "mesh pointer change damages the old geometry");

    const auto clip_only = diff(Scene{replaced}, [&] {
        Scene next = replaced;
        auto narrower = std::make_shared<TriangleMesh>(*clipped);
        narrower->clip = {0.0F, 0.0F, 3.0F, 3.0F};
        child(next, "m")->mesh = narrower;
        return next;
    }());
    check(clip_only.bounds() == PixelRect{20, 60, 25, 65}, "mesh clip change is bounded by the clips");
}

PathGeometry& edit_path(SceneNode& node) {
    auto copy = std::make_shared<PathGeometry>(node.path ? *node.path : PathGeometry{});
    node.path = copy;
    return *copy;
}

void path_diff() {
    Scene before;
    before.width = 200.0F;
    before.height = 100.0F;
    SceneNode node;
    node.id = "p";
    node.kind = SceneNode::Kind::path;
    node.bounds = {20.0F, 20.0F, 0.0F, 0.0F};
    node.fill = Color::hex(0xFF0000);
    edit_path(node).verbs = {PathVerb::move, PathVerb::line, PathVerb::quadratic};
    edit_path(node).values = {0.0F, 0.0F, 40.0F, 0.0F, 60.0F, 30.0F, 40.0F, 20.0F};
    edit_path(node).stroke = {
        .color = Color::hex(0x0000FF), .width = 4.0F, .join = StrokeJoin::bevel};
    before.root.children.push_back(node);

    const DamagePropagation none;
    const Rect frame{0.0F, 0.0F, 200.0F, 100.0F};
    const auto paint_bounds = [&](const SceneNode& candidate) {
        return vellum::graphics::node_paint_bounds(candidate, {20.0F, 20.0F, 0.0F, 0.0F}, frame,
                                                   none);
    };
    const auto bounds = paint_bounds(node);
    check(bounds && bounds->x == 18.0F && bounds->y == 18.0F && bounds->width == 64.0F &&
              bounds->height == 34.0F,
          "path paint bounds are the control box outset by half the stroke width");

    SceneNode mitered = node;
    edit_path(mitered).stroke.join = StrokeJoin::miter;
    edit_path(mitered).stroke.miter_limit = 3.0F;
    const auto miter = paint_bounds(mitered);
    check(miter && miter->x == 14.0F && miter->width == 72.0F,
          "a miter join outsets by the miter limit");

    SceneNode capped = node;
    edit_path(capped).stroke.cap = StrokeCap::round;
    const auto round = paint_bounds(capped);
    check(round && std::abs(round->x - (20.0F - 2.0F * std::sqrt(2.0F))) < 0.0001F,
          "a round cap outsets by sqrt(2) times half the width");

    SceneNode unstroked = node;
    edit_path(unstroked).stroke.width = 0.0F;
    const auto fill_only = paint_bounds(unstroked);
    check(fill_only && fill_only->x == 20.0F && fill_only->width == 60.0F &&
              fill_only->height == 30.0F,
          "an unstroked path paints its control box");

    SceneNode empty = node;
    empty.path = std::make_shared<const PathGeometry>();
    check(!paint_bounds(empty),
          "an empty path paints nothing");

    SceneNode broken = node;
    edit_path(broken).values.pop_back();
    const auto invalid = paint_bounds(broken);
    check(invalid && invalid->width == 200.0F && invalid->height == 100.0F,
          "an invalid path reports the whole frame");

    SceneNode ghost = node;
    ghost.path = nullptr;
    const auto missing = paint_bounds(ghost);
    check(missing && missing->width == 200.0F && missing->height == 100.0F,
          "a path without geometry reports the whole frame");
    for (const float bad : {std::numeric_limits<float>::quiet_NaN(),
                            std::numeric_limits<float>::infinity()}) {
        SceneNode direct = node;
        auto geometry = std::make_shared<PathGeometry>();
        geometry->verbs = {PathVerb::move, PathVerb::line};
        geometry->values = {0.0F, 0.0F, bad, 10.0F};
        direct.path = geometry;
        const auto bounds_of_bad = paint_bounds(direct);
        check(bounds_of_bad && bounds_of_bad->width == 200.0F && bounds_of_bad->height == 100.0F,
              "a directly built non-finite path reports the whole frame");
    }

    Scene same = before;
    check(diff(before, same).empty(), "an identical path has no damage");
    Scene copied = before;
    child(copied, "p")->path = std::make_shared<const PathGeometry>(*node.path);
    check(diff(before, copied).empty(), "a distinct path with the same content has no damage");

    const PixelRect painted{18, 18, 82, 52};
    const auto changed = [&](auto mutate, const char* what) {
        Scene next = before;
        mutate(*child(next, "p"));
        const auto damage = diff(before, next);
        check(!damage.empty() && covers(damage, painted) && disjoint(damage), what);
        return damage;
    };
    changed([](SceneNode& p) { p.fill = Color::hex(0x00FF00); },
            "fill colour change damages the path");
    changed([](SceneNode& p) {
        p.fill_gradients = {{.stops = {{.position = 0.0F}, {.position = 1.0F}}}};
    }, "fill gradient change damages the path");
    changed([](SceneNode& p) { edit_path(p).fill_rule = vellum::graphics::FillRule::evenodd; },
            "fill rule change damages the path");
    changed([](SceneNode& p) { edit_path(p).stroke.color = Color::hex(0xFFFFFF); },
            "stroke colour change damages the path");
    changed([](SceneNode& p) { edit_path(p).stroke.dash_array = {4.0F, 2.0F}; },
            "dash array change damages the path");
    changed([](SceneNode& p) { edit_path(p).stroke.dash_offset = 1.0F; },
            "dash offset change damages the path");
    changed([](SceneNode& p) { edit_path(p).stroke.join = StrokeJoin::round; },
            "join change damages the path");
    changed([](SceneNode& p) { edit_path(p).stroke.cap = StrokeCap::square; },
            "cap change damages the path");
    changed([](SceneNode& p) { edit_path(p).antialias = false; },
            "anti-aliasing change damages the path");
    changed([](SceneNode& p) { edit_path(p).verbs[1] = PathVerb::move; }, "verb change damages the path");

    const auto moved = changed([](SceneNode& p) { edit_path(p).values[4] = 120.0F; },
                               "geometry change damages the old path");
    check(moved.bounds() == PixelRect{18, 18, 142, 52},
          "geometry change damages the old and new paths");

    Scene stroke_grown = before;
    edit_path(*child(stroke_grown, "p")).stroke.width = 10.0F;
    check(diff(before, stroke_grown).bounds() == PixelRect{15, 15, 85, 55},
          "a wider stroke damages its larger reach");

    Scene invisible = before;
    edit_path(*child(invisible, "p")).stroke.width = 0.0F;
    Scene invisible_again = invisible;
    edit_path(*child(invisible_again, "p")).stroke.miter_limit = 9.0F;
    check(diff(invisible, invisible_again).bounds() == PixelRect{20, 20, 80, 50},
          "an invisible stroke change damages only the fill");
}

bool contains(const std::optional<Rect>& bounds, float x, float y) {
    return bounds && x >= bounds->x && x <= bounds->x + bounds->width && y >= bounds->y &&
           y <= bounds->y + bounds->height;
}

// The geometry of a stroke's outline, worked out by hand, must lie inside the
// damage bounds for a diagonal square-capped segment and a sharp miter.
void stroke_reach() {
    const DamagePropagation none;
    const Rect frame{0.0F, 0.0F, 200.0F, 100.0F};
    const Rect origin{10.0F, 10.0F, 0.0F, 0.0F};

    SceneNode diagonal;
    diagonal.kind = SceneNode::Kind::path;
    auto segment = std::make_shared<PathGeometry>();
    segment->verbs = {PathVerb::move, PathVerb::line};
    segment->values = {0.0F, 0.0F, 30.0F, 40.0F};
    segment->stroke = {.color = Color::hex(0xFFFFFF), .width = 4.0F, .join = StrokeJoin::bevel,
                       .cap = StrokeCap::square};
    diagonal.path = segment;
    const auto capped = vellum::graphics::node_paint_bounds(diagonal, origin, frame, none);
    const float along[2]{0.6F, 0.8F};
    const float across[2]{-0.8F, 0.6F};
    bool corners_inside = true;
    for (const auto& end : {std::pair{0.0F, -1.0F}, std::pair{1.0F, 1.0F}}) {
        for (const float side : {-1.0F, 1.0F}) {
            const float x =
                10.0F + 30.0F * end.first + 2.0F * (end.second * along[0] + side * across[0]);
            const float y =
                10.0F + 40.0F * end.first + 2.0F * (end.second * along[1] + side * across[1]);
            corners_inside = corners_inside && contains(capped, x, y);
        }
    }
    check(corners_inside, "a diagonal square cap's corners lie inside the damage bounds");

    SceneNode sharp;
    sharp.kind = SceneNode::Kind::path;
    auto turn = std::make_shared<PathGeometry>();
    turn->verbs = {PathVerb::move, PathVerb::line, PathVerb::line};
    turn->values = {0.0F, 0.0F, 40.0F, 0.0F, 0.0F, 10.0F};
    turn->stroke = {.color = Color::hex(0xFFFFFF), .width = 4.0F, .miter_limit = 10.0F};
    sharp.path = turn;
    const auto mitered = vellum::graphics::node_paint_bounds(sharp, origin, frame, none);
    const double in_x = 1.0;
    const double in_y = 0.0;
    const double out_length = std::hypot(-40.0, 10.0);
    const double out_x = -40.0 / out_length;
    const double out_y = 10.0 / out_length;
    const double half_angle = 0.5 * std::acos(-(in_x * out_x + in_y * out_y));
    const double reach = 2.0 / std::sin(half_angle);
    const double bisector_length = std::hypot(in_x - out_x, in_y - out_y);
    const double tip_x = 10.0 + 40.0 + reach * (in_x - out_x) / bisector_length;
    const double tip_y = 10.0 + reach * (in_y - out_y) / bisector_length;
    check(1.0 / std::sin(half_angle) < 10.0, "the sharp turn is within its miter limit");
    check(tip_x > 10.0 + 40.0 + 2.0 * 6.0, "the miter tip reaches well past half the width");
    check(contains(mitered, static_cast<float>(tip_x), static_cast<float>(tip_y)),
          "a sharp miter's tip lies inside the damage bounds");
}

class TestPropagation final : public DamagePropagation {
public:
    float effect_radius(const SceneNode& node) const override {
        return node.id == "a" ? 5.0F : 0.0F;
    }
    std::optional<Rect> clip_bounds(const SceneNode& node, const Rect&) const override {
        if (node.id == "root") return Rect{0.0F, 0.0F, 105.0F, 100.0F};
        return std::nullopt;
    }
};

void propagation_hooks() {
    const Scene before = base_scene();
    Scene recolored = before;
    child(recolored, "a")->fill = Color::hex(0x00FF00);
    child(recolored, "b")->fill = Color::hex(0x00FF00);
    const DamagePropagation none;
    check(diff(before, recolored, 1.0F, exact_policy(), &none).area() == 800, "default propagation is a no-op");
    const TestPropagation effects;
    const auto damage = diff(before, recolored, 1.0F, exact_policy(), &effects);
    check(covers(damage, {5, 5, 35, 35}), "effect radius expands damage");
    check(covers(damage, {100, 10, 105, 30}) && !damage.intersects({105, 0, 200, 100}),
          "ancestor clip bounds descendant damage");
}

void unusual_bounds() {
    const Scene before = base_scene();
    Scene broken = before;
    child(broken, "a")->bounds.x = std::numeric_limits<float>::quiet_NaN();
    check(diff(before, broken).is_full_frame(), "non-finite bounds repaint the frame");
    check(diff(broken, broken).empty(), "unchanged non-finite bounds add no damage");

    Scene huge = before;
    child(huge, "a")->bounds = {-1.0e30F, 10.0F, 2.0e30F, 20.0F};
    const auto damage = diff(before, huge);
    check(damage.bounds() == PixelRect{0, 10, 200, 30}, "huge bounds clamp to the frame");
    check(vellum::graphics::to_pixel_rect({-1.0e30F, 0.0F, 2.0e30F, 1.0F}, 1.0F) ==
              PixelRect{-(1 << 30), 0, 1 << 30, 1},
          "pixel edges clamp to 2^30");

    Scene negative = before;
    negative.root.children.push_back(rectangle("n", {-50.0F, 50.0F, 60.0F, 10.0F}));
    const auto clipped = diff(before, negative);
    check(clipped.rects().size() == 1 && clipped.rects()[0] == PixelRect{0, 50, 10, 60},
          "negative bounds clip to the frame");

    DamageRegion capped({0, 0, 10, 10}, {.max_rects = 1000});
    check(capped.policy().max_rects == vellum::graphics::kMaxDamageRects, "rect budget is capped");
}

void duplicate_ids() {
    Scene before = base_scene();
    before.root.children.push_back(rectangle("a", {50.0F, 50.0F, 10.0F, 10.0F}));
    check(diff(before, before).empty(), "unchanged duplicate ids add no damage");
    Scene recolored = before;
    recolored.root.children.back().fill = Color::hex(0x0000FF);
    const auto damage = diff(before, recolored);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{50, 50, 60, 60},
          "a duplicate id is matched by position");

    DamageTracker tracker(exact_policy());
    tracker.commit(before, 1.0F);
    (void)tracker.take_damage();
    tracker.mark_dirty("a");
    const auto marked = tracker.take_damage();
    check(marked.area() == 500 && covers(marked, {10, 10, 30, 30}) && covers(marked, {50, 50, 60, 60}),
          "mark_dirty damages every node with the id");
}

void review_hardening() {
    const Scene before = base_scene();
    Scene bumped = before;
    bumped.scene_version = before.scene_version + 1U;
    check(diff(before, bumped).is_full_frame(), "a scene_version change repaints the frame");
    DamageTracker versions(exact_policy());
    versions.commit(before, 1.0F);
    (void)versions.take_damage();
    versions.commit(bumped, 1.0F);
    check(versions.take_damage().is_full_frame(), "the tracker repaints on a scene_version change");

    Scene quiet = before;
    child(quiet, "a")->fill.red = std::bit_cast<float>(0x7FC00000U);
    Scene other_nan = quiet;
    child(other_nan, "a")->fill.red = std::bit_cast<float>(0x7FC00001U);
    check(diff(quiet, quiet).empty(), "an identical NaN is unchanged");
    const auto nan_damage = diff(quiet, other_nan);
    check(nan_damage.rects().size() == 1 && nan_damage.rects()[0] == PixelRect{10, 10, 30, 30},
          "a NaN changing to a different NaN is damage");

    constexpr std::int32_t low = std::numeric_limits<std::int32_t>::min();
    constexpr std::int32_t high = std::numeric_limits<std::int32_t>::max();
    check(PixelRect{low, low, high, high}.area() == std::numeric_limits<std::int64_t>::max(),
          "extreme rect area saturates");
    constexpr std::int32_t limit = vellum::graphics::kMaxPixelCoordinate;
    DamageRegion extreme({low, low, high, high}, exact_policy());
    check(extreme.frame() == PixelRect{-limit, -limit, limit, limit}, "an extreme frame is clamped");
    extreme.add({low, 0, high, 1});
    check(extreme.area() == 2LL * limit, "an extreme rect is clamped on entry");
    extreme.add({low, low, high, high});
    const auto extreme_stats = extreme.stats();
    check(extreme.is_full_frame() && extreme_stats.area_pixels == extreme_stats.frame_area_pixels &&
              extreme_stats.frame_fraction == 1.0,
          "extreme rects keep area arithmetic exact");

    DamageRegion forced({0, 0, 10, 10});
    forced.add_full_frame();
    check(forced.stats().explicit_full_frame && !forced.stats().collapsed_to_full_frame,
          "an explicit full repaint is reported apart from the threshold");
    DamageRegion threshold({0, 0, 10, 10}, {.full_repaint_fraction = 0.5, .antialias_margin = 0});
    threshold.add({0, 0, 10, 6});
    check(threshold.stats().collapsed_to_full_frame && !threshold.stats().explicit_full_frame,
          "a threshold collapse is not reported as explicit");
    DamageRegion forced_whole = forced;
    forced_whole.intersect(PixelRect{0, 0, 10, 10});
    check(forced_whole.stats().explicit_full_frame, "intersecting with the whole frame keeps the flag");
    forced.intersect(PixelRect{0, 0, 5, 5});
    check(!forced.stats().explicit_full_frame && !forced.stats().collapsed_to_full_frame,
          "intersecting below the frame clears the explicit flag");
    DamageRegion window({0, 0, 10, 10}, exact_policy());
    window.add({2, 2, 4, 4});
    threshold.intersect(window);
    check(!threshold.stats().collapsed_to_full_frame && !threshold.stats().explicit_full_frame &&
              threshold.area() == 4,
          "intersecting below the frame clears the collapse flag");
    forced.add_full_frame();
    forced.clear();
    check(!forced.stats().explicit_full_frame, "clear resets the explicit flag");
}

Scene grouped_scene() {
    Scene scene;
    scene.width = 200.0F;
    scene.height = 100.0F;
    SceneNode group;
    group.id = "g";
    group.bounds = {0.0F, 0.0F, 200.0F, 100.0F};
    group.children.push_back(rectangle("a", {10.0F, 10.0F, 20.0F, 20.0F}));
    group.children.push_back(rectangle("b", {100.0F, 10.0F, 20.0F, 20.0F}));
    scene.root.children.push_back(group);
    return scene;
}

void compositing_damage() {
    using vellum::graphics::Affine2D;
    using vellum::graphics::CornerRadii;
    const Affine2D quarter_turn{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    const Affine2D doubled{.a = 2.0F, .d = 2.0F};

    const Scene before = base_scene();
    Scene rotated = before;
    child(rotated, "a")->transform = quarter_turn;
    auto damage = diff(before, rotated);
    check(covers(damage, {10, 10, 30, 30}) && covers(damage, {0, 10, 10, 30}) && damage.area() == 600,
          "a rotation damages the old box and the rotated box about the node origin");
    check(!damage.intersects({100, 10, 120, 30}), "a rotation leaves other nodes alone");

    Scene identity = before;
    child(identity, "a")->transform = Affine2D{};
    check(diff(before, identity).empty(), "an identity transform equals no transform");

    const Scene group = grouped_scene();
    Scene scaled = group;
    child(scaled, "g")->transform = doubled;
    damage = diff(group, scaled);
    check(covers(damage, {10, 10, 30, 30}) && covers(damage, {20, 20, 60, 60}) &&
              covers(damage, {100, 10, 120, 30}),
          "a group transform damages its descendants' old and transformed areas");
    check(damage.area() == 1900 + 400,
          "group transform damage is exactly the mapped areas, clipped to the frame");

    Scene faded = group;
    child(faded, "g")->opacity = 0.5F;
    damage = diff(group, faded);
    check(damage.area() == 800 && covers(damage, {10, 10, 30, 30}) && covers(damage, {100, 10, 120, 30}),
          "an opacity change damages the whole subtree");

    Scene hidden = group;
    child(hidden, "g")->opacity = 0.0F;
    check(diff(group, hidden).area() == 800, "fading to zero damages what the subtree painted");
    check(diff(hidden, group).area() == 800, "fading back in damages what the subtree paints");
    Scene hidden_moved = hidden;
    child(hidden_moved, "a")->bounds.x = 60.0F;
    check(diff(hidden, hidden_moved).empty(), "a change inside a zero-opacity subtree is not damage");

    Scene rounded = before;
    child(rounded, "a")->corner_radii = CornerRadii{8.0F, 0.0F, 4.0F, 2.0F};
    damage = diff(before, rounded);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{10, 10, 30, 30},
          "setting per-corner radii damages the node");
    Scene rerounded = rounded;
    child(rerounded, "a")->corner_radii->bottom_left = 6.0F;
    check(diff(rounded, rerounded).area() == 400, "changing one corner radius damages the node");

    Scene broken = before;
    child(broken, "a")->transform = Affine2D{.a = std::numeric_limits<float>::quiet_NaN()};
    check(diff(before, broken).is_full_frame(), "a non-finite transform repaints the frame");
    Scene broken_opacity = before;
    child(broken_opacity, "a")->opacity = std::numeric_limits<float>::infinity();
    check(diff(before, broken_opacity).is_full_frame(), "a non-finite opacity repaints the frame");

    Scene labelled = before;
    SceneNode label;
    label.id = "t";
    label.kind = SceneNode::Kind::text;
    label.bounds = {10.0F, 50.0F, 40.0F, 20.0F};
    label.text = "Hello";
    label.font_size = 10.0F;
    labelled.root.children.push_back(label);
    Scene turned = labelled;
    child(turned, "t")->transform = quarter_turn;
    check(diff(labelled, turned).is_full_frame(), "text under a non-identity transform repaints the frame");
    check(diff(turned, turned).empty(), "unchanged transformed text adds no damage");

    class Blur final : public DamagePropagation {
    public:
        float effect_radius(const SceneNode& node) const override { return node.id == "a" ? 5.0F : 0.0F; }
    };
    const Blur blur;
    Scene recoloured = scaled;
    child(recoloured, "a")->fill = Color::hex(0x00FF00);
    damage = diff(scaled, recoloured, 1.0F, exact_policy(), &blur);
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{10, 10, 70, 70},
          "an effect radius scales with the transform");
}

PixelRect lowered_pixels(const Scene& scene, const std::string& id) {
    for (const auto& command : vellum::graphics::make_paint_commands(scene)) {
        if (command.node_id != id || !vellum::graphics::draws_content(command.kind)) continue;
        const auto extent = vellum::graphics::paint_extent(command);
        const auto mapped = vellum::graphics::transformed_bounds(command.transform, extent.rect);
        const auto pixels = vellum::graphics::to_pixel_rect(mapped, 1.0F);
        return pixels ? vellum::graphics::intersection(*pixels, vellum::graphics::frame_rect(scene, 1.0F))
                      : PixelRect{};
    }
    return {};
}

vellum::graphics::Affine2D rotation(float degrees, float tx = 0.0F, float ty = 0.0F) {
    const float radians = degrees * 3.14159265358979323846F / 180.0F;
    return {.a = std::cos(radians), .b = std::sin(radians), .c = -std::sin(radians),
            .d = std::cos(radians), .tx = tx, .ty = ty};
}

bool added_matches_lowering(const Scene& base, const Scene& next, const std::string& leaf) {
    const auto damage = diff(base, next);
    const auto expected = lowered_pixels(next, leaf);
    if (expected.empty()) return damage.empty();
    return damage.rects().size() == 1 && damage.rects()[0] == expected;
}

void transform_geometry() {
    using vellum::graphics::Affine2D;
    Scene base;
    base.width = 200.0F;
    base.height = 100.0F;

    const auto with_leaf = [&](Affine2D transform, Rect bounds) {
        Scene next = base;
        SceneNode leaf = rectangle("leaf", bounds);
        leaf.transform = transform;
        next.root.children.push_back(leaf);
        return next;
    };
    check(added_matches_lowering(base, with_leaf(rotation(30.0F), {60.0F, 30.0F, 30.0F, 20.0F}), "leaf"),
          "a 30 degree rotation matches the lowered transform");
    check(added_matches_lowering(base, with_leaf({.a = -1.0F, .tx = 4.0F}, {10.0F, 30.0F, 30.0F, 20.0F}),
                                 "leaf"),
          "a horizontal mirror with translation clips at the frame edge like the lowering");
    check(added_matches_lowering(base, with_leaf({.d = -1.0F, .ty = 3.0F}, {50.0F, 8.0F, 30.0F, 20.0F}),
                                 "leaf"),
          "a vertical mirror with translation clips at the frame edge like the lowering");
    check(added_matches_lowering(
              base, with_leaf({.a = -1.0F, .d = -1.0F, .tx = 7.0F, .ty = -2.0F}, {190.0F, 90.0F, 30.0F, 20.0F}),
              "leaf"),
          "a combined mirror matches the lowering");
    check(!lowered_pixels(with_leaf({.a = -1.0F, .tx = 4.0F}, {10.0F, 30.0F, 30.0F, 20.0F}), "leaf").empty(),
          "the mirror oracle is not vacuous");

    const auto with_group = [&](Affine2D group_transform, Affine2D child_transform) {
        Scene next = base;
        SceneNode group;
        group.id = "group";
        group.bounds = {70.0F, 20.0F, 60.0F, 40.0F};
        group.transform = group_transform;
        SceneNode leaf = rectangle("leaf", {15.0F, 10.0F, 24.0F, 12.0F});
        leaf.transform = child_transform;
        group.children.push_back(leaf);
        next.root.children.push_back(group);
        return next;
    };
    check(added_matches_lowering(base, with_group(rotation(20.0F), rotation(35.0F, 2.0F, 1.0F)), "leaf"),
          "a rotated child of a rotated group composes in the lowering's order");
    check(added_matches_lowering(base, with_group({.a = -1.0F, .tx = 5.0F}, rotation(-50.0F)), "leaf"),
          "a rotated child of a mirrored group composes in the lowering's order");
    check(added_matches_lowering(base, with_group({.d = -1.0F, .ty = 2.0F}, {.a = 1.5F, .d = 0.5F}), "leaf"),
          "a scaled child of a vertically mirrored group composes in the lowering's order");
    const auto forward = lowered_pixels(with_group(rotation(20.0F), rotation(35.0F, 2.0F, 1.0F)), "leaf");
    const auto swapped = lowered_pixels(with_group(rotation(35.0F, 2.0F, 1.0F), rotation(20.0F)), "leaf");
    check(forward != swapped, "the nested-transform cases do not commute, so they test order");

    Scene wide;
    wide.width = 400.0F;
    wide.height = 300.0F;
    Scene stretched = wide;
    SceneNode shadowed = rectangle("shadowed", {150.0F, 120.0F, 20.0F, 10.0F});
    shadowed.box_shadows.push_back({.blur_radius = 20.0F, .color = Color::hex(0x000000)});
    shadowed.transform = Affine2D{.a = 2.0F, .d = 0.5F};
    stretched.root.children.push_back(shadowed);
    const auto blurred = diff(wide, stretched);
    check(blurred.rects().size() == 1 && blurred.rects()[0] == PixelRect{90, 60, 250, 185},
          "a shadow blur grows in frame space by the largest scale, not along the squashed axis");
}

void composite_path_changes() {
    Scene base = grouped_scene();
    child(base, "g")->opacity = 0.5F;
    Scene single = base;
    child(single, "g")->children.pop_back();
    auto damage = diff(base, single);
    check(covers(damage, {10, 10, 30, 30}) && covers(damage, {100, 10, 120, 30}),
          "removing a sibling under a faded group damages the survivor, whose opacity now folds");
    damage = diff(single, base);
    check(covers(damage, {10, 10, 30, 30}) && covers(damage, {100, 10, 120, 30}),
          "adding a sibling under a faded group damages the survivor, which now composites a layer");

    Scene hidden_sibling = base;
    child(hidden_sibling, "b")->opacity = 0.0F;
    check(covers(diff(base, hidden_sibling), {10, 10, 30, 30}),
          "hiding a sibling under a faded group damages the survivor");

    Scene with_text = base;
    SceneNode label;
    label.id = "label";
    label.kind = SceneNode::Kind::text;
    label.bounds = {40.0F, 50.0F, 40.0F, 20.0F};
    label.text = "Hi";
    child(with_text, "g")->children.pop_back();
    child(with_text, "g")->children.push_back(label);
    Scene emptied = with_text;
    child(emptied, "label")->text.clear();
    check(covers(diff(with_text, emptied), {10, 10, 30, 30}),
          "emptying a text sibling under a faded group damages the survivor");

    Scene opaque = grouped_scene();
    Scene opaque_single = opaque;
    child(opaque_single, "g")->children.pop_back();
    check(!diff(opaque, opaque_single).intersects({10, 10, 30, 30}),
          "removing a sibling under an opaque group leaves the survivor alone");
}

void whole_area_closure() {
    using vellum::graphics::enclose_whole_areas;
    const PixelRect target{0, 0, 200, 100};
    const PixelRect layer{20, 0, 60, 40};
    auto grown = enclose_whole_areas({{25, 5, 35, 15}}, {layer}, target);
    check(grown.size() == 1 && grown[0] == layer, "a rect inside a layer becomes the layer's bounds");
    grown = enclose_whole_areas({{10, 10, 30, 30}}, {layer}, target);
    check(grown.size() == 1 && grown[0] == PixelRect{10, 0, 60, 40},
          "a rect cutting a layer grows to hold the whole layer");
    const PixelRect chained{55, 30, 90, 70};
    grown = enclose_whole_areas({{25, 5, 35, 15}}, {layer, chained}, target);
    check(grown.size() == 1 && grown[0] == PixelRect{20, 0, 90, 70},
          "two layers chained by overlap both grow into the rect");
    grown = enclose_whole_areas({{100, 80, 110, 90}, {150, 10, 160, 20}}, {layer, chained}, target);
    check(grown.size() == 2 && grown[0] == PixelRect{100, 80, 110, 90} &&
              grown[1] == PixelRect{150, 10, 160, 20},
          "rects that miss every layer are unchanged");
    grown = enclose_whole_areas({{25, 5, 35, 15}, {58, 2, 64, 6}}, {layer}, target);
    check(grown.size() == 1 && grown[0] == PixelRect{20, 0, 64, 40},
          "a rect the grown layer reaches is merged, so the result stays disjoint");
    grown = enclose_whole_areas({{190, 90, 220, 120}}, {{180, 80, 260, 140}}, target);
    check(grown.size() == 1 && grown[0] == PixelRect{180, 80, 200, 100},
          "growth is clipped to the target");
}

void path_damage() {
    using vellum::graphics::PathGeometry;
    using vellum::graphics::PathVerb;
    auto triangle = std::make_shared<PathGeometry>();
    triangle->verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::close};
    triangle->values = {0.0F, 0.0F, 30.0F, 0.0F, 0.0F, 20.0F};
    SceneNode shape;
    shape.id = "shape";
    shape.kind = SceneNode::Kind::path;
    shape.bounds = {60.0F, 30.0F, 0.0F, 0.0F};
    shape.fill = Color::hex(0x22C55E);
    shape.path = triangle;

    Scene base;
    base.width = 200.0F;
    base.height = 100.0F;
    Scene rotated = base;
    shape.transform = rotation(30.0F);
    rotated.root.children.push_back(shape);
    check(!lowered_pixels(rotated, "shape").empty() && added_matches_lowering(base, rotated, "shape"),
          "a path under a rotation damages its lowered, transformed extent");

    Scene faded = base;
    SceneNode group;
    group.id = "g";
    group.opacity = 0.5F;
    group.children.push_back(rectangle("a", {10.0F, 10.0F, 20.0F, 20.0F}));
    shape.transform.reset();
    group.children.push_back(shape);
    faded.root.children.push_back(group);
    Scene folded = faded;
    child(folded, "g")->children.erase(child(folded, "g")->children.begin());
    check(covers(diff(faded, folded), lowered_pixels(folded, "shape")),
          "a path left alone in a faded group is damaged when the group's opacity folds into it");
}

void tracker() {
    check(vellum::graphics::parse_invalidation_reason("state") == InvalidationReason::state &&
              vellum::graphics::parse_invalidation_reason("timer") == InvalidationReason::timer &&
              vellum::graphics::parse_invalidation_reason("promise") == InvalidationReason::promise &&
              vellum::graphics::parse_invalidation_reason("service") == InvalidationReason::service &&
              vellum::graphics::parse_invalidation_reason("reload") == InvalidationReason::reload &&
              !vellum::graphics::parse_invalidation_reason("resize"),
          "host invalidation reasons parse");

    DamageTracker tracker(exact_policy());
    tracker.mark_dirty("a");
    tracker.mark_dirty_all(InvalidationReason::reload);
    check(tracker.pending_damage().empty() && !tracker.has_scene(),
          "before the first commit marking does nothing");

    const Scene scene = base_scene();
    tracker.commit(scene, 1.0F);
    auto damage = tracker.take_damage();
    check(damage.is_full_frame() && damage.frame() == PixelRect{0, 0, 200, 100}, "first scene repaints the frame");
    check(tracker.last_stats().area_pixels == 20000 && tracker.last_stats().explicit_full_frame,
          "take_damage reports telemetry");

    tracker.commit(scene, 1.0F);
    check(tracker.take_damage().empty(), "unchanged commit has no damage");

    tracker.mark_dirty("b");
    damage = tracker.take_damage();
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{100, 10, 120, 30}, "mark_dirty damages the node");

    tracker.mark_dirty("root");
    damage = tracker.take_damage();
    check(covers(damage, {10, 10, 30, 30}) && covers(damage, {100, 10, 120, 30}) && damage.area() == 800,
          "mark_dirty covers descendants");

    tracker.mark_dirty("missing");
    check(tracker.take_damage().is_full_frame(), "unknown node repaints the frame");

    tracker.mark_dirty_all(InvalidationReason::state);
    check(tracker.take_damage().is_full_frame(), "mark_dirty_all repaints the frame");
    tracker.invalidate(InvalidationReason::reload, "a");
    check(tracker.take_damage().is_full_frame(), "reload repaints the frame");
    tracker.invalidate(InvalidationReason::timer);
    check(tracker.take_damage().is_full_frame(), "invalidation without a node repaints the frame");
    for (const auto reason : {InvalidationReason::state, InvalidationReason::timer,
                              InvalidationReason::promise, InvalidationReason::service}) {
        tracker.invalidate(reason, "a");
        damage = tracker.take_damage();
        check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{10, 10, 30, 30},
              "node invalidation damages only that node");
    }

    Scene moved = scene;
    child(moved, "a")->bounds.y = 50.0F;
    tracker.mark_dirty("b");
    tracker.commit(moved, 1.0F);
    damage = tracker.take_damage();
    check(damage.area() == 1200, "commit accumulates with pending node damage");

    tracker.commit(moved, 2.0F);
    damage = tracker.take_damage();
    check(damage.is_full_frame() && damage.frame() == PixelRect{0, 0, 400, 200}, "scale change repaints the frame");
}

}  // namespace

int main() {
    geometry();
    union_and_intersection();
    coalescing_at_budget();
    threshold_collapse();
    many_small_rects();
    scene_diff();
    group_diff();
    text_diff();
    mesh_diff();
    path_diff();
    stroke_reach();
    propagation_hooks();
    unusual_bounds();
    duplicate_ids();
    review_hardening();
    compositing_damage();
    transform_geometry();
    composite_path_changes();
    whole_area_closure();
    path_damage();
    tracker();
    if (failures != 0) {
        std::fprintf(stderr, "%d damage check(s) failed\n", failures);
        return 1;
    }
    std::puts("damage: all checks passed");
    return 0;
}
