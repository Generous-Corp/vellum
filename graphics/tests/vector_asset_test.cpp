#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/layer_cache.hpp>
#include <vellum/graphics/lowering_diagnostic.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/scene_acceptance.hpp>
#include <vellum/graphics/vector_asset.hpp>

#include <cmath>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
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

const std::string kHash = "sha256:" + std::string(64, 'c');

std::shared_ptr<const PathGeometry> path(const std::string& data, float stroke_width = 0.0F) {
    PathGeometry geometry;
    if (!parse_svg_path_data(data, geometry)) return nullptr;
    geometry.stroke.width = stroke_width;
    return std::make_shared<const PathGeometry>(std::move(geometry));
}

LinearGradient red_to_blue() {
    LinearGradient gradient;
    gradient.angle_degrees = 90.0F;
    gradient.stops = {{.position = 0.0F, .color = Color::hex(0xFF0000)},
                      {.position = 1.0F, .color = Color::hex(0x0000FF)}};
    return gradient;
}

VectorAsset sample() {
    VectorAsset asset{.content_hash = kHash, .view_box = {0.0F, 0.0F, 100.0F, 50.0F}};
    asset.ops.push_back({.kind = VectorOp::Kind::push_group,
                         .transform = translation(10.0F, 5.0F),
                         .opacity = 0.5F,
                         .clip = path("M0 0H40V40H0Z")});
    asset.ops.push_back({.kind = VectorOp::Kind::path,
                         .path = path("M0 0H20V10H0Z", 2.0F),
                         .fill = {.color = Color::hex(0x00FF00)},
                         .stroke = {.color = Color::hex(0x000000)}});
    asset.ops.push_back({.kind = VectorOp::Kind::pop_group});
    asset.ops.push_back({.kind = VectorOp::Kind::path,
                         .transform = translation(60.0F, 20.0F),
                         .path = path("M0 0H30V20H0Z"),
                         .fill = {.gradient = red_to_blue(),
                                  .gradient_transform = {.a = 30.0F, .d = 20.0F}}});
    return asset;
}

void validation() {
    std::string reason;
    expect(vector_asset_is_valid(sample(), &reason), "a well-formed asset is valid", reason);
    const std::vector<std::pair<std::string, std::function<void(VectorAsset&)>>> broken{
        {"a content hash in the sha256 form", [](VectorAsset& a) { a.content_hash = "md5:00"; }},
        {"a positive view box", [](VectorAsset& a) { a.view_box.width = 0.0F; }},
        {"a finite view box", [](VectorAsset& a) { a.view_box.x = NAN; }},
        {"balanced groups", [](VectorAsset& a) { a.ops.pop_back(); a.ops.erase(a.ops.begin() + 2); }},
        {"no stray group end", [](VectorAsset& a) {
             a.ops.insert(a.ops.begin(), {.kind = VectorOp::Kind::pop_group});
         }},
        {"a group opacity within [0, 1]", [](VectorAsset& a) { a.ops[0].opacity = 1.5F; }},
        {"no paint on a group", [](VectorAsset& a) { a.ops[0].fill.color = Color::hex(0x111111); }},
        {"a finite transform", [](VectorAsset& a) { a.ops[1].transform.tx = INFINITY; }},
        {"geometry on every path", [](VectorAsset& a) { a.ops[1].path = nullptr; }},
        {"stroke paint on the operation, not the geometry", [](VectorAsset& a) {
             auto geometry = std::make_shared<PathGeometry>(*a.ops[1].path);
             geometry->stroke.color = Color::hex(0xFFFFFF);
             a.ops[1].path = geometry;
         }},
        {"one of colour or gradient", [](VectorAsset& a) {
             a.ops[3].fill.color = Color::hex(0x123456);
         }},
        {"ordered gradient stops", [](VectorAsset& a) {
             a.ops[3].fill.gradient->stops[0].position = 0.75F;
             a.ops[3].fill.gradient->stops[1].position = 0.25F;
         }},
        {"no repeat length", [](VectorAsset& a) { a.ops[3].fill.gradient->repeat_length = 4.0F; }},
        {"an expressible gradient", [](VectorAsset& a) {
             a.ops[3].fill.gradient->color_space = "oklab";
         }},
        {"an invertible gradient transform", [](VectorAsset& a) {
             a.ops[3].fill.gradient_transform = {.a = 0.0F, .d = 0.0F};
         }},
        {"no clip on a path", [](VectorAsset& a) { a.ops[3].clip = path("M0 0H1V1Z"); }},
        {"no opacity on a path", [](VectorAsset& a) { a.ops[3].opacity = 0.5F; }},
        {"a bounded operation count", [](VectorAsset& a) {
             a.ops.resize(kMaxVectorOps + 1U, a.ops[3]);
         }},
        {"a bounded group depth", [](VectorAsset& a) {
             a.ops.clear();
             for (std::size_t i = 0; i <= kMaxVectorGroupDepth; ++i) {
                 a.ops.push_back({.kind = VectorOp::Kind::push_group});
             }
             for (std::size_t i = 0; i <= kMaxVectorGroupDepth; ++i) {
                 a.ops.push_back({.kind = VectorOp::Kind::pop_group});
             }
         }},
    };
    for (const auto& [rule, breaks] : broken) {
        auto asset = sample();
        breaks(asset);
        std::string why;
        expect(!vector_asset_is_valid(asset, &why) && !why.empty(),
               "validation requires " + rule, why);
    }
    auto equal = sample();
    equal.ops[3].fill.gradient->stops[1].position = 0.0F;
    expect(vector_asset_is_valid(equal), "coincident gradient stops are valid, as in SVG");
}

bool near(const Rect& a, const Rect& b) {
    return std::abs(a.x - b.x) < 1e-4F && std::abs(a.y - b.y) < 1e-4F &&
           std::abs(a.width - b.width) < 1e-4F && std::abs(a.height - b.height) < 1e-4F;
}

void geometry() {
    const auto box = vector_asset_paint_box(sample());
    expect(box && near(*box, {6.0F, 1.0F, 84.0F, 39.0F}),
           "the paint box unites transformed paths with their stroke outset",
           box ? std::to_string(box->x) + "," + std::to_string(box->y) + " " +
                     std::to_string(box->width) + "x" + std::to_string(box->height)
               : "empty");
    const auto mapped = vector_paint_bounds(sample(), {100.0F, 100.0F, 200.0F, 100.0F});
    expect(mapped && near(*mapped, {112.0F, 102.0F, 168.0F, 78.0F}),
           "the paint bounds stretch the view box onto the node's box");
    VectorAsset empty{.content_hash = kHash, .view_box = {0.0F, 0.0F, 1.0F, 1.0F}};
    expect(!vector_asset_paint_box(empty), "an asset that paints nothing has no paint box");
    const auto count = vector_paint_count(sample());
    expect(count.paints == 3U && count.grouped && count.gradients,
           "the paint count sees three paints, a group and a gradient");
}

Scene vector_scene(std::uint32_t version, std::shared_ptr<const VectorAsset> asset) {
    Scene scene;
    scene.width = 300.0F;
    scene.height = 200.0F;
    scene.scene_version = version;
    scene.root.id = "root";
    scene.root.bounds = {0.0F, 0.0F, 300.0F, 200.0F};
    scene.root.children.push_back({.id = "backdrop", .kind = SceneNode::Kind::rectangle,
                                   .bounds = {0.0F, 0.0F, 300.0F, 200.0F},
                                   .fill = Color::hex(0x202020)});
    SceneNode vector{.id = "icon", .kind = SceneNode::Kind::vector,
                     .bounds = {100.0F, 100.0F, 200.0F, 100.0F}};
    vector.vector = std::move(asset);
    scene.root.children.push_back(std::move(vector));
    return scene;
}

Scene legacy_scene(std::uint32_t version) {
    Scene scene;
    scene.width = 300.0F;
    scene.height = 200.0F;
    scene.scene_version = version;
    scene.root.id = "root";
    SceneNode shape{.id = "shape", .kind = SceneNode::Kind::path,
                    .bounds = {10.0F, 10.0F, 50.0F, 50.0F}, .fill = Color::hex(0xFF0000)};
    shape.path = path("M0 0H50V50Z");
    shape.clip = NodeClip{.rect = {0.0F, 0.0F, 40.0F, 40.0F}};
    LinearGradient radial = red_to_blue();
    radial.kind = GradientKind::radial;
    radial.angle_degrees = 180.0F;
    shape.fill_gradients = {radial};
    scene.root.children.push_back(std::move(shape));
    return scene;
}

void versioning() {
    static_assert(kSceneVersion >= 9U && kDamageModelSceneVersion >= 9U);
    const auto asset = std::make_shared<const VectorAsset>(sample());
    const auto current = accept_scene(vector_scene(9U, asset));
    expect(current.accepted, "a version 9 scene with a compiled vector asset is accepted",
           current.reason);
    const auto older = vector_scene(8U, asset);
    const auto mismatch = find_scene_version_mismatch(older);
    expect(mismatch && mismatch->node->id == "icon" && mismatch->introduced == 9U &&
               mismatch->property == "vector",
           "the shared version mismatch identifies the vector field introduced in version 9");
    expect(!scene_fields_match_version(older) && find_version_mismatch(older) != nullptr &&
               find_version_mismatch(older)->id == "icon",
           "a version 8 scene cannot carry the vector node");
    const auto refused = accept_scene(older);
    expect(!refused.accepted && refused.diagnostics.size() == 1U &&
               refused.diagnostics[0].code == kSceneVersionMismatchCode &&
               refused.diagnostics[0].property == "vector" &&
               refused.diagnostics[0].node_id == "icon" &&
               refused.reason == "scene version 8 cannot carry the compiled vector asset at icon",
           "the version 8 refusal names the vector field and node", refused.reason);
    expect(make_paint_commands(older).empty(), "a refused older scene lowers to no commands");
    auto stray = legacy_scene(8U);
    stray.root.children[0].vector = asset;
    expect(!accept_scene(stray).accepted, "a version 8 scene cannot carry an asset on a path");
    for (const auto version : {8U, 9U}) {
        const auto legacy = accept_scene(legacy_scene(version));
        expect(legacy.accepted,
               "a version " + std::to_string(version) +
                   " scene of a clipped path with a radial gradient stays legal",
               legacy.reason);
    }
    auto plain = legacy_scene(1U);
    plain.root.children[0] = {.id = "box", .kind = SceneNode::Kind::rectangle,
                              .bounds = {1.0F, 1.0F, 5.0F, 5.0F}};
    expect(accept_scene(plain).accepted, "a version 1 scene of a rectangle stays legal");
}

void acceptance() {
    const auto asset = std::make_shared<const VectorAsset>(sample());
    auto themed = vector_scene(9U, asset);
    themed.tokens.push_back({"main.color.backdrop", "primitive", themed.root.children[0].fill});
    themed.root.children[0].fill_token = 0U;
    expect(accept_scene(themed).accepted, "a valid token receipt and vector asset coexist");
    themed.root.children[0].fill_token = 1U;
    themed.root.children[1].vector.reset();
    const auto token_refused = accept_scene(themed);
    expect(!token_refused.accepted && token_refused.diagnostics.size() == 1U &&
               token_refused.diagnostics[0].code == kInvalidTokenReferenceCode &&
               token_refused.diagnostics[0].node_id == "backdrop",
           "token validation still precedes vector validation in the same scene");
    themed.root.children[0].fill_token = 0U;
    const auto vector_refused = accept_scene(themed);
    expect(!vector_refused.accepted && vector_refused.diagnostics.size() == 1U &&
               vector_refused.diagnostics[0].code == kInvalidVectorAssetCode &&
               vector_refused.diagnostics[0].node_id == "icon",
           "repairing the token exposes the vector refusal in the same scene");
    auto missing = vector_scene(9U, nullptr);
    auto refused = accept_scene(missing);
    expect(!refused.accepted && refused.diagnostics.size() == 1U &&
               refused.diagnostics[0].code == kInvalidVectorAssetCode &&
               refused.diagnostics[0].node_id == "icon",
           "a vector node without an asset is refused", refused.reason);
    auto parented = vector_scene(9U, asset);
    parented.root.children[1].children.push_back({.id = "child"});
    expect(!accept_scene(parented).accepted, "a vector node with children is refused");
    auto stray = vector_scene(9U, asset);
    stray.root.children[0].vector = asset;
    expect(!accept_scene(stray).accepted, "an asset on a non-vector node is refused");
    auto bad = sample();
    bad.ops.pop_back();
    bad.ops.push_back({.kind = VectorOp::Kind::push_group});
    refused = accept_scene(vector_scene(9U, std::make_shared<const VectorAsset>(bad)));
    expect(!refused.accepted && refused.reason.find("leaves a group open") != std::string::npos,
           "an invalid asset is refused with its reason", refused.reason);
}

void commands() {
    const auto asset = std::make_shared<const VectorAsset>(sample());
    auto scene = vector_scene(9U, asset);
    scene.root.children[1].opacity = 0.5F;
    const auto output = make_paint_commands(scene);
    expect(output.size() == 2U && output[1].kind == PaintCommand::Kind::vector &&
               output[1].node_id == "icon" && output[1].vector == asset &&
               output[1].opacity == 0.5F,
           "a vector node lowers to one vector command that takes the node's opacity");
    const auto extent = paint_extent(output[1]);
    expect(extent.bounded && near(extent.rect, {112.0F, 102.0F, 168.0F, 78.0F}),
           "a vector command's extent is its asset's mapped paint box");
    expect(to_string(PaintCommand::Kind::vector) == "vector" &&
               draws_content(PaintCommand::Kind::vector),
           "the vector command draws content");
}

void damage() {
    const auto asset = std::make_shared<const VectorAsset>(sample());
    const auto before = vector_scene(9U, asset);
    const auto same = compute_scene_damage(before, before, 1.0F);
    expect(same.empty(), "an unchanged vector asset damages nothing");
    auto replaced = before;
    replaced.root.children[1].vector = std::make_shared<const VectorAsset>(sample());
    const auto changed = compute_scene_damage(before, replaced, 1.0F);
    const auto bounds = changed.bounds();
    expect(!changed.empty() && bounds.left >= 105 && bounds.top >= 95 && bounds.right <= 290 &&
               bounds.bottom <= 185,
           "a replaced asset damages only its paint box",
           std::to_string(bounds.left) + "," + std::to_string(bounds.top) + " " +
               std::to_string(bounds.right) + "," + std::to_string(bounds.bottom));
    auto moved = before;
    moved.root.children[1].bounds.x -= 50.0F;
    const auto shifted = compute_scene_damage(before, moved, 1.0F).bounds();
    expect(shifted.left <= 70 && shifted.right >= 278, "a moved vector damages old and new boxes");
    auto commands_before = make_paint_commands(before);
    auto commands_after = make_paint_commands(replaced);
    expect(classify_change(commands_before, commands_after) == LayerInvalidation::resource,
           "a replaced asset invalidates a cached layer as a resource change");
}

}  // namespace

int main() {
    validation();
    geometry();
    versioning();
    acceptance();
    commands();
    damage();
    std::cout << (failures == 0 ? "vector asset tests passed\n" : "vector asset tests failed\n");
    return failures == 0 ? 0 : 1;
}
