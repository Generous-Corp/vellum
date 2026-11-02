// Renders a knob and an LED, bound to a gain value, through the soft renderer
// and compares the PNG byte for byte with the committed golden image. The only
// libraries involved are the module and its pinned RmlUi.
//
//   soft_render_golden_test                 compare with the golden image
//   soft_render_golden_test --update-golden rewrite the golden image
#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/soft_renderer.hpp>

#include "test_support.hpp"

#include <cstring>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

using namespace vellum::rmlui;
namespace test = vellum::rmlui::test;

namespace {

const std::filesystem::path fixtures = VELLUM_RMLUI_TEST_FIXTURES;
constexpr std::uint32_t width = 160;
constexpr std::uint32_t height = 96;

struct GainModel final : ModelHost {
    explicit GainModel(double value) : gain(value) {}
    Status read(std::string_view field, ModelValue& out) override {
        if (field != "gain")
            return Status::rejected;
        out = ModelValue::number_value(gain);
        return Status::ok;
    }
    Status write(std::string_view field, const ModelValue& value) override {
        if (field != "gain" || value.kind != ModelValue::Kind::number)
            return Status::rejected;
        gain = value.number;
        return Status::ok;
    }
    double gain;
};

struct Render {
    std::string png;
    std::size_t triangles = 0;
    std::size_t skipped = 0;
};

Render render(const std::string& document, double gain) {
    // Assets come through the manifest-checked provider with the test's own
    // SHA-256, as a shipped skin's would.
    std::vector<AssetManifestEntry> manifest;
    for (const auto* name : {"knob_led.rml", "empty.rml"})
        manifest.push_back({name, test::sha256_hex(test::read_binary(fixtures / name))});
    RootedAssetProvider assets(fixtures, manifest, test::sha256_hex);
    SoftRenderer renderer(0x000000ffu);
    HeadlessRecorder no_lua;
    Backend backend(assets, renderer, no_lua);
    VELLUM_RMLUI_CHECK(backend.open(width, height) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.bind_model("controls", std::make_shared<GainModel>(gain), {"gain"}) ==
                       Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document(document) == Status::ok);
    // The first update evaluates the data bindings; the second lays out the
    // class and transform they produced.
    backend.render();
    backend.render();
    VELLUM_RMLUI_CHECK(renderer.width() == width && renderer.height() == height);
    Render result{renderer.encode_png(), renderer.triangles_drawn(), renderer.skipped_draws()};
    backend.close();
    return result;
}

} // namespace

int main(int argc, char** argv) {
    const bool update = argc > 1 && std::strcmp(argv[1], "--update-golden") == 0;
    const auto golden_path = fixtures / "knob_led.golden.png";

    const auto knob = render("knob_led.rml", 0.75);
    VELLUM_RMLUI_CHECK(!knob.png.empty());
    VELLUM_RMLUI_CHECK(knob.triangles > 0);
    VELLUM_RMLUI_CHECK(knob.skipped == 0);
    test::write_binary("knob_led.actual.png", knob.png);

    // The renderer is a pure function of the document and model.
    VELLUM_RMLUI_CHECK(render("knob_led.rml", 0.75).png == knob.png);

    if (update) {
        test::write_binary(golden_path, knob.png);
        std::printf("wrote %s (%zu bytes)\n", golden_path.string().c_str(), knob.png.size());
        return 0;
    }

    const auto golden = test::read_binary(golden_path);
    VELLUM_RMLUI_CHECK(!golden.empty());
    if (golden != knob.png) {
        std::fprintf(stderr,
                     "knob_led.actual.png (%zu bytes, sha256 %s) differs from %s "
                     "(%zu bytes, sha256 %s)\n",
                     knob.png.size(), test::sha256_hex(knob.png).c_str(),
                     golden_path.string().c_str(), golden.size(),
                     test::sha256_hex(golden).c_str());
        return 1;
    }

    // Negative controls: the same comparison must reject an empty document and
    // the same document with the LED off and the pointer turned down.
    const auto empty = render("empty.rml", 0.75);
    VELLUM_RMLUI_CHECK(!empty.png.empty());
    VELLUM_RMLUI_CHECK(empty.png != golden);
    const auto low = render("knob_led.rml", 0.2);
    VELLUM_RMLUI_CHECK(low.png != golden);
    VELLUM_RMLUI_CHECK(low.png != empty.png);

    std::printf("vellum-rmlui soft render golden: ok (%zu bytes, sha256 %s)\n", golden.size(),
                test::sha256_hex(golden).c_str());
    return 0;
}
