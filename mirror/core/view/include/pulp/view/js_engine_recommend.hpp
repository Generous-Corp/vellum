#pragma once

#include <pulp/view/js_engine.hpp>
#include <string>
#include <string_view>

namespace pulp::view {

// Recommendation result from analyzing a JS script or workload description
struct EngineRecommendation {
    JsEngineType recommended = JsEngineType::quickjs;
    std::string reason;
    bool upgrade_advised = false;  // true if current engine is suboptimal
};

// Analyze JS source code and recommend the best engine.
// Checks for patterns like Three.js imports, large script size, etc.
// `current` is the engine currently in use — if it matches the recommendation,
// upgrade_advised will be false.
EngineRecommendation recommend_engine_for_script(
    std::string_view js_source,
    JsEngineType current = JsEngineType::quickjs);

// Recommend engine for a named workload (e.g., "threejs", "standard-ui", "apple-only")
EngineRecommendation recommend_engine_for_workload(
    std::string_view workload,
    JsEngineType current = JsEngineType::quickjs);

} // namespace pulp::view
