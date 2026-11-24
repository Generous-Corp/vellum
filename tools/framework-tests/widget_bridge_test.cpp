// Drives the framework's scripted widget bridge headlessly: a script builds
// native widgets, native input reaches the script's handlers, and a failing
// script reports its error and leaves the tree it already built.

#include "framework_test.hpp"

#include <vellum/canvas/bundled_fonts.hpp>
#include <vellum/state/store.hpp>
#include <vellum/view/design_codegen.hpp>
#include <vellum/view/design_ir.hpp>
#include <vellum/view/font_container.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/scripted_ui.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/widgets.hpp>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <unistd.h>

namespace {

namespace view = vellum::view;

struct Harness {
    view::ScriptEngine engine;
    view::View root;
    vellum::state::StateStore store;
    view::WidgetBridge bridge;

    explicit Harness(view::CapabilitySet capabilities = view::CapabilitySet::all())
        : bridge(engine, root, store, nullptr, capabilities) { root.set_bounds({0, 0, 400, 300}); }

    std::string js_string(const std::string& expression) {
        return engine.evaluate("String(" + expression + ")").toString();
    }
};

FRAMEWORK_TEST(script_creates_a_label_and_sets_its_text) {
    Harness h;
    h.bridge.load_script("createLabel('title', 10, 10, 120, 20);"
                         "setText('title', 'hello');");

    CHECK(h.root.child_count() == 1);
    auto* label = dynamic_cast<view::Label*>(h.bridge.widget("title"));
    CHECK(label != nullptr);
    CHECK(label->parent() == &h.root);
    CHECK(label->text() == "hello");
    CHECK(h.js_string("getText('title')") == "hello");
}

FRAMEWORK_TEST(sealed_font_api_is_available_without_filesystem) {
    Harness h(view::CapabilitySet{});
    h.bridge.load_script("globalThis.embeddedType = typeof registerEmbeddedFont; globalThis.pathType = typeof registerFont;");
    CHECK(h.js_string("embeddedType") == "function");
    CHECK(h.js_string("pathType") == "undefined");
    CHECK(h.js_string("registerEmbeddedFont('sealed-family', '/etc/hosts')") == "false");
}

// Font registration is process-global and every case here shares one process,
// so each probe registers under a family name nothing else uses.
std::string probe_family(const std::string& label) {
    static int counter = 0;
    return "SealedProbe-" + label + "-" + std::to_string(::getpid()) + "-" +
           std::to_string(++counter);
}

std::filesystem::path fresh_temp_dir(const std::string& label) {
    const auto dir = std::filesystem::temp_directory_path() /
                     ("vellum-sealed-fonts-" + label + "-" + std::to_string(::getpid()));
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);
    std::filesystem::create_directories(dir);
    return dir;
}

std::vector<unsigned char> read_test_font() {
    std::ifstream input(VELLUM_TEST_FONT_PATH, std::ios::binary);
    std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)), {});
    return bytes;
}

void write_file(const std::filesystem::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    out << text;
}

bool register_embedded(Harness& h, const std::string& family, const std::string& name) {
    return h.js_string("registerEmbeddedFont('" + family + "', '" + name + "', 400, 700)") ==
           "true";
}

// A WOFF2 header (48 bytes) followed by a body no decoder can read, declaring
// `sfnt_size` decompressed bytes.
std::vector<unsigned char> synthetic_woff2(std::uint32_t sfnt_size, std::size_t body = 64) {
    std::vector<unsigned char> bytes(48 + body, 0x5a);
    bytes[0] = 'w';
    bytes[1] = 'O';
    bytes[2] = 'F';
    bytes[3] = '2';
    bytes[4] = 0x00; // flavor 0x00010000
    bytes[5] = 0x01;
    bytes[6] = 0x00;
    bytes[7] = 0x00;
    const auto length = static_cast<std::uint32_t>(bytes.size());
    for (int i = 0; i < 4; ++i)
        bytes[8 + i] = static_cast<unsigned char>(length >> (24 - 8 * i));
    bytes[12] = 0x00; // numTables 1
    bytes[13] = 0x01;
    for (int i = 0; i < 4; ++i)
        bytes[16 + i] = static_cast<unsigned char>(sfnt_size >> (24 - 8 * i));
    return bytes;
}

FRAMEWORK_TEST(empty_grant_registers_only_sealed_embedded_fonts) {
    const auto bytes = read_test_font();
    CHECK(bytes.size() > 1024);
    // Bytes that carry a font signature but do not parse, a container over the
    // shipped-font limit, and plain text: none may reach the registry.
    const std::vector<unsigned char> malformed{'O', 'T', 'T', 'O', 'n', 'o', 't',
                                               ' ', 'a', ' ', 'f', 'o', 'n', 't'};
    std::vector<unsigned char> oversized(view::kMaxShippedFontBytes + 1, 0);
    std::copy(bytes.begin(), bytes.end(), oversized.begin());
    static const char text[] = "plain text, not a font";
    // A real face whose last table record claims bytes past the end of the
    // file: the structural check refuses it whatever the platform parser does.
    auto overrun = bytes;
    if (overrun.size() > 12) {
        const std::size_t tables = (std::size_t(overrun[4]) << 8) | overrun[5];
        const std::size_t record = 12 + (tables - 1) * 16;
        for (std::size_t i = 0; i < 4 && record + 15 < overrun.size(); ++i)
            overrun[record + 12 + i] = 0x7f;
    }

    const auto script_dir = fresh_temp_dir("bridge");
    std::filesystem::create_directories(script_dir / "assets");
    // A real font beside the script that the panel did not seal.
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, script_dir / "assets" / "unsealed.ttf");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, script_dir / "userfont-1");

    Harness h(view::CapabilitySet{});
    h.bridge.set_script_base_dir(script_dir);
    h.bridge.set_embedded_assets({
        view::WidgetBridge::EmbeddedAsset{"assets/sealed.ttf", bytes.data(), bytes.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/malformed.ttf", malformed.data(),
                                          malformed.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/oversized.ttf", oversized.data(),
                                          oversized.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/overrun.ttf", overrun.data(), overrun.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/text.ttf",
                                          reinterpret_cast<const unsigned char*>(text),
                                          sizeof(text) - 1},
    });
    CHECK(h.js_string("typeof registerEmbeddedFont") == "function");
    CHECK(h.js_string("typeof registerFont") == "undefined");

    const auto sealed = probe_family("sealed");
    CHECK(register_embedded(h, sealed, "assets/sealed.ttf"));
    CHECK(vellum::canvas::is_font_registered(sealed));
    const auto dotted = probe_family("dotted");
    CHECK(register_embedded(h, dotted, "./assets/sealed.ttf"));
    CHECK(vellum::canvas::is_font_registered(dotted));

    // Nothing outside the sealed set resolves: not a real font on disk beside
    // the script, a bare id, a climb out of it or back into it, an absolute
    // path or a file:// URL. Sealed bytes that are not a usable font never
    // reach the registry either.
    const auto disk_font = (script_dir / "assets" / "unsealed.ttf").generic_string();
    for (const auto& name : std::vector<std::string>{
             "assets/unsealed.ttf", "userfont-1", "../userfont-1", "assets/../assets/sealed.ttf",
             "assets/../../assets/sealed.ttf", "/etc/hosts", disk_font, "file://" + disk_font,
             "file://assets/sealed.ttf", "assets/malformed.ttf", "assets/oversized.ttf",
             "assets/overrun.ttf", "assets/text.ttf"}) {
        const auto family = probe_family("refused");
        const bool registered = register_embedded(h, family, name);
        if (registered || vellum::canvas::is_font_registered(family))
            std::fprintf(stderr, "  registered from '%s'\n", name.c_str());
        CHECK(!registered);
        CHECK(!vellum::canvas::is_font_registered(family));
    }
    std::error_code ec;
    std::filesystem::remove_all(script_dir, ec);
}

FRAMEWORK_TEST(sealed_woff2_that_the_decoder_rejects_is_refused) {
    CHECK(vellum::canvas::woff2_decoder_available());
    // A decompression bomb declares a face far over the decoder's bound; a
    // malformed container declares a plausible size over a body that is not
    // WOFF2, declares none, or is cut short.
    const auto bomb = synthetic_woff2(512u * 1024u * 1024u);
    const auto garbage = synthetic_woff2(4096);
    const auto undeclared = synthetic_woff2(0);
    const std::vector<unsigned char> truncated(garbage.begin(), garbage.begin() + 18);

    Harness h(view::CapabilitySet{});
    h.bridge.set_embedded_assets({
        view::WidgetBridge::EmbeddedAsset{"assets/bomb.woff2", bomb.data(), bomb.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/garbage.woff2", garbage.data(), garbage.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/undeclared.woff2", undeclared.data(),
                                          undeclared.size()},
        view::WidgetBridge::EmbeddedAsset{"assets/truncated.woff2", truncated.data(),
                                          truncated.size()},
    });
    for (const char* name : {"assets/bomb.woff2", "assets/garbage.woff2",
                             "assets/undeclared.woff2", "assets/truncated.woff2"}) {
        const auto family = probe_family("woff2");
        const bool registered = register_embedded(h, family, name);
        if (registered)
            std::fprintf(stderr, "  registered from '%s'\n", name);
        CHECK(!registered);
        CHECK(!vellum::canvas::is_font_registered(family));
    }
}

FRAMEWORK_TEST(a_panel_cannot_rebind_a_family_another_owner_registered) {
    const auto bytes = read_test_font();
    const std::vector<view::WidgetBridge::EmbeddedAsset> sealed{
        view::WidgetBridge::EmbeddedAsset{"assets/face.ttf", bytes.data(), bytes.size()}};
    Harness a(view::CapabilitySet{}), b(view::CapabilitySet{}), a2(view::CapabilitySet{});
    const auto first_owner = probe_family("owner-a");
    a.bridge.set_storage_identity(first_owner);
    b.bridge.set_storage_identity(probe_family("owner-b"));
    a2.bridge.set_storage_identity(first_owner);
    for (auto* h : {&a, &b, &a2})
        h->bridge.set_embedded_assets(sealed);

    const auto family = probe_family("shared");
    CHECK(register_embedded(a, family, "assets/face.ttf"));
    // Another panel may not take the family over.
    CHECK(!register_embedded(b, family, "assets/face.ttf"));
    // The owning panel adds faces, and another instance of it shares them.
    CHECK(register_embedded(a, family, "assets/face.ttf"));
    CHECK(register_embedded(a2, family, "assets/face.ttf"));

    // A family host code registered is not a panel's to rebind either.
    const auto host_family = probe_family("host");
    CHECK(vellum::canvas::register_font(bytes.data(), bytes.size(), host_family));
    CHECK(!register_embedded(a, host_family, "assets/face.ttf"));
}

FRAMEWORK_TEST(anonymous_bridges_cannot_share_sealed_font_ownership) {
    const auto bytes = read_test_font();
    Harness a(view::CapabilitySet{}), b(view::CapabilitySet{});
    for (auto* h : {&a, &b})
        h->bridge.set_embedded_assets({{"face.ttf", bytes.data(), bytes.size()}});
    CHECK(a.bridge.storage_identity().empty());
    CHECK(b.bridge.storage_identity().empty());
    const auto family = probe_family("anonymous");
    CHECK(register_embedded(a, family, "face.ttf"));
    CHECK(!register_embedded(b, family, "face.ttf"));
    CHECK(register_embedded(a, family, "face.ttf"));
}

FRAMEWORK_TEST(anonymous_sessions_keep_private_font_ownership_across_reload) {
    const auto bytes = read_test_font();
    const auto family = probe_family("anonymous-session");
    view::ScriptedUiOptions options;
    options.script_source = "if (!registerEmbeddedFont('" + family +
        "', 'face.ttf')) throw new Error('owner lost');";
    options.embedded_assets = {{"face.ttf", bytes.data(), bytes.size()}};
    options.granted_capabilities = view::CapabilitySet{};
    options.enable_hot_reload = options.enable_theme_reload = false;
    view::View root_a, root_b;
    vellum::state::StateStore store_a, store_b;
    view::ScriptedUiSession a(root_a, store_a, options);
    std::string error;
    CHECK(a.load(&error));
    CHECK(a.reload(&error));
    options.script_source = "if (registerEmbeddedFont('" + family +
        "', 'face.ttf')) throw new Error('another owner accepted');";
    view::ScriptedUiSession b(root_b, store_b, options);
    CHECK(b.load(&error));
    CHECK(a.reload(&error));
}

class ResetTestPort final : public view::ScriptInspectorPort {
public:
    static inline std::function<std::string(EvaluationDeadline)> reset;
    void attach(view::ScriptEngine*) override {}
    void detach() override {}
    bool pump() override { return false; }
    void set_post_evaluation_reset(std::function<std::string(EvaluationDeadline)> cb) override {
        reset = std::move(cb);
    }
    std::string run_pending_post_evaluation_reset() override { return {}; }
};

FRAMEWORK_TEST(deadline_recovery_reuses_last_good_sealed_fonts) {
    const auto dir = fresh_temp_dir("recovery");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, dir / "face.ttf");
    const auto family = probe_family("recovery");
    write_file(dir / "ui.js", "if (!registerEmbeddedFont('" + family +
        "', 'face.ttf')) throw new Error('last good font lost');");
    view::ScriptedUiOptions options;
    options.script_path = dir / "ui.js";
    options.granted_capabilities = view::CapabilitySet{};
    options.enable_hot_reload = options.enable_theme_reload = false;
    view::View root;
    vellum::state::StateStore store;
    struct FactoryGuard {
        view::ScriptInspectorPortFactory previous = view::script_inspector_port_factory();
        FactoryGuard() {
            view::set_script_inspector_port_factory([]() -> std::unique_ptr<view::ScriptInspectorPort> {
                return std::make_unique<ResetTestPort>();
            });
        }
        ~FactoryGuard() {
            view::set_script_inspector_port_factory(previous);
            ResetTestPort::reset = {};
        }
    } guard;
    view::ScriptedUiSession session(root, store, options);
    std::string error;
    CHECK(session.load(&error));
    CHECK(vellum::canvas::is_font_registered(family));
    // An ordinary failed reload must not replace the successful asset snapshot.
    std::filesystem::remove(dir / "face.ttf");
    CHECK(!session.reload(&error));
    CHECK(static_cast<bool>(ResetTestPort::reset));
    if (ResetTestPort::reset) {
        const auto reset_error = ResetTestPort::reset(
            std::chrono::steady_clock::now() + std::chrono::seconds(2));
        if (!reset_error.empty()) std::fprintf(stderr, "  reset error: %s\n", reset_error.c_str());
        CHECK(reset_error.empty());
    }
    std::filesystem::remove_all(dir);
}

FRAMEWORK_TEST(directory_switch_does_not_reuse_same_timestamp_font_bytes) {
    const auto first = fresh_temp_dir("cache-first");
    const auto second = fresh_temp_dir("cache-second");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, first / "face.ttf");
    write_file(second / "face.ttf", "not a font");
    std::filesystem::last_write_time(second / "face.ttf",
                                    std::filesystem::last_write_time(first / "face.ttf"));
    const auto family_a = probe_family("cache-first");
    const auto family_b = probe_family("cache-second");
    write_file(first / "ui.js", "if (!registerEmbeddedFont('" + family_a +
        "', 'face.ttf')) throw new Error('positive control failed');");
    write_file(second / "ui.js", "if (registerEmbeddedFont('" + family_b +
        "', 'face.ttf')) throw new Error('stale bytes reused');");
    view::ScriptedUiOptions options;
    options.script_path = first / "ui.js";
    options.granted_capabilities = view::CapabilitySet{};
    options.enable_hot_reload = options.enable_theme_reload = false;
    view::View root;
    vellum::state::StateStore store;
    view::ScriptedUiSession session(root, store, options);
    std::string error;
    CHECK(session.load(&error));
    CHECK(vellum::canvas::is_font_registered(family_a));
    CHECK(session.reload_from(second / "ui.js", &error));
    CHECK(!vellum::canvas::is_font_registered(family_b));
    std::filesystem::remove_all(first);
    std::filesystem::remove_all(second);
}

FRAMEWORK_TEST(a_path_backed_session_seals_the_fonts_beside_its_script) {
    const auto dir = fresh_temp_dir("session");
    const auto outside = fresh_temp_dir("session-outside");
    std::filesystem::create_directories(dir / "assets");
    std::filesystem::create_directories(dir / "build");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, dir / "assets" / "face.ttf");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, outside / "elsewhere.ttf");
    // A build tree beside the script is not part of the panel.
    write_file(dir / "build" / "CMakeCache.txt", "");
    std::filesystem::copy_file(VELLUM_TEST_FONT_PATH, dir / "build" / "face.ttf");
    std::error_code ec;
    std::filesystem::create_symlink(outside / "elsewhere.ttf", dir / "assets" / "escape.ttf", ec);
    CHECK(!ec);

    const auto sealed = probe_family("path-sealed");
    const auto marker = probe_family("path-no-fs");
    const auto escaped = probe_family("path-escaped");
    const auto built = probe_family("path-build");
    const auto script = [&](const std::string& family) {
        return "registerEmbeddedFont('" + family + "', 'assets/face.ttf', 400, 400);\n"
               "registerEmbeddedFont('" + escaped + "', 'assets/escape.ttf', 400, 400);\n"
               "registerEmbeddedFont('" + built + "', 'build/face.ttf', 400, 400);\n"
               "if (typeof registerFont === 'undefined') registerEmbeddedFont('" + marker +
               "', 'assets/face.ttf', 400, 400);\n";
    };
    write_file(dir / "ui.js", script(sealed));

    view::View root;
    root.set_bounds({0, 0, 320, 240});
    vellum::state::StateStore store;
    view::ScriptedUiOptions options;
    options.script_path = dir / "ui.js";
    options.enable_hot_reload = false;
    options.enable_theme_reload = false;
    options.granted_capabilities = view::CapabilitySet{};
    view::ScriptedUiSession session(root, store, options);
    std::string error;
    CHECK(session.load(&error));
    if (!error.empty())
        std::fprintf(stderr, "  load error: %s\n", error.c_str());
    // No Filesystem grant and no registerFont: the face came from sealed bytes.
    CHECK(vellum::canvas::is_font_registered(sealed));
    CHECK(vellum::canvas::is_font_registered(marker));
    // A symlink that leaves the directory and a build tree are not sealed.
    CHECK(!vellum::canvas::is_font_registered(escaped));
    CHECK(!vellum::canvas::is_font_registered(built));

    // A reload seals the files again for the new realm.
    const auto reloaded = probe_family("path-reloaded");
    write_file(dir / "ui.js", script(reloaded));
    CHECK(session.reload(&error));
    CHECK(vellum::canvas::is_font_registered(reloaded));
    CHECK(!vellum::canvas::is_font_registered(escaped));

    std::filesystem::remove_all(dir, ec);
    std::filesystem::remove_all(outside, ec);
}

FRAMEWORK_TEST(codegen_registers_a_font_beside_the_script_by_its_sealed_name) {
    view::DesignIR ir;
    ir.root.type = "frame";
    view::IRAssetRef asset;
    asset.asset_id = "userfont-1";
    asset.local_path = "assets/0123abcd.ttf";
    ir.asset_manifest.assets.push_back(asset);
    view::IRFontAsset fa;
    fa.family = "Sealed Grotesk";
    fa.weight = 400;
    fa.weight_max = 700;
    fa.asset_id = "userfont-1";
    fa.resolved_path = "assets/0123abcd.ttf";
    ir.font_family_assets.push_back(fa);

    CHECK(view::sealed_font_asset_name(ir, fa) == "assets/0123abcd.ttf");
    const auto js = view::generate_pulp_js(ir);
    CHECK(js.find("if (typeof registerEmbeddedFont === 'function') registerEmbeddedFont("
                  "'Sealed Grotesk', 'assets/0123abcd.ttf', 400, 700);") != std::string::npos);
    // The manifest id is not an embedded file name and never reaches the call.
    CHECK(js.find("'userfont-1'") == std::string::npos);
    // A sealed face emits no Filesystem-gated call.
    CHECK(js.find("registerFont") == std::string::npos);

    // A face reachable only by an absolute path keeps the gated fallback.
    auto absolute = ir;
    absolute.asset_manifest.assets[0].local_path = "/design/pack/clash.ttf";
    absolute.font_family_assets[0].resolved_path = "/design/pack/clash.ttf";
    CHECK(view::sealed_font_asset_name(absolute, absolute.font_family_assets[0]).empty());
    const auto absolute_js = view::generate_pulp_js(absolute);
    CHECK(absolute_js.find("registerEmbeddedFont") == std::string::npos);
    CHECK(absolute_js.find("if (typeof registerFont === 'function') registerFont('Sealed "
                           "Grotesk', '/design/pack/clash.ttf', 400, 700);") !=
          std::string::npos);

    // Names that are not script-relative are never offered as sealed.
    for (const char* unsafe : {"../assets/0123abcd.ttf", "assets/../../x.ttf",
                               "file://assets/x.ttf", "/etc/hosts", "C:/fonts/x.ttf"}) {
        auto probe = ir;
        probe.asset_manifest.assets.clear();
        probe.font_family_assets[0].resolved_path = unsafe;
        const bool offered = !view::sealed_font_asset_name(probe, probe.font_family_assets[0]).empty();
        if (offered)
            std::fprintf(stderr, "  sealed name offered for '%s'\n", unsafe);
        CHECK(!offered);
    }
    // A leading ./ normalizes to the embedded name.
    auto dotted = ir;
    dotted.asset_manifest.assets[0].local_path = "./assets/0123abcd.ttf";
    CHECK(view::sealed_font_asset_name(dotted, dotted.font_family_assets[0]) ==
          "assets/0123abcd.ttf");
}

FRAMEWORK_TEST(native_click_reaches_the_script_toggle_handler) {
    Harness h;
    h.bridge.load_script("globalThis.flips = [];"
                         "createToggle('mute', 20, 40, 60, 30);"
                         "on('mute', 'toggle', function (on) { flips.push(on); });");

    auto* toggle = dynamic_cast<view::Toggle*>(h.bridge.widget("mute"));
    CHECK(toggle != nullptr);
    CHECK(!toggle->is_on());

    h.root.simulate_click({50, 55});
    CHECK(toggle->is_on());
    CHECK(h.js_string("flips.join(',')") == "1");

    h.root.simulate_click({50, 55});
    CHECK(!toggle->is_on());
    CHECK(h.js_string("flips.join(',')") == "1,0");
}

FRAMEWORK_TEST(click_outside_the_toggle_does_not_reach_the_handler) {
    Harness h;
    h.bridge.load_script("globalThis.flips = [];"
                         "createToggle('mute', 20, 40, 60, 30);"
                         "on('mute', 'toggle', function (on) { flips.push(on); });");

    h.root.simulate_click({300, 250});
    CHECK(h.js_string("flips.length") == "0");
}

FRAMEWORK_TEST(native_focus_transfer_notifies_target_and_ancestors) {
    view::View root;
    root.set_bounds({0, 0, 200, 100});
    auto child = std::make_unique<view::View>();
    child->set_id("focus-target");
    child->set_focusable(true);
    auto* target = child.get();
    root.add_child(std::move(child));
    int focus = 0;
    int focusin = 0;
    int blur = 0;
    int focusout = 0;
    target->on_focus_event = [&](bool gained, view::FocusCause) {
        gained ? ++focus : ++blur;
    };
    root.on_descendant_focus_event = [&](view::View&, bool gained, view::FocusCause) {
        gained ? ++focusin : ++focusout;
    };
    CHECK(view::transfer_input_focus(root, target, view::FocusCause::keyboard));
    CHECK(target->focus_visible());
    CHECK(focus == 1);
    CHECK(focusin == 1);
    CHECK(view::transfer_input_focus(root, nullptr, view::FocusCause::keyboard) == false);
    CHECK(blur == 1);
    CHECK(focusout == 1);
}

FRAMEWORK_TEST(native_focus_transfer_reaches_real_widget_bridge) {
    Harness h;
    h.bridge.load_script(
        "globalThis.focusSeen = []; globalThis.focusInSeen = [];"
        "globalThis.blurSeen = []; globalThis.focusOutSeen = [];"
        "var parent = document.createElement('div'); parent.id = 'focus-parent';"
        "var target = document.createElement('input'); target.id = 'focus-target';"
        "target.addEventListener('focus', function () {"
            "focusSeen.push({visible: target._focusVisible, cause: target._focusCause});"
        "});"
        "target.addEventListener('focusin', function () { focusInSeen.push(1); });"
        "target.addEventListener('blur', function () { blurSeen.push(1); });"
        "target.addEventListener('focusout', function () { focusOutSeen.push(1); });"
        "document.body.appendChild(parent); parent.appendChild(target);"
        "var style = document.createElement('style');"
        "style.textContent = '#focus-parent:focus-within { color: red; }';"
        "document.body.appendChild(style);");

    auto* target = h.bridge.widget(h.js_string("target._id"));
    auto* parent = h.bridge.widget(h.js_string("parent._id"));
    CHECK(target != nullptr);
    CHECK(parent != nullptr);

    CHECK(view::transfer_input_focus(h.root, target, view::FocusCause::keyboard));
    CHECK(h.js_string("focusSeen.length") == "1");
    CHECK(h.js_string("focusInSeen.length") == "1");
    CHECK(h.js_string("focusSeen[0].visible") == "true");
    CHECK(h.js_string("focusSeen[0].cause") == "keyboard");
    CHECK(h.js_string("document.querySelector('#focus-parent:focus-within') === parent") == "true");
    CHECK(h.js_string("document.getElementById('focus-parent').style.color") == "red");

    CHECK(!view::transfer_input_focus(h.root, nullptr, view::FocusCause::programmatic));
    CHECK(h.js_string("blurSeen.length") == "1");
    CHECK(h.js_string("focusOutSeen.length") == "1");
    CHECK(h.js_string("document.querySelector('#focus-parent:focus-within') === null") == "true");

    CHECK(view::transfer_input_focus(h.root, target, view::FocusCause::pointer));
    CHECK(h.js_string("focusSeen.length") == "2");
    CHECK(h.js_string("focusInSeen.length") == "2");
    CHECK(h.js_string("focusSeen[1].visible") == "false");
    CHECK(h.js_string("focusSeen[1].cause") == "pointer");
    CHECK(!view::transfer_input_focus(h.root, nullptr, view::FocusCause::pointer));
    CHECK(h.js_string("blurSeen.length") == "2");
    CHECK(h.js_string("focusOutSeen.length") == "2");
}

FRAMEWORK_TEST(element_focus_and_blur_cover_native_and_fallback_paths) {
    Harness h;
    h.bridge.load_script(
        "globalThis.nativeFocus = 0; globalThis.nativeBlur = 0;"
        "globalThis.fallbackFocus = 0; globalThis.fallbackBlur = 0;"
        "var nativeEl = document.createElement('input'); nativeEl.id = 'native-focus';"
        "nativeEl.addEventListener('focus', function () { nativeFocus++; });"
        "nativeEl.addEventListener('blur', function () { nativeBlur++; });"
        "var disabledEl = document.createElement('input'); disabledEl.id = 'disabled-focus';"
        "disabledEl.disabled = true;"
        "disabledEl.addEventListener('focus', function () { fallbackFocus++; });"
        "disabledEl.addEventListener('blur', function () { fallbackBlur++; });"
        "document.body.appendChild(nativeEl); document.body.appendChild(disabledEl);");

    h.engine.evaluate("nativeEl.focus()");
    CHECK(h.js_string("nativeFocus") == "1");
    h.engine.evaluate("nativeEl.blur()");
    CHECK(h.js_string("nativeBlur") == "1");

    h.engine.evaluate("disabledEl.focus()");
    CHECK(h.js_string("fallbackFocus") == "1");
    h.engine.evaluate("disabledEl.blur()");
    CHECK(h.js_string("fallbackBlur") == "1");
}

FRAMEWORK_TEST(disabled_focus_target_leaves_existing_focus_unchanged) {
    view::View root;
    root.set_bounds({0, 0, 200, 100});
    auto focused = std::make_unique<view::View>();
    focused->set_focusable(true);
    auto* focused_ptr = focused.get();
    auto disabled = std::make_unique<view::View>();
    disabled->set_focusable(true);
    disabled->set_enabled(false);
    auto* disabled_ptr = disabled.get();
    root.add_child(std::move(focused));
    root.add_child(std::move(disabled));

    CHECK(view::transfer_input_focus(root, focused_ptr));
    CHECK(view::focused_input_under_root(root) == focused_ptr);
    CHECK(view::transfer_input_focus(root, disabled_ptr));
    CHECK(view::focused_input_under_root(root) == focused_ptr);
}

FRAMEWORK_TEST(failing_script_throws_and_keeps_the_built_tree) {
    Harness h;
    h.bridge.load_script("createLabel('kept', 0, 0, 50, 20);");

    bool threw = false;
    try {
        h.bridge.load_script("createLabel('partial', 0, 30, 50, 20); missingFunction();");
    } catch (const std::runtime_error& error) {
        threw = std::string(error.what()).find("missingFunction") != std::string::npos;
    }
    CHECK(threw);
    CHECK(h.bridge.widget("kept") != nullptr);
    CHECK(h.bridge.widget("partial") != nullptr);
    CHECK(h.root.child_count() == 2);
}

} // namespace

int main() { return framework_test::run_all(); }
