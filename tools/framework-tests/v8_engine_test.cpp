// Proves the view-script unit runs the sealed V8 provider, not a stub: the
// engine is available, reports the runtime version libv8 itself returns and
// the provider identity the build pinned, evaluates JavaScript that only a
// real V8 accepts, and shares the process with the Skia/Dawn provider's own
// ICU (V8's Intl and Skia's text shaping both work, in either order).
//
// The same file is the control. A build without VELLUM_ENABLE_V8 registers
// this test as expected to fail, so it proves the checks below fail when V8
// is not linked. A QuickJS case in the same run proves the identity checks
// tell the engines apart.

#include "framework_test.hpp"

#include <vellum/canvas/text_shaper.hpp>
#include <vellum/view/js_engine.hpp>

#include <cstdio>
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
    // Intl runs on the ICU and locale data sealed inside libv8.
    CHECK(js_string(*engine, "new Intl.NumberFormat('de-DE').format(1234567.891)") == "1.234.567,891");
    CHECK(js_string(*engine, "[...new Intl.Segmenter('en', {granularity: 'word'})"
                             ".segment('hello sealed world')].filter(s => s.isWordLike).length") == "3");
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
    CHECK(js_string(*engine, "'a'.localeCompare('b', 'sv')") == "-1");
    CHECK(js_string(*engine, "new Intl.DateTimeFormat('ja-JP', {timeZone: 'UTC'}).format(0)") == "1970/1/1");
    CHECK(wrapped_lines() == before);
    CHECK(js_string(*engine, "'\\u00df'.toLocaleUpperCase('de')") == "SS");
}

} // namespace

int main() {
    return framework_test::run_all();
}
