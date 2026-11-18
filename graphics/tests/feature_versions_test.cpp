#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/feature_versions.hpp>
#include <vellum/graphics/scene.hpp>

#include <cstdint>
#include <cstdio>
#include <functional>
#include <string_view>

namespace {

using namespace vellum::graphics;

int failures = 0;

void check(bool ok, const char* what, std::string_view feature = {}) {
    if (!ok) {
        std::fprintf(stderr, "FAILED: %s %.*s\n", what, static_cast<int>(feature.size()),
                     feature.data());
        ++failures;
    }
}

using Builder = std::function<void(SceneNode&)>;

Builder builder_for(std::string_view feature) {
    if (feature == "transform") return [](SceneNode& node) { node.opacity = 0.5F; };
    if (feature == "path") return [](SceneNode& node) { node.kind = SceneNode::Kind::path; };
    if (feature == "image") return [](SceneNode& node) { node.kind = SceneNode::Kind::image; };
    if (feature == "grouping") return [](SceneNode& node) { node.isolate = true; };
    if (feature == "blend") return [](SceneNode& node) { node.blend_mode = BlendMode::multiply; };
    if (feature == "tokens") return [](SceneNode& node) { node.fill_token = 0U; };
    if (feature == "gradients") {
        return [](SceneNode& node) { node.stroke_gradient = LinearGradient{}; };
    }
    if (feature == "vectors") return [](SceneNode& node) { node.kind = SceneNode::Kind::vector; };
    if (feature == "effects") return [](SceneNode& node) { node.effects.emplace_back(); };
    if (feature == "tracks") return [](SceneNode& node) { node.tracks.emplace_back(); };
    if (feature == "plots") return [](SceneNode& node) { node.kind = SceneNode::Kind::plot; };
    if (feature == "text-layout") {
        return [](SceneNode& node) { node.text_layout = TextLayout{}; };
    }
    return {};
}

Scene scene_with(const Builder& build, std::uint32_t version) {
    Scene scene;
    scene.scene_version = version;
    scene.root.id = "root";
    SceneNode child;
    child.id = "child";
    build(child);
    scene.root.children.push_back(std::move(child));
    return scene;
}

void check_table_shape() {
    std::uint32_t scene_count = 0;
    for (const auto& feature : kFeatureVersions) {
        if (feature.scene_version != 0U) ++scene_count;
    }
    check(scene_count == latest_scene_version() - 1U,
          "scene versions count up from 2 without gaps");
    for (std::uint32_t version = 2U; version <= latest_scene_version(); ++version) {
        std::uint32_t rows = 0;
        for (const auto& feature : kFeatureVersions) {
            if (feature.scene_version == version) ++rows;
        }
        check(rows == 1U, "each scene version has one row");
    }
    for (std::uint32_t version = 2U; version <= latest_design_ir_version(); ++version) {
        std::uint32_t rows = 0;
        for (const auto& feature : kFeatureVersions) {
            if (feature.design_ir_version == version) ++rows;
        }
        check(rows == 1U, "each DesignIR version has one row");
    }
    check(kSceneVersion == latest_scene_version(), "kSceneVersion is the table's newest");
    check(kDamageModelSceneVersion <= kSceneVersion, "the damage model is not ahead of the scene");
    check(scene_version_is_supported(kSceneVersion) &&
              !scene_version_is_supported(kSceneVersion + 1U),
          "the newest table version is the newest supported one");
}

void check_predicates() {
    for (const auto& feature : kFeatureVersions) {
        if (feature.scene_version == 0U) continue;
        const Builder build = builder_for(feature.name);
        check(static_cast<bool>(build), "the table row has no field builder in this test",
              feature.name);
        if (!build) continue;

        const Scene early = scene_with(build, feature.scene_version - 1U);
        const auto mismatch = find_scene_version_mismatch(early);
        check(mismatch.has_value() && mismatch->introduced == feature.scene_version,
              "no predicate refuses the field one version early", feature.name);
        check(!scene_fields_match_version(early), "scene_fields_match_version accepts it early",
              feature.name);

        const Scene current = scene_with(build, feature.scene_version);
        check(scene_fields_match_version(current), "the field is refused at its own version",
              feature.name);
    }
}

}  // namespace

int main() {
    check_table_shape();
    check_predicates();
    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::puts("feature versions ok");
    return 0;
}
