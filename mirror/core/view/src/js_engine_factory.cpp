// Engine factory — selects and creates JS engine backends.
// Build-time selection via PULP_JS_ENGINE CMake option.
// Runtime selection via create_js_engine(JsEngineType).

#include <pulp/view/js_engine.hpp>
#include <stdexcept>

namespace pulp::view {

// Forward declarations — each backend provides its own create function
std::unique_ptr<JsEngine> create_quickjs_engine();
std::unique_ptr<JsEngine> create_v8_engine();

#if __APPLE__
std::unique_ptr<JsEngine> create_jsc_engine();
#endif

bool is_engine_available(JsEngineType type) {
    switch (type) {
        case JsEngineType::quickjs:
            return true;  // Always available

        case JsEngineType::jsc:
#if __APPLE__
            return true;
#else
            return false;
#endif

        case JsEngineType::v8:
#ifdef PULP_HAS_V8
            return true;
#else
            return false;
#endif
    }
    return false;
}

std::unique_ptr<JsEngine> create_js_engine(JsEngineType type) {
    switch (type) {
        case JsEngineType::quickjs:
            return create_quickjs_engine();

        case JsEngineType::jsc:
#if __APPLE__
            return create_jsc_engine();
#else
            throw std::runtime_error("JavaScriptCore is only available on Apple platforms");
#endif

        case JsEngineType::v8:
            return create_v8_engine();  // Throws if PULP_HAS_V8 not defined
    }
    throw std::runtime_error("Unknown JS engine type");
}

std::unique_ptr<JsEngine> create_default_js_engine() {
    // Default: QuickJS for backward compatibility and portability.
    // QuickJS is the battle-tested default — all existing code (WidgetBridge,
    // web-compat preludes, tests) was written and tested against it.
    //
    // To use JSC or V8 as the default, set PULP_DEFAULT_ENGINE_JSC or
    // PULP_DEFAULT_ENGINE_V8 at build time, or pass the engine type explicitly
    // to ScriptEngine(JsEngineType::jsc) or create_js_engine(JsEngineType::v8).
#if defined(PULP_DEFAULT_ENGINE_V8) && defined(PULP_HAS_V8)
    return create_v8_engine();
#elif defined(PULP_DEFAULT_ENGINE_JSC) && __APPLE__
    return create_jsc_engine();
#else
    return create_quickjs_engine();
#endif
}

} // namespace pulp::view
