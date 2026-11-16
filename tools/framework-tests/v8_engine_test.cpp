// Proves the view-script unit runs the sealed V8 provider, not a stub: the
// engine is available, reports the runtime version libv8 itself returns and
// the provider identity the build pinned, evaluates JavaScript that only a
// real V8 accepts, and shares the process with the Skia/Dawn provider's own
// ICU (V8's Unicode handling and Skia's text shaping both work, in either
// order). The pinned provider ships no ICU locale data, so Intl formatting
// and collation are not exercised here.
//
// The same file is the control. A build without VELLUM_ENABLE_V8 registers
// this test as expected to fail, so it proves the checks below fail when V8
// is not linked. A QuickJS case in the same run proves the identity checks
// tell the engines apart.

#include "framework_test.hpp"

#include <vellum/canvas/text_shaper.hpp>
#include <vellum/view/js_engine.hpp>

#include <cstdio>
#include <optional>
#include <string_view>
#include <memory>
#include <string>

#ifndef VELLUM_TEST_EXPECTED_V8_VERSION
#define VELLUM_TEST_EXPECTED_V8_VERSION ""
#endif

namespace {

namespace view = vellum::view;

const std::string kExpectedVersion = VELLUM_TEST_EXPECTED_V8_VERSION;

std::string js_string(view::JsEngine& engine, const std::string& expression) {
    return engine.evaluate("String(" + expression + ")").toString();
}

int wrapped_lines() {
    vellum::canvas::TextShaper shaper;
    const auto prepared = shaper.prepare("Vellum sealed provider coexistence", "Inter", 16.0f);
    if (!shaper.uses_real_shaping())
        return -1;
    return shaper.count_lines(prepared, 60.0f);
}

FRAMEWORK_TEST(the_build_pins_a_v8_version) {
    CHECK(!kExpectedVersion.empty());
}

FRAMEWORK_TEST(v8_is_available) {
    CHECK(view::is_engine_available(view::JsEngineType::v8));
}

FRAMEWORK_TEST(v8_reports_the_sealed_provider) {
    auto engine = view::create_js_engine(view::JsEngineType::v8);
    CHECK(engine != nullptr);
    if (!engine)
        return;
    std::printf("VELLUM_ENGINE_IDENTITY_BEGIN\n"
                "engine_type=%s\nruntime_version=%s\nexpected_runtime_version=%s\n"
                "provider_kind=%s\nprovider_path=%s\n"
                "VELLUM_ENGINE_IDENTITY_END\n",
                std::string(view::engine_type_name(engine->type())).c_str(),
                engine->runtime_version().c_str(), engine->expected_runtime_version().c_str(),
                engine->provider_kind().c_str(), engine->provider_path().c_str());
    CHECK(engine->type() == view::JsEngineType::v8);
    CHECK(engine->is_valid());
    // The version libv8 reports at run time, against the lock's pin.
    CHECK(!kExpectedVersion.empty() && engine->runtime_version().rfind(kExpectedVersion, 0) == 0);
    CHECK(engine->expected_runtime_version() == kExpectedVersion);
    CHECK(engine->provider_kind() == "v8builder");
    CHECK(engine->provider_path() == "@rpath/libv8.dylib");
}

FRAMEWORK_TEST(v8_evaluates_javascript) {
    auto engine = view::create_js_engine(view::JsEngineType::v8);
    CHECK(js_string(*engine, "new Float64Array([1, 2, 3]).reduce((a, b) => a + b, 0) * 7") == "42");
    CHECK(js_string(*engine, "(2n ** 64n).toString()") == "18446744073709551616");
    // WebAssembly is a V8 global that QuickJS does not have.
    CHECK(js_string(*engine, "typeof WebAssembly") == "object");
    // Unicode property escapes, case mapping and normalization run on the
    // ICU code sealed inside libv8.
    CHECK(js_string(*engine, "/\\p{Script=Greek}/u.test('\\u03b1')") == "true");
    CHECK(js_string(*engine, "'\\u00df'.toUpperCase()") == "SS");
    CHECK(js_string(*engine, "'A\\u030a'.normalize('NFC').length") == "1");
}

FRAMEWORK_TEST(quickjs_is_not_reported_as_v8) {
    auto engine = view::create_js_engine(view::JsEngineType::quickjs);
    CHECK(engine->type() == view::JsEngineType::quickjs);
    CHECK(engine->runtime_version().empty());
    CHECK(engine->provider_kind().empty());
    CHECK(js_string(*engine, "typeof WebAssembly") == "undefined");
}

FRAMEWORK_TEST(v8_and_skia_icu_share_the_process) {
    const int before = wrapped_lines();
    CHECK(before >= 2);
    auto engine = view::create_js_engine(view::JsEngineType::v8);
    CHECK(js_string(*engine, "/\\p{Lu}/u.test('\\u00c5') && 'e\\u0301'.normalize('NFC') === '\\u00e9'") == "true");
    CHECK(wrapped_lines() == before);
    CHECK(js_string(*engine, "'\\u0130'.toLowerCase().length") == "2");
}

// An import the resolver cannot satisfy reports a JS error through the
// completion handler; the process keeps running.
FRAMEWORK_TEST(v8_missing_import_reports_an_error_instead_of_crashing) {
    auto engine = view::create_js_engine(view::JsEngineType::v8);
    CHECK(engine != nullptr);
    if (!engine)
        return;
    int completions = 0;
    std::string error;
    engine->run_module("import { value } from './missing.js';\nglobalThis.imported = value;",
                       [](std::string_view) -> std::optional<std::string> { return std::nullopt; },
                       [&](const std::string& message, const choc::value::Value&) {
                           ++completions;
                           error = message;
                       });
    CHECK(completions == 1);
    CHECK(error.find("Cannot find module './missing.js'") != std::string::npos);
    // The engine is still usable after the failed import.
    CHECK(js_string(*engine, "1 + 1") == "2");
}

// Positive control: a resolvable relative import evaluates and its export
// reaches the importing module.
FRAMEWORK_TEST(v8_resolved_import_still_works) {
    auto engine = view::create_js_engine(view::JsEngineType::v8);
    CHECK(engine != nullptr);
    if (!engine)
        return;
    int completions = 0;
    std::string error = "unset";
    engine->run_module("import { value } from './dep.js';\nglobalThis.imported = value * 2;",
                       [](std::string_view path) -> std::optional<std::string> {
                           if (path == "./dep.js")
                               return std::string("export const value = 21;");
                           return std::nullopt;
                       },
                       [&](const std::string& message, const choc::value::Value&) {
                           ++completions;
                           error = message;
                       });
    CHECK(completions == 1);
    CHECK(error.empty());
    CHECK(js_string(*engine, "globalThis.imported") == "42");
}

} // namespace

int main() {
    return framework_test::run_all();
}
