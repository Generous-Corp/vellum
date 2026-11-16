// Installed-package consumer of the view-script unit built with V8
// (VELLUM_ENABLE_V8). It links only what the install carries and must find
// libv8.dylib beside the Skia/Dawn provider through the installed rpaths.
// argv[1] is the V8 version the lock pins.

#include <vellum/view/js_engine.hpp>

#include <cstdio>
#include <exception>
#include <string>

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: vellum-v8-consumer <expected V8 version>\n");
        return 2;
    }
    const std::string expected = argv[1];
    namespace view = vellum::view;
    try {
        if (!view::is_engine_available(view::JsEngineType::v8)) {
            std::fprintf(stderr, "v8 consumer: the installed view-script unit has no V8\n");
            return 1;
        }
        auto engine = view::create_js_engine(view::JsEngineType::v8);
        const std::string answer = engine->evaluate("String([1, 2, 3].map(x => x * 7).join(','))").toString();
        const std::string unicode = engine->evaluate("String(/\\p{Script=Greek}/u.test('\\u03b1'))").toString();
        std::printf("engine=%s runtime=%s expected=%s kind=%s path=%s answer=%s unicode=%s\n",
                    std::string(view::engine_type_name(engine->type())).c_str(),
                    engine->runtime_version().c_str(), engine->expected_runtime_version().c_str(),
                    engine->provider_kind().c_str(), engine->provider_path().c_str(),
                    answer.c_str(), unicode.c_str());
        if (engine->type() != view::JsEngineType::v8 || engine->runtime_version().rfind(expected, 0) != 0 ||
            engine->expected_runtime_version() != expected || engine->provider_kind() != "v8builder" ||
            answer != "7,14,21" || unicode != "true") {
            std::fprintf(stderr, "v8 consumer: the installed engine is not the pinned V8\n");
            return 1;
        }
    } catch (const std::exception& error) {
        std::fprintf(stderr, "v8 consumer: %s\n", error.what());
        return 1;
    }
    return 0;
}
