#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/effect.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/scene_acceptance.hpp>

#include <cmath>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace vellum::graphics;

int failures = 0;

bool expect(bool condition, const std::string& what, const std::string& detail = {}) {
    std::cout << (condition ? "PASS " : "FAIL ") << what;
    if (!detail.empty()) std::cout << ": " << detail;
    std::cout << '\n';
    if (!condition) ++failures;
    return condition;
}

std::string describe(const PixelRect& rect) {
    return "[" + std::to_string(rect.left) + "," + std::to_string(rect.top) + "," +
           std::to_string(rect.right) + "," + std::to_string(rect.bottom) + ")";
}

NodeEffect blur(float sigma) { return {.kind = EffectKind::blur, .sigma = sigma}; }

NodeEffect shadow(float sigma, float dx, float dy) {
    return {.kind = EffectKind::drop_shadow,
            .sigma = sigma,
            .offset_x = dx,
            .offset_y = dy,
            .color = Color::rgba(0.0F, 0.0F, 0.0F, 0.6F)};
}

NodeEffect backdrop(float sigma) { return {.kind = EffectKind::backdrop_blur, .sigma = sigma}; }

NodeEffect glass(std::vector<MaterialValue> values = {}) {
    return {.kind = EffectKind::material,
            .material = std::string(kFrostedGlassMaterial),
            .values = std::move(values)};
}

SceneNode box(std::string id, Rect bounds, Color fill = Color::hex(0x38BDF8)) {
    return {.id = std::move(id), .kind = SceneNode::Kind::rectangle, .bounds = bounds,
            .fill = fill};
}

Scene scene_with(std::vector<SceneNode> children, std::uint32_t version = kSceneVersion) {
    Scene scene;
    scene.width = 400.0F;
    scene.height = 300.0F;
    scene.scene_version = version;
    scene.root = {.id = "root", .kind = SceneNode::Kind::group,
                  .bounds = {0.0F, 0.0F, 400.0F, 300.0F}};
    scene.root.children = std::move(children);
    return scene;
}

SceneNode effect_group(std::vector<NodeEffect> effects) {
    SceneNode group{.id = "fx", .kind = SceneNode::Kind::group,
                    .bounds = {100.0F, 80.0F, 120.0F, 90.0F}};
    group.children.push_back(box("fx/a", {10.0F, 10.0F, 40.0F, 30.0F}));
    group.children.push_back(box("fx/b", {50.0F, 40.0F, 50.0F, 40.0F}, Color::hex(0xF97316)));
    group.effects = std::move(effects);
    return group;
}

void version_matrix() {
    for (std::uint32_t version = 1U; version <= 11U; ++version) {
        const auto scene = scene_with({effect_group({blur(4.0F)})}, version);
        const auto acceptance = accept_scene(scene);
        const std::string label = "a version " + std::to_string(version) + " scene with effects";
        if (version < 10U) {
            expect(!scene_fields_match_version(scene) && !acceptance.accepted &&
                       acceptance.diagnostics.size() == 1U &&
                       acceptance.diagnostics[0].code == kSceneVersionMismatchCode &&
                       acceptance.diagnostics[0].property == "effects" &&
                       acceptance.diagnostics[0].node_id == "fx" &&
                       make_paint_commands(scene).empty(),
                   label + " is refused and names the effects", acceptance.reason);
        } else if (version == 10U) {
            expect(scene_fields_match_version(scene) && acceptance.accepted &&
                       acceptance.diagnostics.empty(),
                   label + " is accepted", acceptance.reason);
        } else {
            expect(!acceptance.accepted && make_paint_commands(scene).empty(),
                   label + " is refused as an unsupported version", acceptance.reason);
        }
    }
    for (std::uint32_t version = 1U; version <= 10U; ++version) {
        const auto plain = scene_with({effect_group({})}, version);
        expect(accept_scene(plain).accepted && !make_paint_commands(plain).empty(),
               "a version " + std::to_string(version) + " scene without effects is accepted");
    }
    auto legacy = effect_group({});
    legacy.transform = Affine2D{.a = 0.9F, .b = 0.1F, .c = -0.1F, .d = 0.9F};
    legacy.opacity = 0.6F;
    legacy.clip = NodeClip{.rect = {0.0F, 0.0F, 80.0F, 60.0F}};
    legacy.isolate = true;
    expect(accept_scene(scene_with({legacy}, 5U)).accepted &&
               accept_scene(scene_with({legacy}, 10U)).accepted,
           "transform, opacity, clip and isolation stay legal at versions 5 and 10");
    auto mixed = legacy;
    mixed.effects = {blur(2.0F)};
    const auto old = accept_scene(scene_with({mixed}, 5U));
    expect(!old.accepted && old.diagnostics.size() == 1U &&
               old.diagnostics[0].property == "effects",
           "a version 5 scene mixing legal grouping with effects is refused for the effects",
           old.reason);
}

void effect_validation() {
    const std::vector<std::pair<std::string, std::vector<NodeEffect>>> invalid = {
        {"a NaN sigma", {blur(std::numeric_limits<float>::quiet_NaN())}},
        {"a negative sigma", {blur(-1.0F)}},
        {"a sigma over the limit", {blur(kMaxEffectSigma * 2.0F)}},
        {"an infinite shadow offset",
         {shadow(2.0F, std::numeric_limits<float>::infinity(), 0.0F)}},
        {"a NaN matrix entry",
         {{.kind = EffectKind::color_matrix,
           .matrix = {std::numeric_limits<float>::quiet_NaN()}}}},
        {"two backdrop effects", {backdrop(4.0F), glass()}},
        {"an unknown kind", {{.kind = static_cast<EffectKind>(42)}}},
    };
    for (const auto& [name, effects] : invalid) {
        const auto acceptance = accept_scene(scene_with({effect_group(effects)}));
        expect(!acceptance.accepted && acceptance.diagnostics.size() == 1U &&
                   acceptance.diagnostics[0].code == kInvalidEffectCode &&
                   acceptance.diagnostics[0].node_id == "fx",
               name + " is refused with an invalid-effect error", acceptance.reason);
    }
    auto mask_parent = effect_group({});
    auto mask = box("fx/mask", {0.0F, 0.0F, 60.0F, 60.0F});
    mask.mask = MaskMode::alpha;
    mask.effects = {blur(2.0F)};
    mask_parent.children.push_back(mask);
    const auto masked = accept_scene(scene_with({mask_parent}));
    expect(!masked.accepted && masked.diagnostics.size() == 1U &&
               masked.diagnostics[0].node_id == "fx/mask",
           "a mask carrying effects is refused", masked.reason);
}

void material_registry() {
    auto& registry = MaterialRegistry::shared();
    const auto builtin = registry.find(kFrostedGlassMaterial);
    expect(builtin && builtin->recipe == MaterialRecipe::frosted_glass &&
               builtin->disposition == LoweringDiagnostic::Disposition::portable &&
               builtin->provenance.owner == "vellum" && !builtin->provenance.source.empty() &&
               builtin->uniforms.size() == 3U,
           "Vellum ships the frosted-glass material with provenance and typed uniforms");
    expect(!registry.unregister_extension(kFrostedGlassMaterial) &&
               registry.find(kFrostedGlassMaterial) != nullptr,
           "a Vellum material cannot be unregistered");

    const auto refused = [](const std::vector<NodeEffect>& effects) {
        return accept_scene(scene_with({effect_group(effects)}));
    };
    const auto unknown = refused({{.kind = EffectKind::material, .material = "vellum.nothing"}});
    expect(!unknown.accepted && unknown.diagnostics.size() == 1U &&
               unknown.diagnostics[0].code == kUnknownMaterialCode,
           "an unknown material id is refused", unknown.reason);
    const std::vector<std::pair<std::string, MaterialValue>> bad_values = {
        {"a colour for a number uniform",
         {.name = "blur", .type = MaterialValueType::color, .value = {1.0F, 1.0F, 1.0F, 1.0F}}},
        {"a number for a colour uniform",
         {.name = "tint", .type = MaterialValueType::number, .value = {0.5F}}},
        {"a vector for a number uniform",
         {.name = "saturation", .type = MaterialValueType::vector2, .value = {1.0F, 1.0F}}},
        {"an unknown uniform", {.name = "glow", .type = MaterialValueType::number}},
        {"a value over the range", {.name = "blur", .value = {65.0F}}},
        {"a NaN value", {.name = "saturation", .value = {std::numeric_limits<float>::quiet_NaN()}}},
        {"a colour channel over one",
         {.name = "tint", .type = MaterialValueType::color, .value = {1.5F, 0.0F, 0.0F, 1.0F}}},
        {"an unknown value type",
         {.name = "blur", .type = static_cast<MaterialValueType>(9), .value = {1.0F}}},
    };
    for (const auto& [name, value] : bad_values) {
        const auto acceptance = refused({glass({value})});
        expect(!acceptance.accepted && acceptance.diagnostics.size() == 1U &&
                   acceptance.diagnostics[0].code == kInvalidMaterialValueCode,
               name + " is refused", acceptance.reason);
    }
    const auto twice = refused({glass({{.name = "blur", .value = {4.0F}},
                                       {.name = "blur", .value = {6.0F}}})});
    expect(!twice.accepted && twice.diagnostics[0].code == kInvalidMaterialValueCode,
           "a uniform set twice is refused", twice.reason);
    const auto valid = refused({glass({{.name = "blur", .value = {6.0F}},
                                       {.name = "tint",
                                        .type = MaterialValueType::color,
                                        .value = {0.2F, 0.3F, 0.4F, 0.5F}}})});
    expect(valid.accepted && valid.diagnostics.empty(), "valid material values are accepted",
           valid.reason);
    expect(material_uniform_value(*builtin, {}, "saturation")[0] == 1.4F &&
               material_uniform_value(*builtin, {{.name = "saturation", .value = {2.0F}}},
                                      "saturation")[0] == 2.0F,
           "an unset uniform takes its default and a set one its value");

    MaterialDefinition app{
        .id = "com.example.sheen",
        .provenance = {.owner = "com.example", .source = "sheen.dylib", .revision = "3"},
        .uniforms = {{.name = "angle",
                      .type = MaterialValueType::number,
                      .default_value = {45.0F},
                      .minimum = 0.0F,
                      .maximum = 360.0F}},
        .component = "sheen",
        .passes = 2U,
        .textures = 1U,
    };
    std::string error;
    auto vellum_named = app;
    vellum_named.id = "vellum.sheen";
    expect(!registry.register_extension(vellum_named, &error),
           "an application material cannot claim the vellum namespace", error);
    auto componentless = app;
    componentless.component.clear();
    expect(!registry.register_extension(componentless, &error),
           "an application material names the component that draws it", error);
    auto ownerless = app;
    ownerless.provenance.owner.clear();
    expect(!registry.register_extension(ownerless, &error),
           "an application material records its owner", error);
    auto bad_default = app;
    bad_default.uniforms[0].default_value = {400.0F};
    expect(!registry.register_extension(bad_default, &error),
           "a default outside its range is refused", error);
    auto duplicate_uniform = app;
    duplicate_uniform.uniforms.push_back(app.uniforms[0]);
    expect(!registry.register_extension(duplicate_uniform, &error),
           "a uniform declared twice is refused", error);

    expect(registry.register_extension(app, &error), "an application material registers", error);
    expect(!registry.register_extension(app, &error), "an id registers once", error);
    const auto registered = registry.find(app.id);
    expect(registered && registered->recipe == MaterialRecipe::extension &&
               registered->disposition == LoweringDiagnostic::Disposition::extension,
           "an application material is an extension");
    const NodeEffect sheen{.kind = EffectKind::material,
                           .material = app.id,
                           .values = {{.name = "angle", .value = {90.0F}}}};
    const auto scene = scene_with({effect_group({sheen})});
    const auto acceptance = accept_scene(scene);
    expect(acceptance.accepted && acceptance.diagnostics.size() == 1U &&
               acceptance.diagnostics[0].code == kMaterialExtensionCode &&
               acceptance.diagnostics[0].disposition ==
                   LoweringDiagnostic::Disposition::extension &&
               !effect_draws_natively(sheen),
           "a scene naming an application material is visibly non-portable",
           acceptance.diagnostics.empty() ? acceptance.reason
                                          : acceptance.diagnostics[0].message);
    const auto wrong = accept_scene(scene_with({effect_group(
        {{.kind = EffectKind::material,
          .material = app.id,
          .values = {{.name = "angle", .type = MaterialValueType::color}}}})}));
    expect(!wrong.accepted && wrong.diagnostics[0].code == kInvalidMaterialValueCode,
           "an application material's uniform types are enforced", wrong.reason);
    expect(registry.unregister_extension(app.id) && registry.find(app.id) == nullptr,
           "an application material unregisters");
    const auto gone = accept_scene(scene);
    expect(!gone.accepted && gone.diagnostics[0].code == kUnknownMaterialCode,
           "a scene naming an unregistered material is refused", gone.reason);
}

void costs_and_budget() {
    const auto blur_cost = effect_cost(blur(4.0F));
    expect(blur_cost.radius == 12.0F && blur_cost.passes == 2U && blur_cost.textures == 1U &&
               !blur_cost.backdrop,
           "a blur declares three sigma of radius, two passes and one texture");
    const auto matrix_cost = effect_cost({.kind = EffectKind::color_matrix});
    expect(matrix_cost.radius == 0.0F && matrix_cost.passes == 1U && matrix_cost.textures == 0U,
           "a colour matrix declares no radius, one pass and no texture");
    const auto shadow_cost = effect_cost(shadow(2.0F, -5.0F, 3.0F));
    expect(shadow_cost.radius == 11.0F && shadow_cost.passes == 3U && shadow_cost.textures == 2U,
           "a drop shadow declares its blur reach plus its larger offset");
    const auto backdrop_cost = effect_cost(backdrop(5.0F));
    expect(backdrop_cost.radius == 0.0F && backdrop_cost.backdrop_reach == 15.0F &&
               backdrop_cost.backdrop && backdrop_cost.passes == 3U,
           "a backdrop blur declares the reach it samples, not a filter radius");
    const auto glass_cost = effect_cost(glass({{.name = "blur", .value = {8.0F}}}));
    expect(glass_cost.backdrop && glass_cost.backdrop_reach == 24.0F && glass_cost.passes == 5U &&
               glass_cost.textures == 2U,
           "frosted glass declares its blur reach, five passes and two textures");
    expect(effect_cost(blur(-1.0F)).passes == 0U, "an invalid effect costs nothing");

    const auto within = effect_chain_cost({blur(1.0F), blur(1.0F), blur(1.0F)});
    expect(!within.over_budget && within.total.passes == 7U && within.total.textures == 4U &&
               within.total.radius == 9.0F,
           "three blurs fit the budget and sum their radii");
    const auto passes = effect_chain_cost({blur(1.0F), blur(1.0F), blur(1.0F), blur(1.0F)});
    expect(passes.over_budget && passes.total.passes == 9U,
           "a fourth blur exceeds the pass budget");
    const auto textures = effect_chain_cost({shadow(1.0F, 1.0F, 1.0F), shadow(1.0F, 1.0F, 1.0F)});
    expect(textures.over_budget && textures.total.passes == 7U && textures.total.textures == 5U,
           "two drop shadows exceed the texture budget within the pass budget");
}

bool same_commands(const std::vector<PaintCommand>& a, const std::vector<PaintCommand>& b) {
    if (a.size() != b.size()) return false;
    for (std::size_t index = 0; index < a.size(); ++index) {
        const auto& l = a[index];
        const auto& r = b[index];
        if (l.kind != r.kind || l.node_id != r.node_id || l.effects.size() != r.effects.size() ||
            l.opacity != r.opacity || l.layer_isolated != r.layer_isolated) {
            return false;
        }
    }
    return true;
}

void lowering() {
    const auto commands = make_paint_commands(scene_with({effect_group({blur(4.0F)})}));
    expect(commands.size() == 4U && commands[0].kind == PaintCommand::Kind::begin_layer &&
               commands[0].node_id == "fx" && commands[0].effects.size() == 1U &&
               commands[0].layer_isolated && commands[3].kind == PaintCommand::Kind::end_layer,
           "a node with effects lowers to an isolated layer carrying them");
    const auto& layer = commands[0];
    expect(layer.bounds.x == 110.0F - 12.0F && layer.bounds.y == 90.0F - 12.0F &&
               layer.bounds.width == 90.0F + 24.0F && layer.bounds.height == 70.0F + 24.0F,
           "the layer bounds are the members' extent grown by the chain radius",
           std::to_string(layer.bounds.x) + "," + std::to_string(layer.bounds.y) + " " +
               std::to_string(layer.bounds.width) + "x" + std::to_string(layer.bounds.height));

    auto single = effect_group({blur(2.0F)});
    single.children.resize(1U);
    single.opacity = 0.5F;
    const auto unfolded = make_paint_commands(scene_with({single}));
    expect(unfolded.size() == 3U && unfolded[0].opacity == 0.5F && unfolded[1].opacity == 1.0F,
           "an effect group's opacity is not folded into its one member");

    SceneNode pane{.id = "pane", .kind = SceneNode::Kind::group,
                   .bounds = {20.0F, 30.0F, 100.0F, 60.0F}};
    pane.effects = {backdrop(3.0F)};
    pane.transform = Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    const auto empty = make_paint_commands(scene_with({pane}));
    const bool shaped = empty.size() == 2U && empty[0].kind == PaintCommand::Kind::begin_layer;
    expect(shaped && empty[0].effect_box.x == 20.0F && empty[0].effect_box.width == 100.0F &&
               empty[0].transform.b == 1.0F && empty[0].bounds.width == 60.0F &&
               empty[0].bounds.height == 100.0F,
           "a backdrop group with no members still lowers, bounded by its turned box");

    auto over = effect_group({blur(1.0F), blur(1.0F), blur(1.0F), blur(1.0F)});
    auto without = effect_group({});
    const auto fallback = make_paint_commands(scene_with({over}));
    expect(same_commands(fallback, make_paint_commands(scene_with({without}))),
           "an over-budget chain lowers exactly as the node without effects");
    const auto loss = accept_scene(scene_with({over}));
    expect(loss.accepted && loss.diagnostics.size() == 1U &&
               loss.diagnostics[0].code == kEffectBudgetExceededCode &&
               loss.diagnostics[0].severity == LoweringDiagnostic::Severity::warning &&
               loss.diagnostics[0].node_id == "fx",
           "an over-budget chain is accepted with a loss warning");
    const auto fits = accept_scene(scene_with({effect_group({blur(1.0F), blur(1.0F), blur(1.0F)})}));
    expect(fits.accepted && fits.diagnostics.empty(), "a chain within budget reports no loss");
}

PixelRect damage_bounds(const Scene& before, const Scene& after, float scale = 1.0F) {
    return compute_scene_damage(before, after, scale,
                                {.max_rects = 64, .full_repaint_fraction = 2.0,
                                 .antialias_margin = 0})
        .bounds();
}

void damage_radius() {
    // fx/a sits at (110, 90) 40x30 and moves right by 5.
    const auto edit = [](std::vector<NodeEffect> effects, auto&& adjust) {
        auto before = effect_group(effects);
        adjust(before);
        auto after = before;
        after.children[0].bounds.x += 5.0F;
        return damage_bounds(scene_with({before}), scene_with({after}));
    };
    const auto none = [](SceneNode&) {};
    const auto plain = edit({}, none);
    expect(plain == PixelRect{110, 90, 155, 120}, "without effects a move damages the member",
           describe(plain));
    const auto blurred = edit({blur(4.0F)}, none);
    expect(blurred == PixelRect{98, 78, 167, 132},
           "inside a blur the damage grows by three sigma", describe(blurred));
    const auto shadowed = edit({shadow(2.0F, 6.0F, -3.0F)}, none);
    expect(shadowed == PixelRect{98, 78, 167, 132},
           "inside a drop shadow the damage grows by its reach and larger offset",
           describe(shadowed));
    const auto matrix = edit({{.kind = EffectKind::color_matrix}}, none);
    expect(matrix == plain, "a colour matrix does not grow damage", describe(matrix));
    NodeEffect lift{.kind = EffectKind::color_matrix};
    lift.matrix[19] = 0.1F;
    const auto lifted = edit({lift}, none);
    expect(lifted == PixelRect{110, 90, 200, 160},
           "a colour matrix that lifts alpha damages its whole area when it changes",
           describe(lifted));
    auto recolour_before = effect_group({lift});
    auto recolour_after = recolour_before;
    recolour_after.children[1].fill = Color::hex(0x22C55E);
    const auto inside = damage_bounds(scene_with({recolour_before}), scene_with({recolour_after}));
    expect(inside == PixelRect{150, 120, 200, 160},
           "a change that keeps a lifted area damages only the member", describe(inside));
    const auto nested = [&] {
        auto before = effect_group({blur(1.0F)});
        before.children[0].effects = {blur(2.0F)};
        auto after = before;
        after.children[0].bounds.x += 5.0F;
        return damage_bounds(scene_with({before}), scene_with({after}));
    }();
    expect(nested == PixelRect{101, 81, 164, 129}, "nested chains sum their radii",
           describe(nested));
    const auto turned = edit({blur(4.0F)}, [](SceneNode& group) {
        group.transform = Affine2D{.a = 2.0F, .d = 2.0F};
    });
    // Under a 2x scale about (100, 80) the member covers (120, 100) to (200,
    // 160) and moves to x = 130; the blur's 12 is in frame pixels.
    expect(turned == PixelRect{108, 88, 222, 172},
           "a transform does not scale an effect's radius", describe(turned));
    const auto inner_clip = edit({blur(4.0F)}, [](SceneNode& group) {
        group.children[0].clip = NodeClip{.rect = {0.0F, 0.0F, 10.0F, 10.0F}};
    });
    expect(inner_clip == blurred,
           "a clip inside the group does not bound the damage the blur spreads",
           describe(inner_clip));
    const auto outer_clip = edit({blur(4.0F)}, [](SceneNode& group) {
        group.clip = NodeClip{.rect = {0.0F, 0.0F, 60.0F, 200.0F}};
    });
    expect(outer_clip == PixelRect{100, 80, 160, 132},
           "the group's own clip bounds the spread damage", describe(outer_clip));
    const auto subtree_clip = edit({blur(4.0F)}, [](SceneNode& group) {
        group.clip = NodeClip{.rect = {0.0F, 0.0F, 60.0F, 200.0F}, .clips_own_paint = false};
    });
    expect(subtree_clip == PixelRect{98, 78, 167, 132},
           "a subtree-only clip sits inside the group, so the blur spreads past it",
           describe(subtree_clip));

    auto sigma_before = effect_group({blur(2.0F)});
    auto sigma_after = effect_group({blur(5.0F)});
    const auto resized = damage_bounds(scene_with({sigma_before}), scene_with({sigma_after}));
    expect(resized == PixelRect{95, 75, 215, 175},
           "a changed sigma damages the whole group at the larger reach", describe(resized));
    auto recoloured = sigma_before;
    recoloured.effects[0].kind = EffectKind::drop_shadow;
    const auto rekinded = damage_bounds(scene_with({sigma_before}), scene_with({recoloured}));
    expect(!rekinded.empty(), "a changed effect kind damages the group", describe(rekinded));
}

void backdrop_damage() {
    // The pane's box is (200, 100) 80x60 and its backdrop samples 15 beyond.
    const auto scene_of = [](float behind_x, NodeEffect effect) {
        SceneNode pane{.id = "pane", .kind = SceneNode::Kind::group,
                       .bounds = {200.0F, 100.0F, 80.0F, 60.0F}};
        pane.effects = {std::move(effect)};
        return scene_with({box("behind", {behind_x, 120.0F, 6.0F, 6.0F}), pane});
    };
    const auto near = damage_bounds(scene_of(184.0F, backdrop(5.0F)),
                                    scene_of(186.0F, backdrop(5.0F)));
    expect(near == PixelRect{184, 100, 280, 160},
           "damage inside a backdrop's sample damages its box", describe(near));
    const auto far = damage_bounds(scene_of(170.0F, backdrop(5.0F)),
                                   scene_of(172.0F, backdrop(5.0F)));
    expect(far == PixelRect{170, 120, 178, 126},
           "damage outside a backdrop's sample leaves its box alone", describe(far));
    const auto material = damage_bounds(
        scene_of(170.0F, glass({{.name = "blur", .value = {8.0F}}})),
        scene_of(172.0F, glass({{.name = "blur", .value = {8.0F}}})));
    expect(material == PixelRect{170, 100, 280, 160},
           "frosted glass samples its blur's reach", describe(material));

    const auto chained = [](float behind_x) {
        SceneNode first{.id = "first", .kind = SceneNode::Kind::group,
                        .bounds = {100.0F, 100.0F, 50.0F, 50.0F}};
        first.effects = {backdrop(2.0F)};
        SceneNode second{.id = "second", .kind = SceneNode::Kind::group,
                         .bounds = {152.0F, 100.0F, 50.0F, 50.0F}};
        second.effects = {backdrop(2.0F)};
        return scene_with({box("behind", {behind_x, 110.0F, 4.0F, 4.0F}), first, second});
    };
    const auto both = damage_bounds(chained(95.0F), chained(96.0F));
    expect(both == PixelRect{95, 100, 202, 150},
           "a damaged backdrop box reaches the next backdrop's sample", describe(both));

    DamageTracker tracker({.max_rects = 64, .full_repaint_fraction = 2.0, .antialias_margin = 0});
    tracker.commit(scene_of(184.0F, backdrop(5.0F)), 1.0F);
    (void)tracker.take_damage();
    tracker.mark_dirty("behind");
    const auto marked = tracker.take_damage().bounds();
    expect(marked == PixelRect{184, 100, 280, 160},
           "a host invalidation inside a backdrop's sample damages its box", describe(marked));
    tracker.commit(scene_of(186.0F, backdrop(5.0F)), 1.0F);
    expect(tracker.take_damage().bounds() == PixelRect{184, 100, 280, 160},
           "the tracker propagates backdrop damage on commit");
}

}  // namespace

int main() {
    version_matrix();
    effect_validation();
    material_registry();
    costs_and_budget();
    lowering();
    damage_radius();
    backdrop_damage();
    std::cout << "effects " << (failures == 0 ? "passed" : "failed") << '\n';
    return failures == 0 ? 0 : 1;
}
