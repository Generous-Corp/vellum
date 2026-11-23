// The Linux AT-SPI provider exports one D-Bus object per accessible View. This
// test builds a known View tree, exports it on the session bus through the
// provider's test seam, and acts as an AT-SPI client: it walks
// Accessible.GetChildren from the root, reads each node's role, and reads
// Component.GetExtents for one widget. It needs a session bus, so it exits 77
// (skipped) without one; run it under `dbus-run-session`.
//
// Negative controls, in the same executable so one run holds both:
//   - a node path the provider never exported must fail to read, so the client
//     helper can report a failure at all;
//   - after the provider shuts down, the root must no longer answer;
//   - a tree with one more accessible widget must report one more child, so the
//     child-count check is not a constant.

#include "framework_test.hpp"

#include <vellum/platform/dbus.hpp>
#include <vellum/view/accessibility.hpp>
#include <vellum/view/accessibility_provider.hpp>
#include <vellum/view/platform/atspi_mapping.hpp>
#include <vellum/view/view.hpp>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#if defined(__linux__) && !defined(__ANDROID__)

namespace {

namespace view = vellum::view;
namespace atspi = vellum::view::atspi;
using vellum::platform::DBus;

constexpr const char* kRootPath = "/org/a11y/atspi/accessible/root";
constexpr const char* kIfaceAccessible = "org.a11y.atspi.Accessible";
constexpr const char* kIfaceComponent = "org.a11y.atspi.Component";

struct WireNode {
    std::string path;
    unsigned role = 0;
    std::string role_name;
    std::vector<std::string> child_paths;
};

// The client blocks on a worker thread while this thread pumps the provider,
// because a blocking call pumps only the client's own connection.
template <typename CallFn>
bool run_client_call(void* provider_handle, CallFn&& call_fn) {
    std::atomic<bool> done{false};
    std::atomic<bool> ok{false};
    std::thread worker([&] {
        ok = call_fn();
        done = true;
    });
    for (int i = 0; i < 600 && !done.load(); ++i) {
        view::accessibility_pump(provider_handle);
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (!done.load()) {
        // The call never returned. Joining would hang the run, and the worker
        // holds references into this frame, so it cannot be detached either:
        // end the process with a named failure instead.
        std::fprintf(stderr, "FAIL bounded client call expired: an AT-SPI client call did not return\n");
        std::fflush(stderr);
        std::_Exit(3);
    }
    worker.join();
    return ok.load();
}

bool read_node(const std::string& server, const std::string& path, WireNode& out) {
    DBus client;
    if (!client.connect_session())
        return false;
    const bool got_role = client.call_method(
        server, path, kIfaceAccessible, "GetRole", [](DBus::Writer&) {},
        [&](DBus::Reader& r) { r.read_uint32(out.role); });
    const bool got_name = client.call_method(
        server, path, kIfaceAccessible, "GetRoleName", [](DBus::Writer&) {},
        [&](DBus::Reader& r) { r.read_string(out.role_name); });
    const bool got_children = client.call_method(
        server, path, kIfaceAccessible, "GetChildren", [](DBus::Writer&) {},
        [&](DBus::Reader& r) {
            DBus::Reader array;
            if (!r.recurse(array))
                return;
            while (array.arg_type() != 0) {
                DBus::Reader entry;
                if (array.recurse(entry)) {
                    std::string name, child_path;
                    entry.read_string(name);
                    entry.read_string(child_path);
                    out.child_paths.push_back(child_path);
                }
                if (!array.next())
                    break;
            }
        });
    out.path = path;
    return got_role && got_name && got_children;
}

std::unique_ptr<view::View> labelled(view::View::AccessRole role, const char* label,
                                     view::Rect bounds) {
    auto child = std::make_unique<view::View>();
    child->set_access_role(role);
    child->set_access_label(label);
    child->set_bounds(bounds);
    return child;
}

// root (none)
//   Title (label)
//   Panel (group)
//     container (none)
//       Gain (slider)     re-parents onto Panel
//   Footer (label)
struct Tree {
    view::View root;
    view::View* gain = nullptr;

    explicit Tree(bool extra_label = false) {
        root.set_bounds({0, 0, 400, 300});
        auto panel = labelled(view::View::AccessRole::group, "Panel", {10, 40, 380, 200});
        auto container = std::make_unique<view::View>();
        container->set_bounds({5, 5, 370, 190});
        auto slider = labelled(view::View::AccessRole::slider, "Gain", {20, 30, 200, 24});
        gain = slider.get();
        container->add_child(std::move(slider));
        panel->add_child(std::move(container));
        root.add_child(labelled(view::View::AccessRole::label, "Title", {10, 10, 100, 20}));
        root.add_child(std::move(panel));
        root.add_child(labelled(view::View::AccessRole::label, "Footer", {10, 250, 100, 20}));
        if (extra_label)
            root.add_child(labelled(view::View::AccessRole::label, "Extra", {10, 275, 100, 20}));
    }
};

FRAMEWORK_TEST(exported_tree_has_the_roles_and_nesting_of_the_view_tree) {
    Tree tree;
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    CHECK(!server.empty());
    if (!handle)
        return;

    WireNode root_node, title, panel, gain, footer;
    const bool walked = run_client_call(handle, [&] {
        if (!read_node(server, kRootPath, root_node) || root_node.child_paths.size() != 3)
            return false;
        return read_node(server, root_node.child_paths[0], title) &&
               read_node(server, root_node.child_paths[1], panel) &&
               read_node(server, root_node.child_paths[2], footer) &&
               panel.child_paths.size() == 1 && read_node(server, panel.child_paths[0], gain);
    });
    CHECK(walked);

    CHECK(root_node.role == atspi::kRoleApplication);
    CHECK(root_node.role_name == "application");
    CHECK(title.role == atspi::role_to_atspi_role(view::View::AccessRole::label));
    CHECK(title.child_paths.empty());
    CHECK(panel.role == atspi::role_to_atspi_role(view::View::AccessRole::group));
    CHECK(panel.child_paths.size() == 1);
    // The none-role container is skipped: the slider sits directly under Panel.
    CHECK(gain.role == atspi::role_to_atspi_role(view::View::AccessRole::slider));
    CHECK(gain.role_name == "slider");
    CHECK(footer.role == atspi::role_to_atspi_role(view::View::AccessRole::label));

    // The slider's screen extents are its bounds summed up the View chain:
    // (20,30) + (5,5) + (10,40) = (35,75), size 200x24.
    int x = 0, y = 0, w = 0, h = 0;
    const bool extents = run_client_call(handle, [&] {
        DBus client;
        if (!client.connect_session())
            return false;
        return client.call_method(
            server, gain.path, kIfaceComponent, "GetExtents",
            [](DBus::Writer& writer) { writer.append_uint32(1u); },  // window coordinates
            [&](DBus::Reader& r) {
                DBus::Reader fields;
                if (r.recurse(fields)) {
                    fields.read_int32(x);
                    fields.read_int32(y);
                    fields.read_int32(w);
                    fields.read_int32(h);
                }
            });
    });
    CHECK(extents);
    CHECK(x == 35 && y == 75 && w == 200 && h == 24);

    view::shutdown_accessibility(handle);
}

struct Extents {
    bool ok = false;
    int x = 0, y = 0, w = 0, h = 0;
};

Extents get_extents(void* handle, const std::string& server, const std::string& path,
                    unsigned coord_type) {
    Extents out;
    out.ok = run_client_call(handle, [&] {
        DBus client;
        if (!client.connect_session())
            return false;
        return client.call_method(
            server, path, kIfaceComponent, "GetExtents",
            [&](DBus::Writer& writer) { writer.append_uint32(coord_type); },
            [&](DBus::Reader& r) {
                DBus::Reader fields;
                if (r.recurse(fields)) {
                    fields.read_int32(out.x);
                    fields.read_int32(out.y);
                    fields.read_int32(out.w);
                    fields.read_int32(out.h);
                }
            });
    });
    return out;
}

// The slider's wire path: the root's second child is Panel, whose only child is Gain.
bool find_gain(void* handle, const std::string& server, std::string& gain_path) {
    WireNode root_node, panel;
    return run_client_call(handle, [&] {
        if (!read_node(server, kRootPath, root_node) || root_node.child_paths.size() != 3)
            return false;
        if (!read_node(server, root_node.child_paths[1], panel) || panel.child_paths.size() != 1)
            return false;
        gain_path = panel.child_paths[0];
        return true;
    });
}

// A screen request is refused until the host reports where the window sits, and
// the geometry it reports decides every answer after that.
FRAMEWORK_TEST(screen_coordinates_need_the_window_origin_and_follow_the_viewport) {
    Tree tree;
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    if (!handle)
        return;
    std::string gain;
    CHECK(find_gain(handle, server, gain));

    // No origin reported: the screen space is refused; window space still works.
    CHECK(!get_extents(handle, server, gain, 0u).ok);
    const Extents window = get_extents(handle, server, gain, 1u);
    CHECK(window.ok);
    CHECK(window.x == 35 && window.y == 75 && window.w == 200 && window.h == 24);

    // A window at (100,200) whose root fills it.
    view::accessibility_set_window_geometry(handle, 100, 200, 1.0f, 0.0f, 0.0f);
    const Extents screen = get_extents(handle, server, gain, 0u);
    CHECK(screen.ok);
    CHECK(screen.x == 135 && screen.y == 275 && screen.w == 200 && screen.h == 24);

    // The same window showing the root at 2x, centred (offset 100,50).
    view::accessibility_set_window_geometry(handle, 100, 200, 2.0f, 100.0f, 50.0f);
    const Extents scaled = get_extents(handle, server, gain, 1u);
    CHECK(scaled.ok);
    CHECK(scaled.x == 170 && scaled.y == 200 && scaled.w == 400 && scaled.h == 48);
    const Extents scaled_screen = get_extents(handle, server, gain, 0u);
    CHECK(scaled_screen.ok);
    CHECK(scaled_screen.x == 270 && scaled_screen.y == 400);

    view::shutdown_accessibility(handle);
}

// Component.Contains answers a boolean, read back with DBus::Reader::read_bool.
// The slider's window rectangle is (35,75) 200x24, so (40,80) is inside and
// (500,500) is outside; the two answers differ, so the reader cannot be
// returning a constant.
FRAMEWORK_TEST(contains_reads_back_as_a_boolean) {
    Tree tree;
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    if (!handle)
        return;
    WireNode root_node, panel, gain;
    const bool found = run_client_call(handle, [&] {
        return read_node(server, kRootPath, root_node) && root_node.child_paths.size() == 3 &&
               read_node(server, root_node.child_paths[1], panel) && panel.child_paths.size() == 1 &&
               read_node(server, panel.child_paths[0], gain);
    });
    CHECK(found);
    auto contains = [&](int x, int y, bool& answer) {
        return run_client_call(handle, [&] {
            DBus client;
            if (!client.connect_session())
                return false;
            return client.call_method(
                server, gain.path, kIfaceComponent, "Contains",
                [&](DBus::Writer& writer) {
                    writer.append_int32(x);
                    writer.append_int32(y);
                    writer.append_uint32(1u);  // window coordinates
                },
                [&](DBus::Reader& r) { r.read_bool(answer); });
        });
    };
    bool inside = false, outside = true;
    CHECK(contains(40, 80, inside));
    CHECK(contains(500, 500, outside));
    CHECK(inside);
    CHECK(!outside);
    view::shutdown_accessibility(handle);
}

FRAMEWORK_TEST(control_an_unexported_path_cannot_be_read) {
    Tree tree;
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    if (!handle)
        return;
    WireNode missing;
    const bool read = run_client_call(handle, [&] {
        return read_node(server, "/org/a11y/atspi/accessible/not_exported", missing);
    });
    CHECK(!read);
    view::shutdown_accessibility(handle);
}

FRAMEWORK_TEST(control_the_root_stops_answering_after_shutdown) {
    Tree tree;
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    if (!handle)
        return;
    WireNode before;
    CHECK(run_client_call(handle, [&] { return read_node(server, kRootPath, before); }));
    view::shutdown_accessibility(handle);
    WireNode after;
    // No provider remains to pump, so only the client can answer: it must fail.
    std::atomic<bool> ok{true};
    std::thread worker([&] { ok = read_node(server, kRootPath, after); });
    worker.join();
    CHECK(!ok.load());
}

FRAMEWORK_TEST(control_one_more_accessible_widget_adds_one_child) {
    Tree tree(true);
    std::string server;
    void* handle = view::init_accessibility_on_session_bus_for_test(tree.root, &server);
    CHECK(handle != nullptr);
    if (!handle)
        return;
    WireNode root_node;
    CHECK(run_client_call(handle, [&] { return read_node(server, kRootPath, root_node); }));
    CHECK(root_node.child_paths.size() == 4);
    view::shutdown_accessibility(handle);
}

} // namespace

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--control-hang") == 0) {
        // A client call that never returns must end the run with the bounded-call
        // failure, not hang it. No provider and no bus are needed.
        run_client_call(nullptr, [] {
            std::this_thread::sleep_for(std::chrono::seconds(60));
            return true;
        });
        std::printf("the call returned: the bound did not fire\n");
        return 1;
    }
    if (!DBus::library_available()) {
        std::printf("SKIP libdbus is not available\n");
        return 77;
    }
    {
        DBus probe;
        if (!probe.connect_session()) {
            std::printf("SKIP no session bus (run under dbus-run-session)\n");
            return 77;
        }
    }
    return framework_test::run_all();
}

#else

int main() {
    std::printf("SKIP the AT-SPI provider is Linux only\n");
    return 77;
}

#endif
