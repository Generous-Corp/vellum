#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace vellum::graphics {

/// One feature and the versions that introduced it: the scene schema version
/// and the DesignIR schema version, either of which is zero when the feature
/// has no counterpart in that schema.
struct FeatureVersion final {
    std::string_view name;
    std::uint32_t scene_version;
    std::uint32_t design_ir_version;
};

// `tools/versions/generate_feature_versions.mjs` reads the rows between the
// markers below and writes `feature-versions.generated.js` for
// `@vellum/design-ir` and `@vellum/ui`. A row is
// `FeatureVersion{"name", SCENE, DESIGN_IR},`. Scene versions count up from 2
// without gaps, and so do the nonzero DesignIR versions. The field predicates
// for a row stay in `scene.hpp` and in the DesignIR validator.
inline constexpr std::array kFeatureVersions{
    // vellum-feature-versions-begin
    FeatureVersion{"transform", 2U, 0U},
    FeatureVersion{"path", 3U, 0U},
    FeatureVersion{"image", 4U, 0U},
    FeatureVersion{"grouping", 5U, 0U},
    FeatureVersion{"blend", 6U, 0U},
    FeatureVersion{"tokens", 7U, 0U},
    FeatureVersion{"gradients", 8U, 0U},
    FeatureVersion{"vectors", 9U, 6U},
    FeatureVersion{"effects", 10U, 3U},
    FeatureVersion{"tracks", 11U, 4U},
    FeatureVersion{"plots", 12U, 5U},
    FeatureVersion{"text-layout", 13U, 0U},
    FeatureVersion{"compositing", 0U, 2U},
    // vellum-feature-versions-end
};

[[nodiscard]] constexpr std::uint32_t latest_scene_version() noexcept {
    std::uint32_t latest = 1U;
    for (const auto& feature : kFeatureVersions) {
        if (feature.scene_version > latest) latest = feature.scene_version;
    }
    return latest;
}

[[nodiscard]] constexpr std::uint32_t latest_design_ir_version() noexcept {
    std::uint32_t latest = 1U;
    for (const auto& feature : kFeatureVersions) {
        if (feature.design_ir_version > latest) latest = feature.design_ir_version;
    }
    return latest;
}

}  // namespace vellum::graphics
