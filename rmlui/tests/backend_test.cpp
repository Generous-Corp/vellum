#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/importer_adapter.hpp>

#include "test_support.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

using namespace vellum::rmlui;
using vellum::rmlui::test::sha256_hex;

namespace {

struct TestModel final : ModelHost {
    Status read(std::string_view field, ModelValue& out) override {
        if (field == "gain") {
            out = ModelValue::number_value(gain);
            return Status::ok;
        }
        if (field == "enabled") {
            out = ModelValue::boolean_value(enabled);
            return Status::ok;
        }
        return Status::rejected;
    }
    Status write(std::string_view field, const ModelValue& value) override {
        if (field == "gain" && value.kind == ModelValue::Kind::number) {
            gain = value.number;
            return Status::ok;
        }
        if (field == "enabled" && value.kind == ModelValue::Kind::boolean) {
            enabled = value.boolean;
            return Status::ok;
        }
        return Status::rejected;
    }
    double gain = 0.25;
    bool enabled = false;
};

void test_hasher_self_check() {
    VELLUM_RMLUI_CHECK(sha256_hex("abc") ==
                       "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    VELLUM_RMLUI_CHECK(sha256_hex("") ==
                       "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

void test_headless_contract() {
    HeadlessRecorder headless;
    headless.add_asset("editor.lua", "return true\n");
    Backend backend(headless, headless, headless, &headless);

    VELLUM_RMLUI_CHECK(backend.open(640, 480) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.open(640, 480) == Status::already_open);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.lua") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("onload") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.invoke_action("parameter.gain") == Status::ok);
    backend.render();

    VELLUM_RMLUI_CHECK(backend.is_open());
    VELLUM_RMLUI_CHECK(headless.frame_width() == 640);
    VELLUM_RMLUI_CHECK(headless.frame_height() == 480);
    VELLUM_RMLUI_CHECK((headless.callbacks() == std::vector<std::string>{"editor.lua", "onload"}));
    VELLUM_RMLUI_CHECK((headless.actions() == std::vector<std::string>{"parameter.gain"}));

    backend.close();
    VELLUM_RMLUI_CHECK(!backend.is_open());
    VELLUM_RMLUI_CHECK(backend.dispatch_lua("late") == Status::not_open);
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);
}

void test_document_lowering() {
    HeadlessRecorder headless;
    headless.add_asset("editor.rml", "<rml><body><div style='display:block;width:120px;height:40px;"
                                     "background-color:#204060;'></div></body></rml>");
    Backend backend(headless, headless, headless);

    VELLUM_RMLUI_CHECK(backend.open(640, 480) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    VELLUM_RMLUI_CHECK(!headless.commands().empty());
    const auto& first = headless.commands().front();
    VELLUM_RMLUI_CHECK(first.vertices && !first.vertices->empty());
    VELLUM_RMLUI_CHECK(first.indices && first.indices->size() % 3 == 0);
}

void test_model_binding() {
    HeadlessRecorder headless;
    headless.add_asset("editor.rml", "<rml><body data-model='part'><div style='display:block;"
                                     "width:120px;height:40px;background-color:#204060;'>"
                                     "{{gain}}</div></body></rml>");
    Backend backend(headless, headless, headless);
    auto model = std::make_shared<TestModel>();

    VELLUM_RMLUI_CHECK(backend.open(320, 120) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.bind_model("part", model, {"gain", "enabled"}) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.bind_model("part", model, {"gain", "enabled"}) == Status::rejected);
    VELLUM_RMLUI_CHECK(backend.bind_model("other", model, {"gain", "gain"}) == Status::rejected);
    VELLUM_RMLUI_CHECK(backend.dirty_model("part", "gain") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.dirty_model("missing", "gain") == Status::rejected);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    VELLUM_RMLUI_CHECK(!headless.commands().empty());
    backend.close();
    VELLUM_RMLUI_CHECK(backend.dirty_model("part", "gain") == Status::not_open);
}

void test_fonts_fail_closed() {
    HeadlessRecorder headless;
    Backend backend(headless, headless, headless);
    VELLUM_RMLUI_CHECK(backend.load_font_face("missing.ttf", "Inter") == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.open(320, 120) == Status::ok);
    FontFaceOptions invalid_weight;
    invalid_weight.weight = 1001;
    VELLUM_RMLUI_CHECK(backend.load_font_face("missing.ttf", "Inter", invalid_weight) ==
                       Status::rejected);
    if (Backend::fonts_enabled()) {
        VELLUM_RMLUI_CHECK(backend.load_font_face("missing.ttf", "Inter") == Status::missing_asset);
        headless.add_asset("bad.ttf", "not-a-font");
        VELLUM_RMLUI_CHECK(backend.load_font_face("bad.ttf", "Inter") == Status::rejected);
#if defined(VELLUM_RMLUI_TEST_FONT)
        headless.add_asset("Inter-Regular.ttf",
                           vellum::rmlui::test::read_binary(VELLUM_RMLUI_TEST_FONT));
        VELLUM_RMLUI_CHECK(backend.load_font_face("Inter-Regular.ttf", "Inter") == Status::ok);
        headless.add_asset("font.rml", "<rml><body><div style='font-family:Inter;font-size:24px;'>"
                                       "Vellum</div></body></rml>");
        VELLUM_RMLUI_CHECK(backend.load_document("font.rml") == Status::ok);
        backend.render();
        const auto glyphs = std::find_if(
            headless.commands().begin(), headless.commands().end(), [](const DrawCommand& c) {
                return c.kind == DrawCommand::Kind::texture && c.texture_path.empty() &&
                       c.texture_bytes && !c.texture_bytes->empty();
            });
        VELLUM_RMLUI_CHECK(glyphs != headless.commands().end());
#endif
    } else {
        headless.add_asset("Inter-Regular.ttf", "not-a-font");
        VELLUM_RMLUI_CHECK(backend.load_font_face("Inter-Regular.ttf", "Inter") ==
                           Status::rejected);
    }
}

void test_manifest_textures() {
    HeadlessRecorder headless;
    // A PNG signature and IHDR dimensions are enough at this seam; decoding
    // belongs to the renderer.
    std::string png("\x89PNG\r\n\x1a\n", 8);
    png.append("\0\0\0\rIHDR\0\0\0\x10\0\0\0\x08\x08\06\0\0\0", 21);
    headless.add_asset("skin/button.png", std::move(png));
    headless.add_asset(
        "editor.rml",
        "<rml><body><img src='skin/button.png' style='width:16px;height:8px;'/></body></rml>");
    Backend backend(headless, headless, headless);

    VELLUM_RMLUI_CHECK(backend.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    const auto texture = std::find_if(
        headless.commands().begin(), headless.commands().end(),
        [](const DrawCommand& command) { return command.kind == DrawCommand::Kind::texture; });
    VELLUM_RMLUI_CHECK(texture != headless.commands().end());
    VELLUM_RMLUI_CHECK(texture->texture_path == "skin/button.png");
    VELLUM_RMLUI_CHECK(texture->texture_bytes != nullptr);
    VELLUM_RMLUI_CHECK(texture->texture_bytes->size() == 29);
    VELLUM_RMLUI_CHECK(texture->texture_width == 16);
    VELLUM_RMLUI_CHECK(texture->texture_height == 8);
    VELLUM_RMLUI_CHECK(texture->vertices && !texture->vertices->empty());
    VELLUM_RMLUI_CHECK(texture->indices && !texture->indices->empty());
}

void test_clip_and_transform_state() {
    HeadlessRecorder headless;
    headless.add_asset(
        "editor.rml",
        "<rml><body><div style='display:block;overflow:hidden;width:48px;height:32px;'>"
        "<div style='display:block;transform:rotate(8deg);width:24px;height:16px;"
        "background-color:#204060;'>x</div></div></body></rml>");
    Backend backend(headless, headless, headless);

    VELLUM_RMLUI_CHECK(backend.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    const auto stateful = std::find_if(
        headless.commands().begin(), headless.commands().end(),
        [](const DrawCommand& command) { return command.scissor_enabled || command.transform_enabled; });
    VELLUM_RMLUI_CHECK(stateful != headless.commands().end());
}

void test_input_routing() {
    HeadlessRecorder headless;
    headless.add_asset(
        "editor.rml",
        "<rml><body><button style='display:block;width:40px;height:40px;'>x</button></body></rml>");
    Backend backend(headless, headless, headless);

    VELLUM_RMLUI_CHECK(backend.open(64, 64) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::ok);
    backend.render();
    VELLUM_RMLUI_CHECK(backend.process_mouse_move(10, 10) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.mouse_interacting());
    VELLUM_RMLUI_CHECK(backend.process_mouse_button_down(0) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_mouse_button_up(0) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_mouse_wheel(0.0f, 1.0f) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_mouse_leave() == Status::ok);
    VELLUM_RMLUI_CHECK(!backend.mouse_interacting());
    VELLUM_RMLUI_CHECK(backend.process_key_down(65) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_key_up(65) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_text_input("x") == Status::ok);
    VELLUM_RMLUI_CHECK(backend.process_text_input("") == Status::rejected);
    backend.close();
    VELLUM_RMLUI_CHECK(backend.process_mouse_move(0, 0) == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.process_key_down(65) == Status::not_open);
}

void test_extensions_fail_closed() {
    HeadlessRecorder headless;
    Backend without_host(headless, headless, headless);
    VELLUM_RMLUI_CHECK(without_host.register_custom_property({"knob-frames", "1", "number"}) ==
                       Status::ok);
    VELLUM_RMLUI_CHECK(without_host.register_custom_property({"knob-frames", "1", "number"}) ==
                       Status::rejected);
    VELLUM_RMLUI_CHECK(without_host.open(16, 16) == Status::rejected);
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);

    HeadlessRecorder rejecting;
    rejecting.reject_extensions(true);
    Backend rejected(rejecting, rejecting, rejecting, nullptr, &rejecting);
    VELLUM_RMLUI_CHECK(rejected.register_element_instancer({"knob"}) == Status::ok);
    VELLUM_RMLUI_CHECK(rejected.open(16, 16) == Status::rejected);
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);

    HeadlessRecorder accepting;
    Backend accepted(accepting, accepting, accepting, nullptr, &accepting);
    VELLUM_RMLUI_CHECK(accepted.register_element_instancer({"knob"}) == Status::ok);
    VELLUM_RMLUI_CHECK(accepted.open(16, 16) == Status::ok);
    VELLUM_RMLUI_CHECK(accepting.element_instancers().size() == 1);
    VELLUM_RMLUI_CHECK(accepted.register_element_instancer({"late"}) == Status::rejected);
}

void test_missing_and_rejected_assets() {
    HeadlessRecorder headless;
    headless.reject_path("../escape.rml");
    Backend backend(headless, headless, headless);

    VELLUM_RMLUI_CHECK(backend.load_document("editor.rml") == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.reload_document() == Status::not_open);
    VELLUM_RMLUI_CHECK(backend.open(1, 1) == Status::ok);
    VELLUM_RMLUI_CHECK(backend.reload_document() == Status::rejected);
    VELLUM_RMLUI_CHECK(backend.load_document("missing.rml") == Status::missing_asset);
    VELLUM_RMLUI_CHECK(backend.load_document("../escape.rml") == Status::rejected);
    VELLUM_RMLUI_CHECK(backend.invoke_action("unsupported") == Status::rejected);
}

void test_frames_are_isolated() {
    HeadlessRecorder headless;
    headless.begin_frame(100, 80);
    DrawCommand command;
    headless.submit(command);
    headless.end_frame();
    VELLUM_RMLUI_CHECK(headless.commands().size() == 1);
    headless.begin_frame(20, 10);
    VELLUM_RMLUI_CHECK(headless.commands().empty());
}

void test_rooted_assets() {
    const auto root = vellum::rmlui::test::unique_temp_dir("vellum-rmlui-assets");
    std::ofstream(root / "editor.rml") << "<rml/>\n";
    const auto digest = sha256_hex("<rml/>\n");
    RootedAssetProvider assets(root, {{"editor.rml", digest}}, sha256_hex);
    Asset loaded;

    VELLUM_RMLUI_CHECK(assets.load("editor.rml", loaded) == Status::ok);
    VELLUM_RMLUI_CHECK(loaded.bytes == "<rml/>\n");
    RootedAssetProvider no_hasher(root, {{"editor.rml", digest}}, {});
    VELLUM_RMLUI_CHECK(no_hasher.load("editor.rml", loaded) == Status::rejected);
    RootedAssetProvider capped(root, {{"editor.rml", digest}}, sha256_hex, 3);
    VELLUM_RMLUI_CHECK(capped.load("editor.rml", loaded) == Status::rejected);
    VELLUM_RMLUI_CHECK(assets.load("../editor.rml", loaded) == Status::rejected);
    VELLUM_RMLUI_CHECK(assets.load("missing.rml", loaded) == Status::rejected);

    const auto outside = root.parent_path() / (root.filename().string() + "-outside");
    std::ofstream(outside) << "outside";
    std::error_code symlink_error;
    std::filesystem::create_symlink(outside, root / "escape.rml", symlink_error);
    if (!symlink_error) {
        RootedAssetProvider symlink_assets(root, {{"escape.rml", sha256_hex("outside")}},
                                           sha256_hex);
        VELLUM_RMLUI_CHECK(symlink_assets.load("escape.rml", loaded) == Status::rejected);
    }

    std::ofstream(root / "editor.rml", std::ios::trunc) << "tampered";
    VELLUM_RMLUI_CHECK(assets.load("editor.rml", loaded) == Status::rejected);
    std::filesystem::remove_all(root);
    std::filesystem::remove(outside);
}

struct StubLua final : LuaHost {
    HostStatus initialise() override {
        return HostStatus::ok;
    }
    HostStatus dispatch(std::string_view) override {
        return HostStatus::ok;
    }
};
struct StubElements final : ElementHost {
    HostStatus register_element(ElementRegistration registration) override {
        retained = std::move(registration.dispatch);
        return HostStatus::ok;
    }
    std::function<HostStatus(std::string_view)> retained;
};
struct ThrowingEvents final : EventHost {
    HostStatus dispatch(HostEvent) override {
        throw 1;
    }
};

void test_importer_adapter_quarantine() {
    ImporterAdapter adapter;
    auto elements = std::make_shared<StubElements>();
    VELLUM_RMLUI_CHECK(adapter.activate() == HostStatus::missing_capability);
    VELLUM_RMLUI_CHECK(adapter.bind_lua(std::make_shared<StubLua>()) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(adapter.bind_elements(elements) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(adapter.bind_events(std::make_shared<ThrowingEvents>()) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(adapter.activate() == HostStatus::ok);
    VELLUM_RMLUI_CHECK(adapter.receipt().host_bridge_active);
    VELLUM_RMLUI_CHECK(adapter.register_element({"knob", [](std::string_view) {
                                                     return HostStatus::ok;
                                                 }}) == HostStatus::ok);
    VELLUM_RMLUI_CHECK(elements->retained("x") == HostStatus::ok);
    // A throwing provider quarantines the bridge; retained callbacks go dead
    // and the receipt stops advertising capabilities.
    VELLUM_RMLUI_CHECK(adapter.dispatch_event({HostEventKind::parameter, "gain", 0.5}) ==
                       HostStatus::rejected);
    VELLUM_RMLUI_CHECK(!adapter.receipt().host_bridge_active);
    VELLUM_RMLUI_CHECK(elements->retained("x") == HostStatus::not_active);
}

} // namespace

int main() {
    test_hasher_self_check();
    test_headless_contract();
    test_document_lowering();
    test_model_binding();
    test_fonts_fail_closed();
    test_manifest_textures();
    test_clip_and_transform_state();
    test_input_routing();
    test_extensions_fail_closed();
    test_missing_and_rejected_assets();
    test_frames_are_isolated();
    test_rooted_assets();
    test_importer_adapter_quarantine();
    VELLUM_RMLUI_CHECK(Backend::open_instances() == 0);
    std::puts("vellum-rmlui backend: ok");
    return 0;
}
