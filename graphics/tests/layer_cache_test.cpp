#include <vellum/graphics/layer_cache.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

void expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
}

SceneNode rectangle(std::string id, Rect bounds, std::uint32_t color) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = Color::hex(color)};
}

SceneNode group(std::string id, Rect bounds, std::vector<SceneNode> children,
                float opacity = 1.0F) {
    return {.id = std::move(id), .kind = SceneNode::Kind::group, .bounds = bounds,
            .children = std::move(children), .opacity = opacity};
}

// root
//   panel (group): panel/a, panel/b, panel/inner (group): panel/inner/c, panel/inner/d
//   fade (group, opacity 0.5): fade/a, fade/b
//   lone (rectangle)
Scene base_scene() {
    Scene scene{.width = 400.0F, .height = 300.0F};
    scene.root = group("root", {0.0F, 0.0F, 400.0F, 300.0F}, {});
    scene.root.children.push_back(group(
        "panel", {10.0F, 10.0F, 200.0F, 120.0F},
        {rectangle("panel/a", {0.0F, 0.0F, 50.0F, 40.0F}, 0x112233),
         rectangle("panel/b", {60.0F, 0.0F, 50.0F, 40.0F}, 0x223344),
         group("panel/inner", {0.0F, 50.0F, 120.0F, 60.0F},
               {rectangle("panel/inner/c", {0.0F, 0.0F, 30.0F, 30.0F}, 0x334455),
                rectangle("panel/inner/d", {40.0F, 0.0F, 30.0F, 30.0F}, 0x445566)})}));
    scene.root.children.push_back(group(
        "fade", {220.0F, 10.0F, 150.0F, 100.0F},
        {rectangle("fade/a", {0.0F, 0.0F, 60.0F, 60.0F}, 0x556677),
         rectangle("fade/b", {30.0F, 30.0F, 60.0F, 60.0F}, 0x667788)},
        0.5F));
    scene.root.children.push_back(rectangle("lone", {10.0F, 200.0F, 40.0F, 40.0F}, 0x778899));
    return scene;
}

SceneNode& node(Scene& scene, const std::string& id) {
    std::function<SceneNode*(SceneNode&)> find = [&](SceneNode& current) -> SceneNode* {
        if (current.id == id) return &current;
        for (auto& child : current.children) {
            if (auto* found = find(child)) return found;
        }
        return nullptr;
    };
    auto* found = find(scene.root);
    if (found == nullptr) throw std::runtime_error("missing node " + id);
    return *found;
}

const PaintCommandRange* range_of(const std::vector<PaintCommandRange>& ranges,
                                  const std::string& id) {
    for (const auto& range : ranges) {
        if (range.node != nullptr && range.node->id == id) return &range;
    }
    return nullptr;
}

void subtree_ranges() {
    Scene scene = base_scene();
    // A faded group with one drawing member folds its opacity and loses its
    // layer slot; its descendants' ranges must shift with the erase.
    scene.root.children.insert(
        scene.root.children.begin(),
        group("folded", {0.0F, 0.0F, 10.0F, 10.0F},
              {group("folded/inner", {0.0F, 0.0F, 10.0F, 10.0F},
                     {rectangle("folded/inner/x", {0.0F, 0.0F, 5.0F, 5.0F}, 0x000000)})},
              0.5F));
    std::vector<PaintCommandRange> ranges;
    const auto commands = make_paint_commands(scene, &ranges);
    expect(make_paint_commands(scene).size() == commands.size(),
           "reporting ranges does not change the command stream");
    expect(!ranges.empty() && ranges.front().node == &scene.root &&
               ranges.front().begin == 0U && ranges.front().end == commands.size(),
           "the root's range is the whole stream");
    bool consistent = true;
    for (const auto& range : ranges) {
        if (range.begin > range.end || range.end > commands.size()) consistent = false;
        for (auto index = range.begin; index < range.end; ++index) {
            const auto& id = commands[index].node_id;
            if (id.rfind(range.node->id, 0) != 0 && range.node->id != "root") consistent = false;
        }
    }
    expect(consistent, "every range holds only its subtree's commands");
    const auto* folded = range_of(ranges, "folded");
    const auto* inner = range_of(ranges, "folded/inner/x");
    expect(folded != nullptr && inner != nullptr && folded->end - folded->begin == 1U &&
               inner->begin == folded->begin && commands[inner->begin].node_id == "folded/inner/x",
           "a folded group's range and its descendants' ranges follow the erased layer slot");
    const auto* fade = range_of(ranges, "fade");
    expect(fade != nullptr && commands[fade->begin].kind == PaintCommand::Kind::begin_layer &&
               commands[fade->end - 1U].kind == PaintCommand::Kind::end_layer &&
               fade->end - fade->begin == 4U,
           "a faded group's range is bracketed by its layer commands");
    const auto* panel = range_of(ranges, "panel");
    const auto* nested = range_of(ranges, "panel/inner");
    expect(panel != nullptr && nested != nullptr && panel->end - panel->begin == 4U &&
               nested->begin >= panel->begin && nested->end <= panel->end &&
               nested->end - nested->begin == 2U,
           "a nested group's range lies inside its parent's");
    std::vector<PaintCommandRange> refused{{.node = nullptr, .begin = 9, .end = 9}};
    Scene future = scene;
    future.scene_version = kSceneVersion + 1U;
    expect(make_paint_commands(future, &refused).empty() && refused.empty(),
           "an unsupported scene clears the ranges and yields nothing");
}

using Mutation = std::pair<std::string, std::function<void(PaintCommand&)>>;

void equality_covers_every_field() {
    PaintCommand base{
        .kind = PaintCommand::Kind::rectangle,
        .node_id = "n",
        .bounds = {1.0F, 2.0F, 3.0F, 4.0F},
        .fill = Color::hex(0x101010),
        .fill_gradients = {{.angle_degrees = 90.0F,
                            .stops = {{.position = 0.0F, .color = Color::hex(0)},
                                      {.position = 1.0F, .color = Color::hex(0xFFFFFF)}}}},
        .box_shadows = {{.offset_x = 1.0F, .blur_radius = 2.0F, .color = Color::hex(0)}},
        .corner_radius = 2.0F,
        .text = "t",
        .text_runs = {{.text = "r"}},
        .mesh = std::make_shared<TriangleMesh>(),
        .path = std::make_shared<PathGeometry>(),
        .image = ImageReference{.content_hash = "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                                .source_rect = Rect{1.0F, 2.0F, 3.0F, 4.0F},
                                .fit = ImageFit::cover,
                                .sampling = ImageSampling::nearest},
        .image_asset = std::make_shared<ImageAsset>(),
    };
    expect(paint_commands_equal(base, base), "a command equals itself");
    const std::vector<Mutation> mutations{
        {"kind", [](PaintCommand& c) { c.kind = PaintCommand::Kind::text; }},
        {"node_id", [](PaintCommand& c) { c.node_id = "m"; }},
        {"bounds.x", [](PaintCommand& c) { c.bounds.x += 1.0F; }},
        {"bounds.y", [](PaintCommand& c) { c.bounds.y += 1.0F; }},
        {"bounds.width", [](PaintCommand& c) { c.bounds.width += 1.0F; }},
        {"bounds.height", [](PaintCommand& c) { c.bounds.height += 1.0F; }},
        {"fill", [](PaintCommand& c) { c.fill.alpha = 0.5F; }},
        {"gradient angle", [](PaintCommand& c) { c.fill_gradients[0].angle_degrees = 0.0F; }},
        {"gradient repeating", [](PaintCommand& c) { c.fill_gradients[0].repeating = true; }},
        {"gradient repeat length",
         [](PaintCommand& c) { c.fill_gradients[0].repeat_length = 3.0F; }},
        {"gradient stop position",
         [](PaintCommand& c) { c.fill_gradients[0].stops[1].position = 0.5F; }},
        {"gradient stop colour",
         [](PaintCommand& c) { c.fill_gradients[0].stops[0].color.red = 0.5F; }},
        {"gradient count", [](PaintCommand& c) { c.fill_gradients.clear(); }},
        {"shadow offset x", [](PaintCommand& c) { c.box_shadows[0].offset_x = 4.0F; }},
        {"shadow offset y", [](PaintCommand& c) { c.box_shadows[0].offset_y = 4.0F; }},
        {"shadow blur", [](PaintCommand& c) { c.box_shadows[0].blur_radius = 4.0F; }},
        {"shadow spread", [](PaintCommand& c) { c.box_shadows[0].spread_radius = 4.0F; }},
        {"shadow colour", [](PaintCommand& c) { c.box_shadows[0].color.green = 0.5F; }},
        {"shadow count", [](PaintCommand& c) { c.box_shadows.clear(); }},
        {"corner_radius", [](PaintCommand& c) { c.corner_radius = 5.0F; }},
        {"text", [](PaintCommand& c) { c.text = "u"; }},
        {"font_size", [](PaintCommand& c) { c.font_size = 20.0F; }},
        {"font_family", [](PaintCommand& c) { c.font_family = "Jost"; }},
        {"font_weight", [](PaintCommand& c) { c.font_weight = 700; }},
        {"letter_spacing", [](PaintCommand& c) { c.letter_spacing = 1.0F; }},
        {"underline", [](PaintCommand& c) { c.underline = true; }},
        {"strikethrough", [](PaintCommand& c) { c.strikethrough = true; }},
        {"run text", [](PaintCommand& c) { c.text_runs[0].text = "s"; }},
        {"run family", [](PaintCommand& c) { c.text_runs[0].style.font_family = "Jost"; }},
        {"run weight", [](PaintCommand& c) { c.text_runs[0].style.font_weight = 700; }},
        {"run size", [](PaintCommand& c) { c.text_runs[0].style.font_size = 30.0F; }},
        {"run spacing", [](PaintCommand& c) { c.text_runs[0].style.letter_spacing = 2.0F; }},
        {"run colour", [](PaintCommand& c) { c.text_runs[0].style.color.blue = 0.5F; }},
        {"run underline", [](PaintCommand& c) { c.text_runs[0].style.underline = true; }},
        {"run strikethrough",
         [](PaintCommand& c) { c.text_runs[0].style.strikethrough = true; }},
        {"run count", [](PaintCommand& c) { c.text_runs.push_back({}); }},
        {"mesh", [](PaintCommand& c) { c.mesh = std::make_shared<TriangleMesh>(); }},
        {"path", [](PaintCommand& c) {
             auto changed = std::make_shared<PathGeometry>();
             changed->verbs = {PathVerb::move};
             changed->values = {1.0F, 2.0F};
             c.path = std::move(changed);
         }},
        {"image hash", [](PaintCommand& c) { c.image->content_hash[7] = '1'; }},
        {"image source rect", [](PaintCommand& c) { c.image->source_rect->x += 1.0F; }},
        {"image fit", [](PaintCommand& c) { c.image->fit = ImageFit::none; }},
        {"image sampling", [](PaintCommand& c) { c.image->sampling = ImageSampling::linear; }},
        {"image asset", [](PaintCommand& c) { c.image_asset = std::make_shared<ImageAsset>(); }},
        {"transform a", [](PaintCommand& c) { c.transform.a = 2.0F; }},
        {"transform b", [](PaintCommand& c) { c.transform.b = 2.0F; }},
        {"transform c", [](PaintCommand& c) { c.transform.c = 2.0F; }},
        {"transform d", [](PaintCommand& c) { c.transform.d = 2.0F; }},
        {"transform tx", [](PaintCommand& c) { c.transform.tx = 2.0F; }},
        {"transform ty", [](PaintCommand& c) { c.transform.ty = 2.0F; }},
        {"opacity", [](PaintCommand& c) { c.opacity = 0.5F; }},
        {"radius top_left", [](PaintCommand& c) { c.corner_radii.top_left = 1.0F; }},
        {"radius top_right", [](PaintCommand& c) { c.corner_radii.top_right = 1.0F; }},
        {"radius bottom_right", [](PaintCommand& c) { c.corner_radii.bottom_right = 1.0F; }},
        {"radius bottom_left", [](PaintCommand& c) { c.corner_radii.bottom_left = 1.0F; }},
        {"layer_unbounded", [](PaintCommand& c) { c.layer_unbounded = true; }},
    };
    for (const auto& [name, mutate] : mutations) {
        PaintCommand changed = base;
        mutate(changed);
        expect(!paint_commands_equal(base, changed), "a change to " + name + " is a change");
    }
    PaintCommand negative_zero = base;
    negative_zero.bounds.x = -0.0F;
    PaintCommand positive_zero = base;
    positive_zero.bounds.x = 0.0F;
    expect(!paint_commands_equal(negative_zero, positive_zero),
           "floats compare bitwise, so -0 and +0 differ");
}

void change_classification() {
    const PaintCommand rect{.kind = PaintCommand::Kind::rectangle, .node_id = "r",
                            .bounds = {0.0F, 0.0F, 10.0F, 10.0F}};
    const std::vector<PaintCommand> before{rect};
    auto moved = before;
    moved[0].bounds.x = 5.0F;
    moved[0].transform.tx = 1.0F;
    auto recoloured = before;
    recoloured[0].fill.red = 1.0F;
    auto both = moved;
    both[0].fill.red = 1.0F;
    auto rekinded = before;
    rekinded[0].kind = PaintCommand::Kind::mesh;
    auto meshed = before;
    meshed[0].mesh = std::make_shared<TriangleMesh>();
    auto pathed = before;
    pathed[0].path = std::make_shared<PathGeometry>();
    auto longer = before;
    longer.push_back(rect);
    expect(classify_change(before, moved) == LayerInvalidation::bounds,
           "a move is a bounds change");
    expect(classify_change(before, recoloured) == LayerInvalidation::content,
           "a recolour is a content change");
    expect(classify_change(before, both) == LayerInvalidation::content,
           "a move with a recolour is a content change");
    expect(classify_change(before, rekinded) == LayerInvalidation::structure,
           "a changed kind is a structure change");
    expect(classify_change(before, longer) == LayerInvalidation::structure,
           "a new command is a structure change");
    expect(classify_change(before, meshed) == LayerInvalidation::resource,
           "a replaced mesh is a resource change");
    expect(classify_change(before, pathed) == LayerInvalidation::resource,
           "a replaced path is a resource change");
}

struct Frame final {
    std::vector<PaintCommand> commands;
    std::vector<PaintCommandRange> ranges;
    std::vector<LayerCache::Layer> layers;
};

// Plans a frame and admits every build at the given size, as the renderer does.
Frame step(LayerCache& cache, const Scene& scene, std::int64_t pixels = 100,
           bool count = true, bool exact_only = false) {
    Frame frame;
    frame.commands = make_paint_commands(scene, &frame.ranges);
    frame.layers = cache.plan(frame.commands, frame.ranges, 400 * 300,
                              {.count_frame = count, .exact_only = exact_only});
    (void)cache.take_dropped();
    for (const auto& layer : frame.layers) {
        if (layer.build) {
            (void)cache.admit(layer.node_id, pixels);
        } else {
            cache.record_hit(layer.node_id);
        }
    }
    return frame;
}

const LayerCache::Layer* planned(const Frame& frame, const std::string& id) {
    for (const auto& layer : frame.layers) {
        if (layer.node_id == id) return &layer;
    }
    return nullptr;
}

std::string ids(const Frame& frame) {
    std::string out;
    for (const auto& layer : frame.layers) {
        out += layer.node_id + (layer.build ? "(build) " : "(hit) ");
    }
    return out;
}

void triggers() {
    LayerCache cache({.allow_inexact = true});
    Scene scene = base_scene();
    auto frame = step(cache, scene);
    const auto* fade = planned(frame, "fade");
    expect(fade != nullptr && fade->build && fade->opacity_layer &&
               fade->trigger == LayerCacheTrigger::opacity && planned(frame, "panel") == nullptr,
           "a faded group is cached from its first frame; a static one is not yet", ids(frame));
    expect(planned(frame, "root") == nullptr, "the scene root is never cached");
    expect(planned(frame, "lone") == nullptr, "a subtree below min_draws is never cached");
    expect(cache.stats().candidates == 3U, "root and single-draw nodes are not candidates",
           std::to_string(cache.stats().candidates));

    frame = step(cache, scene);
    expect(planned(frame, "fade") != nullptr && !planned(frame, "fade")->build &&
               cache.stats().hits == 1U && cache.stats().commands_from_cache == 2U,
           "the faded group's image is reused on the next frame", ids(frame));
    frame = step(cache, scene);
    expect(planned(frame, "panel") == nullptr, "two unchanged frames do not cache a subtree");
    frame = step(cache, scene);
    const auto* panel = planned(frame, "panel");
    expect(panel != nullptr && panel->build &&
               panel->trigger == LayerCacheTrigger::static_content &&
               planned(frame, "panel/inner") == nullptr,
           "the outermost subtree unchanged for static_frames frames is cached", ids(frame));

    node(scene, "fade").opacity = 0.25F;
    frame = step(cache, scene);
    expect(planned(frame, "fade") != nullptr && !planned(frame, "fade")->build,
           "an opacity change alone reuses the faded group's image", ids(frame));

    LayerCache expensive({.allow_inexact = true, .expensive_draws = 4});
    Scene big = base_scene();
    frame = step(expensive, big);
    expect(planned(frame, "panel") == nullptr, "an expensive subtree waits for one repeat");
    frame = step(expensive, big);
    expect(planned(frame, "panel") != nullptr &&
               planned(frame, "panel")->trigger == LayerCacheTrigger::expensive,
           "an expensive subtree is cached once it repeats", ids(frame));
    node(big, "panel/a").fill = Color::hex(0xABCDEF);
    frame = step(expensive, big);
    expect(planned(frame, "panel") == nullptr && !expensive.has_image("panel") &&
               expensive.stats().invalidated(LayerInvalidation::content) == 1U,
           "a changed expensive subtree is dropped as a content change until it repeats",
           ids(frame));
    expect(planned(frame, "panel/inner") == nullptr,
           "an unchanged child below the expensive threshold waits for static_frames");
}

void identity_rules() {
    LayerCache cache({.allow_inexact = true, .static_frames = 1});
    Scene scene = base_scene();
    node(scene, "panel/b").id = "panel";  // a second node with the panel's id
    for (int frame = 0; frame < 3; ++frame) (void)step(cache, scene);
    auto frame = step(cache, scene);
    expect(planned(frame, "panel") == nullptr,
           "a node whose id is shared is never cached", ids(frame));
    expect(planned(frame, "panel/inner") != nullptr,
           "a uniquely named descendant of it still is", ids(frame));

    Scene unnamed = base_scene();
    node(unnamed, "panel").id.clear();
    LayerCache second({.allow_inexact = true, .static_frames = 1});
    (void)step(second, unnamed);
    frame = step(second, unnamed);
    expect(planned(frame, "panel/inner") != nullptr && planned(frame, "") == nullptr,
           "a node without an id is never cached", ids(frame));
}

void drops() {
    LayerCache cache({.allow_inexact = true, .static_frames = 1});
    Scene scene = base_scene();
    (void)step(cache, scene);
    auto frame = step(cache, scene);
    expect(cache.has_image("panel") && cache.has_image("fade"), "panel and fade are cached",
           ids(frame));

    node(scene, "panel/a").fill = Color::hex(0xFF0000);
    frame = step(cache, scene);
    expect(cache.stats().invalidated(LayerInvalidation::content) == 1U &&
               !cache.has_image("panel") && planned(frame, "panel/inner") != nullptr,
           "a changed static subtree is dropped as a content change and its unchanged "
           "child cached instead",
           ids(frame));

    // The panel's image was dropped before it was ever drawn from, so it backs
    // off for a couple of frames before it is built again.
    frame = step(cache, scene);
    expect(planned(frame, "panel") == nullptr, "a wasted build backs off", ids(frame));
    for (int index = 0; index < 3 && planned(frame, "panel") == nullptr; ++index) {
        frame = step(cache, scene);
    }
    expect(planned(frame, "panel") != nullptr && planned(frame, "panel")->build &&
               cache.stats().invalidated(LayerInvalidation::superseded) == 1U,
           "the parent caches again and supersedes its child", ids(frame));

    node(scene, "fade/a").bounds.x += 3.0F;
    frame = step(cache, scene);
    expect(cache.stats().invalidated(LayerInvalidation::bounds) == 1U &&
               planned(frame, "fade") != nullptr && planned(frame, "fade")->build,
           "a moved member rebuilds the faded group's image as a bounds change", ids(frame));
    node(scene, "fade/a").fill = Color::hex(0x00FF00);
    (void)step(cache, scene);
    expect(cache.stats().invalidated(LayerInvalidation::content) == 2U,
           "a recoloured member rebuilds it as a content change");
    node(scene, "fade").children.push_back(rectangle("fade/c", {0.0F, 0.0F, 5.0F, 5.0F}, 0));
    (void)step(cache, scene);
    expect(cache.stats().invalidated(LayerInvalidation::structure) == 1U,
           "a new member rebuilds it as a structure change");

    auto& children = scene.root.children;
    std::erase_if(children, [](const SceneNode& child) { return child.id == "fade"; });
    (void)step(cache, scene);
    expect(cache.stats().invalidated(LayerInvalidation::visibility) == 1U &&
               !cache.has_image("fade"),
           "a removed subtree's image is dropped for visibility");

    (void)step(cache, scene);
    expect(cache.has_image("panel"), "panel is cached before a host invalidation");
    cache.invalidate_node("panel/inner");
    const auto dropped = cache.take_dropped();
    expect(dropped.size() == 1U && dropped[0].node_id == "panel" &&
               dropped[0].reason == LayerInvalidation::host_invalidate,
           "invalidating a descendant group without paint drops the image containing it");
    cache.invalidate_node("no-such-node");
    expect(cache.take_dropped().empty(), "invalidating an unknown node drops nothing");

    (void)step(cache, scene);
    (void)step(cache, scene);
    expect(cache.has_image("panel"), "panel caches again after static_frames");
    cache.invalidate_all(LayerInvalidation::device_lost);
    expect(!cache.has_image("panel") && cache.stats().layers == 0U &&
               cache.stats().invalidated(LayerInvalidation::device_lost) == 1U,
           "invalidating everything drops every image");
    LayerCache warm({.allow_inexact = true, .static_frames = 2});
    for (int index = 0; index < 3; ++index) (void)step(warm, scene);
    expect(warm.has_image("panel"), "a warm cache holds panel");
    warm.invalidate_all(LayerInvalidation::resize);
    frame = step(warm, scene);
    expect(planned(frame, "panel") == nullptr,
           "invalidating everything restarts the static-frame count", ids(frame));

    cache.set_policy({.enabled = false});
    frame = step(cache, scene);
    expect(frame.layers.empty() && cache.stats().layers == 0U,
           "a disabled policy plans no layers");
}

void budget() {
    LayerCache counted({.allow_inexact = true, .static_frames = 1, .max_layers = 1});
    Scene scene = base_scene();
    (void)step(counted, scene);
    (void)step(counted, scene);
    expect(counted.stats().layers == 1U && counted.stats().refused_over_budget == 1U,
           "max_layers caps the number of images",
           std::to_string(counted.stats().layers));

    LayerCache sized({.allow_inexact = true, .static_frames = 1, .max_pixels = 150});
    (void)step(sized, scene);
    (void)step(sized, scene);
    expect(sized.stats().layers == 1U && sized.stats().layer_pixels == 100 &&
               sized.stats().refused_over_budget == 1U,
           "max_pixels caps the total image area");

    LayerCache defaulted({.allow_inexact = true, .static_frames = 1});
    std::vector<PaintCommandRange> ranges;
    const auto commands = make_paint_commands(scene, &ranges);
    const auto layers = defaulted.plan(commands, ranges, 1000);
    expect(!layers.empty() && !defaulted.admit(layers[0].node_id, 2001) &&
               defaulted.admit(layers[0].node_id, 2000),
           "the default pixel budget is twice the target area");
    LayerCache empty;
    const auto planned_layers = empty.plan(commands, ranges, 1000);
    expect(!planned_layers.empty() && !empty.admit(planned_layers[0].node_id, 0) &&
               !empty.has_image(planned_layers[0].node_id),
           "an empty extent is never admitted");
    expect(!empty.admit("no-such-node", 10), "an unplanned node is never admitted");
}

void abandon_and_replay() {
    LayerCache cache;
    Scene scene = base_scene();
    std::vector<PaintCommandRange> ranges;
    const auto commands = make_paint_commands(scene, &ranges);
    auto layers = cache.plan(commands, ranges, 400 * 300);
    expect(layers.size() == 1U && cache.admit("fade", 100), "fade is admitted");
    cache.abandon("fade", LayerInvalidation::frame_failed);
    expect(!cache.has_image("fade") && cache.stats().builds == 0U && cache.stats().layers == 0U &&
               cache.stats().invalidated(LayerInvalidation::frame_failed) == 1U,
           "an abandoned build leaves no image and no reserved budget");

    LayerCache replay({.allow_inexact = true, .static_frames = 2});
    (void)step(replay, scene);
    (void)step(replay, scene, 100, false);
    (void)step(replay, scene, 100, false);
    auto frame = step(replay, scene);
    expect(planned(frame, "panel") == nullptr,
           "replayed frames do not advance the static-frame count", ids(frame));
    frame = step(replay, scene);
    expect(planned(frame, "panel") != nullptr, "counted frames do", ids(frame));

    LayerCache exact({.allow_inexact = true, .static_frames = 1, .expensive_draws = 1});
    for (int index = 0; index < 4; ++index) frame = step(exact, scene, 100, true, true);
    expect(frame.layers.size() == 1U && frame.layers[0].node_id == "fade" &&
               !exact.has_image("panel"),
           "exact_only caches faded groups and nothing static or expensive", ids(frame));
    frame = step(exact, scene);
    expect(planned(frame, "panel") != nullptr,
           "the static count kept running while exact_only held the subtree back", ids(frame));
    frame = step(exact, scene, 100, true, true);
    expect(planned(frame, "panel") == nullptr && !exact.has_image("panel") &&
               exact.stats().invalidated(LayerInvalidation::superseded) == 1U,
           "returning to exact_only drops the static image", ids(frame));

    LayerCache gap({.allow_inexact = true, .static_frames = 1});
    (void)step(gap, scene);
    Scene without = scene;
    std::erase_if(without.root.children, [](const SceneNode& child) {
        return child.id == "panel";
    });
    (void)step(gap, without);
    frame = step(gap, scene);
    expect(planned(frame, "panel") == nullptr,
           "a subtree that left the scene for a frame starts counting again", ids(frame));
}

void default_policy_is_exact() {
    LayerCache cache;
    Scene scene = base_scene();
    Frame frame;
    for (int index = 0; index < 8; ++index) frame = step(cache, scene);
    expect(frame.layers.size() == 1U && frame.layers[0].node_id == "fade" &&
               !cache.has_image("panel"),
           "the default policy caches only faded groups, however long a subtree is static",
           ids(frame));

    Scene flat = base_scene();
    std::erase_if(flat.root.children, [](const SceneNode& child) { return child.id == "fade"; });
    frame = step(cache, flat);
    expect(frame.layers.empty() && cache.stats().candidates == 0U && !cache.has_image("fade"),
           "a frame without a faded group skips planning and drops the old images",
           std::to_string(cache.stats().candidates));
}

void wasted_builds_back_off() {
    // An expensive subtree with a child that changes every other frame.
    LayerCache cache({.allow_inexact = true, .expensive_draws = 4});
    Scene scene = base_scene();
    std::size_t builds = 0;
    for (int index = 0; index < 20; ++index) {
        if (index % 2 == 1) {
            node(scene, "panel/a").fill = Color::hex(index % 4 == 1 ? 0xFF0000U : 0x00FF00U);
        }
        const auto frame = step(cache, scene);
        if (const auto* panel = planned(frame, "panel"); panel != nullptr && panel->build) {
            ++builds;
        }
    }
    expect(builds <= 4U, "a subtree whose images are never used backs off instead of "
                         "rebuilding every other frame",
           "builds=" + std::to_string(builds));

    LayerCache steady({.allow_inexact = true, .expensive_draws = 4});
    Scene still = base_scene();
    (void)step(steady, still);
    auto frame = step(steady, still);
    frame = step(steady, still);
    expect(planned(frame, "panel") != nullptr && !planned(frame, "panel")->build,
           "a subtree that stays unchanged is built once and reused", ids(frame));

    LayerCache refused({.allow_inexact = true, .static_frames = 1, .max_layers = 1});
    std::size_t refusals = 0;
    for (int index = 0; index < 6; ++index) {
        (void)step(refused, still);
        refusals += refused.stats().refused_over_budget;
    }
    // panel and then panel/inner are each refused once, then left alone.
    expect(refusals == 2U, "a refused subtree is not re-planned on the next frames",
           std::to_string(refusals));
}

}  // namespace

int main() {
    subtree_ranges();
    equality_covers_every_field();
    change_classification();
    triggers();
    identity_rules();
    drops();
    budget();
    abandon_and_replay();
    default_policy_is_exact();
    wasted_builds_back_off();
    std::cout << (failures == 0 ? "layer cache policy: all checks passed\n"
                                : "layer cache policy: " + std::to_string(failures) +
                                      " failed\n");
    return failures == 0 ? 0 : 1;
}
