// Runs the blobs vellum_add_js_bytecode() embedded: the engine must accept
// them, the bytecode cache must take them instead of compiling, and the
// result must equal the plain evaluation of the same script.

#include <vellum/view/js_engine.hpp>
#include <vellum/view/widget_bridge.hpp>

#include "consumer_bridge_script_bytecode_data.hpp"
#include "consumer_script_bytecode_data.hpp"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>

namespace {

using namespace vellum::view;

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL %s\n", what);
    }
}

double evaluate_fresh(const std::string& code) {
    auto engine = create_js_engine(JsEngineType::quickjs);
    return engine ? engine->evaluate_script(code).getWithDefault<double>(-1) : -1;
}

double reference_result(const std::string& code) {
    auto engine = create_js_engine(JsEngineType::quickjs);
    return engine ? engine->evaluate(code).getWithDefault<double>(-2) : -2;
}

void reset() {
    clear_script_bytecode_cache();
    detail::clear_embedded_script_bytecode_for_tests();
}

}  // namespace

int main() {
    std::ifstream in(VELLUM_TEST_JS_BYTECODE_FIXTURE, std::ios::binary);
    const std::string code((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    check(!code.empty(), "the fixture is readable");

    reset();
    check(register_embedded_script_bytecode(consumer_script_bytecode::bytecode_fixture_js_vjsbc,
                                            consumer_script_bytecode::bytecode_fixture_js_vjsbc_size),
          "the installed engine accepts the generated plain blob");
    check(evaluate_fresh(code) == reference_result(code), "the embedded script runs the same program");
    check(script_bytecode_cache_stats().embedded == 1, "the plain blob replaced a compile");
    check(script_bytecode_cache_stats().compiles == 0, "nothing was compiled for the plain script");

    reset();
    check(register_embedded_script_bytecode(
              consumer_script_bytecode::bytecode_bridge_fixture_js_vjsbc,
              consumer_script_bytecode::bytecode_bridge_fixture_js_vjsbc_size),
          "the installed engine accepts the generated bridge blob");
    evaluate_fresh(WidgetBridge::loaded_script_source(code));
    check(script_bytecode_cache_stats().embedded == 1, "the bridge blob replaced a compile");
    check(script_bytecode_cache_stats().compiles == 0, "nothing was compiled for the bridge script");
    evaluate_fresh(code);
    check(script_bytecode_cache_stats().compiles == 1, "the raw script is not what the bridge blob holds");

    reset();
    std::printf("%s\n", failures == 0 ? "PASS" : "FAILED");
    return failures == 0 ? 0 : 1;
}
