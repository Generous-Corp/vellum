// Build-time (embedded) script bytecode: the blobs vellum_add_js_bytecode()
// embeds, their registration, and the bytecode cache taking them instead of
// compiling.

#include "framework_test.hpp"

#include <vellum/view/js_engine.hpp>
#include <vellum/view/widget_bridge.hpp>

#include "js_bytecode_embed_bridge_fixture_data.hpp"
#include "js_bytecode_embed_fixture_data.hpp"

#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

using namespace vellum::view;

std::string read_fixture() {
    std::ifstream in(VELLUM_TEST_JS_BYTECODE_FIXTURE, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>()};
}

// Fresh state for every case: nothing cached, nothing registered.
struct Reset {
    Reset() {
        clear_script_bytecode_cache();
        detail::clear_embedded_script_bytecode_for_tests();
    }
    ~Reset() {
        clear_script_bytecode_cache();
        detail::clear_embedded_script_bytecode_for_tests();
    }
};

double evaluate_fresh(const std::string& code) {
    auto engine = create_js_engine(JsEngineType::quickjs);
    if (!engine)
        return -1;
    return engine->evaluate_script(code).getWithDefault<double>(-1);
}

double reference_result(const std::string& code) {
    auto engine = create_js_engine(JsEngineType::quickjs);
    if (!engine)
        return -2;
    return engine->evaluate(code).getWithDefault<double>(-2);
}

// Offset of the bytecode payload: everything after the header.
std::size_t payload_offset(const std::vector<std::uint8_t>& blob) {
    const std::size_t fingerprint =
        blob[12] | (blob[13] << 8) | (blob[14] << 16) | (std::size_t(blob[15]) << 24);
    return 16 + fingerprint + 8 + 32 + 8 + 32;
}

} // namespace

FRAMEWORK_TEST(embedded_bytecode_replaces_the_compile_and_runs_the_same_program) {
    Reset reset;
    const auto code = read_fixture();
    const auto expected = reference_result(code);
    CHECK(expected > 0);

    const auto blob = build_embedded_script_bytecode(code);
    CHECK(!blob.empty());
    CHECK(register_embedded_script_bytecode(blob.data(), blob.size()));

    CHECK(evaluate_fresh(code) == expected);
    auto stats = script_bytecode_cache_stats();
    CHECK(stats.embedded == 1);
    CHECK(stats.compiles == 0);

    // A later realm reads the now-cached bytecode as an ordinary hit.
    CHECK(evaluate_fresh(code) == expected);
    stats = script_bytecode_cache_stats();
    CHECK(stats.hits == 1);
    CHECK(stats.compiles == 0);
}

FRAMEWORK_TEST(embedded_bytecode_serves_the_instantiation_precompile) {
    Reset reset;
    const auto code = read_fixture();
    const auto blob = build_embedded_script_bytecode(code);
    CHECK(register_embedded_script_bytecode(blob.data(), blob.size()));

    CHECK(precompile_scripts({code}) == 0);  // nothing compiled...
    CHECK(script_bytecode_cached(code));     // ...yet the editor will hit
    auto stats = script_bytecode_cache_stats();
    CHECK(stats.embedded == 1);
    CHECK(stats.compiles == 0);
    CHECK(evaluate_fresh(code) == reference_result(code));
    CHECK(script_bytecode_cache_stats().compiles == 0);
}

FRAMEWORK_TEST(embedded_bytecode_is_never_used_for_a_different_source) {
    Reset reset;
    const auto code = read_fixture();
    const auto blob = build_embedded_script_bytecode(code);
    CHECK(register_embedded_script_bytecode(blob.data(), blob.size()));

    // Same length, one character different: the length filter passes, the
    // SHA-256 must not.
    auto edited = code;
    const auto at = edited.find("(i * 5 + 2)");
    CHECK(at != std::string::npos);
    if (at == std::string::npos)
        return;
    edited[at + 5] = '6';
    CHECK(edited.size() == code.size());

    CHECK(evaluate_fresh(edited) == reference_result(edited));
    CHECK(reference_result(edited) != reference_result(code));
    const auto stats = script_bytecode_cache_stats();
    CHECK(stats.embedded == 0);
    CHECK(stats.compiles == 1);
}

FRAMEWORK_TEST(malformed_or_foreign_embedded_bytecode_is_refused) {
    Reset reset;
    const auto code = read_fixture();
    const auto blob = build_embedded_script_bytecode(code);
    CHECK(blob.size() > 100);
    if (blob.size() <= 100)
        return;

    CHECK(!register_embedded_script_bytecode(blob.data(), blob.size() - 1));  // truncated

    auto padded = blob;
    padded.push_back(0);
    CHECK(!register_embedded_script_bytecode(padded.data(), padded.size()));

    auto bad_magic = blob;
    bad_magic[0] = 'X';
    CHECK(!register_embedded_script_bytecode(bad_magic.data(), bad_magic.size()));

    auto unknown_format = blob;
    unknown_format[8] = 99;
    CHECK(!register_embedded_script_bytecode(unknown_format.data(), unknown_format.size()));

    auto foreign = blob;
    foreign[16 + 2] ^= 0x01;  // inside the fingerprint string
    CHECK(!register_embedded_script_bytecode(foreign.data(), foreign.size()));

    // What a cross-compiled build embeds in place of a blob.
    const std::uint8_t placeholder[] = {'-'};
    CHECK(!register_embedded_script_bytecode(placeholder, sizeof placeholder));
    CHECK(!register_embedded_script_bytecode(nullptr, 0));

    // Nothing was registered: the script compiles from source as before.
    CHECK(evaluate_fresh(code) == reference_result(code));
    CHECK(script_bytecode_cache_stats().embedded == 0);
}

FRAMEWORK_TEST(embedded_bytecode_that_fails_its_integrity_check_compiles_from_source) {
    Reset reset;
    const auto code = read_fixture();
    auto blob = build_embedded_script_bytecode(code);
    CHECK(!blob.empty());
    if (blob.empty())
        return;
    // A well-formed header over a damaged payload registers (the header is
    // checked eagerly) but is never deserialized.
    blob[payload_offset(blob) + 4] ^= 0xFF;
    CHECK(register_embedded_script_bytecode(blob.data(), blob.size()));

    CHECK(evaluate_fresh(code) == reference_result(code));
    const auto stats = script_bytecode_cache_stats();
    CHECK(stats.embedded == 0);
    CHECK(stats.compiles == 1);
}

FRAMEWORK_TEST(the_engine_bytecode_fingerprint_is_stable_and_names_the_engine) {
    const auto fingerprint = script_bytecode_engine_fingerprint();
    CHECK(fingerprint.rfind("quickjs-", 0) == 0);
    CHECK(fingerprint.find("unavailable") == std::string::npos);
    CHECK(fingerprint == script_bytecode_engine_fingerprint());
}

FRAMEWORK_TEST(generated_plain_script_blob_is_accepted_by_this_engine) {
    Reset reset;
    const auto code = read_fixture();
    using namespace js_bytecode_embed_fixture;
    CHECK(register_embedded_script_bytecode(bytecode_fixture_js_vjsbc,
                                            bytecode_fixture_js_vjsbc_size));
    CHECK(evaluate_fresh(code) == reference_result(code));
    CHECK(script_bytecode_cache_stats().embedded == 1);
    CHECK(script_bytecode_cache_stats().compiles == 0);
}

FRAMEWORK_TEST(generated_widget_bridge_blob_matches_what_load_script_evaluates) {
    Reset reset;
    const auto code = read_fixture();
    using namespace js_bytecode_embed_fixture;
    CHECK(register_embedded_script_bytecode(bytecode_bridge_fixture_js_vjsbc,
                                            bytecode_bridge_fixture_js_vjsbc_size));
    evaluate_fresh(WidgetBridge::loaded_script_source(code));
    CHECK(script_bytecode_cache_stats().embedded == 1);
    CHECK(script_bytecode_cache_stats().compiles == 0);
    // Control: the raw script is not what that blob was compiled from.
    evaluate_fresh(code);
    CHECK(script_bytecode_cache_stats().compiles == 1);
}

int main() { return framework_test::run_all(); }
