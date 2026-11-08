#include <vellum/graphics/damage.hpp>

#include <cstdint>
#include <cstdio>
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
    check(covers(diff(before, reparented), {100, 10, 120, 30}), "reparented node is damaged");
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
    check(damage.rects().size() == 1 && damage.rects()[0] == PixelRect{0, 40, 120, 80},
          "text change damages the text bounds and overflow");

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
    check(tracker.pending_damage().empty() && !tracker.has_scene(), "no scene means nothing to damage yet");

    const Scene scene = base_scene();
    tracker.commit(scene, 1.0F);
    auto damage = tracker.take_damage();
    check(damage.is_full_frame() && damage.frame() == PixelRect{0, 0, 200, 100}, "first scene repaints the frame");
    check(tracker.last_stats().area_pixels == 20000, "take_damage reports telemetry");

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

    tracker.mark_dirty_all(InvalidationReason::reload, "a");
    check(tracker.take_damage().is_full_frame(), "reload repaints the frame");
    tracker.mark_dirty_all(InvalidationReason::timer);
    check(tracker.take_damage().is_full_frame(), "invalidation without a node repaints the frame");
    for (const auto reason : {InvalidationReason::state, InvalidationReason::timer,
                              InvalidationReason::promise, InvalidationReason::service}) {
        tracker.mark_dirty_all(reason, "a");
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
    propagation_hooks();
    tracker();
    if (failures != 0) {
        std::fprintf(stderr, "%d damage check(s) failed\n", failures);
        return 1;
    }
    std::puts("damage: all checks passed");
    return 0;
}
