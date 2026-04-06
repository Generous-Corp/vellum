#include <pulp/view/js_engine_recommend.hpp>
#include <algorithm>

namespace pulp::view {

static bool contains(std::string_view haystack, std::string_view needle) {
    return haystack.find(needle) != std::string_view::npos;
}

EngineRecommendation recommend_engine_for_script(
    std::string_view js_source,
    JsEngineType current)
{
    EngineRecommendation rec;
    rec.recommended = JsEngineType::quickjs;
    rec.reason = "Standard UI script — QuickJS is portable and proven";

    // Detect Three.js patterns
    bool has_threejs = contains(js_source, "THREE.Scene")
                    || contains(js_source, "THREE.WebGLRenderer")
                    || contains(js_source, "THREE.PerspectiveCamera")
                    || contains(js_source, "import * as THREE")
                    || contains(js_source, "from 'three'")
                    || contains(js_source, "from \"three\"");

    if (has_threejs) {
        rec.recommended = JsEngineType::v8;
        rec.reason = "Three.js detected — V8's JIT makes ~1MB library parse and complex "
                     "scene graphs viable. QuickJS will work but parse time may exceed 1s.";
        if (!is_engine_available(JsEngineType::v8)) {
            rec.reason += " (V8 not available in this build — rebuild with --js-engine=v8)";
        }
    }

    // Detect large scripts (>500KB suggests a bundled library)
    if (js_source.size() > 512 * 1024 && !has_threejs) {
        rec.recommended = JsEngineType::v8;
        rec.reason = "Large script (" + std::to_string(js_source.size() / 1024)
                   + "KB) — V8's JIT compiler handles large bundles significantly faster than QuickJS.";
        if (!is_engine_available(JsEngineType::v8)) {
            rec.reason += " (V8 not available in this build)";
        }
    }

    rec.upgrade_advised = (rec.recommended != current);
    return rec;
}

EngineRecommendation recommend_engine_for_workload(
    std::string_view workload,
    JsEngineType current)
{
    EngineRecommendation rec;

    if (contains(workload, "three") || contains(workload, "3d") || contains(workload, "webgl")) {
        rec.recommended = JsEngineType::v8;
        rec.reason = "Three.js / 3D workloads benefit from V8's JIT compilation for "
                     "library parse time and scene graph traversal.";
    } else if (contains(workload, "apple") || contains(workload, "ios") || contains(workload, "macos")) {
        rec.recommended = JsEngineType::jsc;
        rec.reason = "Apple-only target — JavaScriptCore is a zero-dependency system framework "
                     "with good JIT performance on macOS/iOS.";
    } else if (contains(workload, "cross") || contains(workload, "portable")) {
        rec.recommended = JsEngineType::quickjs;
        rec.reason = "Cross-platform target — QuickJS works identically on all platforms.";
    } else {
        rec.recommended = JsEngineType::quickjs;
        rec.reason = "Standard workload — QuickJS is the portable, proven default.";
    }

    if (rec.recommended == JsEngineType::v8 && !is_engine_available(JsEngineType::v8)) {
        rec.reason += " (V8 not available — rebuild with --js-engine=v8 and provide V8 libraries)";
    }
    if (rec.recommended == JsEngineType::jsc && !is_engine_available(JsEngineType::jsc)) {
        rec.recommended = JsEngineType::quickjs;
        rec.reason = "JSC not available on this platform — using QuickJS as fallback.";
    }

    rec.upgrade_advised = (rec.recommended != current);
    return rec;
}

} // namespace pulp::view
