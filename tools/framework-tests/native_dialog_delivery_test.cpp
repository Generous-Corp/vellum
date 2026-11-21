// Exercise the public asynchronous APIs through Pulp's presenter and backend
// test seams. Core Foundation dispatches the completions without an AppKit window.

#include "framework_test.hpp"

#include <vellum/platform/alert.hpp>
#include <vellum/platform/file_dialog.hpp>
#include <vellum/platform/main_thread.hpp>
#include <vellum/platform/popup_menu.hpp>

#include <CoreFoundation/CoreFoundation.h>

#include <chrono>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

using namespace vellum::platform;
using namespace std::chrono_literals;

bool drain_main_thread(MainThreadScope& scope) {
    auto drained = std::make_shared<bool>(false);
    if (!scope.post([drained] { *drained = true; }))
        return false;
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!*drained && std::chrono::steady_clock::now() < deadline)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    return *drained;
}

FRAMEWORK_TEST(menu_delivers_the_first_choice_after_the_presenter_returns) {
    std::function<void(std::optional<int>)> respond;
    MainThreadScope scope;
    int calls = 0;
    std::optional<int> choice;
    set_menu_presenter({[&](const MenuAnchor&, const MenuModel&, MenuPlacement,
                            std::function<void(std::optional<int>)> callback) {
                            respond = std::move(callback);
                        }, {}});
    auto session = show_menu_async(scope, {}, {}, MenuPlacement::below_anchor,
                                   [&](std::optional<int> id) {
                                       ++calls;
                                       choice = id;
                                   });
    set_menu_presenter({});

    CHECK(session.open());
    CHECK(calls == 0);
    respond(12);
    respond(10);
    CHECK(calls == 0);
    CHECK(drain_main_thread(scope));
    CHECK(calls == 1);
    CHECK(choice == 12);
    CHECK(!session.open());
}

FRAMEWORK_TEST(alert_maps_the_first_button_and_defers_delivery) {
    std::function<void(long)> respond;
    MainThreadScope scope;
    int calls = 0;
    AlertResult result = AlertResult::unavailable;
    set_alert_presenter({[&](const AlertRequest&, const AlertButtonPlan&,
                             std::function<void(long)> callback) {
                             respond = std::move(callback);
                         }, {}});
    AlertRequest request;
    request.buttons = AlertButtons::ok_cancel;
    auto session = show_alert_async(scope, nullptr, request, [&](AlertResult value) {
        ++calls;
        result = value;
        CHECK(is_main_thread());
    });
    set_alert_presenter({});

    CHECK(session.open());
    CHECK(calls == 0);
    respond(1);
    respond(0);
    CHECK(calls == 0);
    CHECK(drain_main_thread(scope));
    CHECK(calls == 1);
    CHECK(result == AlertResult::cancel);
    CHECK(!session.open());
}

FRAMEWORK_TEST(file_open_delivers_a_worker_response_on_the_main_thread) {
    using Paths = std::vector<std::string>;
    std::function<void(Paths)> respond;
    MainThreadScope scope;
    int calls = 0;
    Paths chosen;
    FileDialog::AsyncBackend backend;
    backend.open = [&](const FileDialog::OpenRequest&, std::function<void(Paths)> callback) {
        respond = std::move(callback);
    };
    FileDialog::set_async_backend(std::move(backend));
    auto session = FileDialog::open_async(scope, nullptr, {}, [&](Paths paths) {
        ++calls;
        chosen = std::move(paths);
        CHECK(is_main_thread());
    });
    FileDialog::clear_async_backend();

    CHECK(session.open());
    CHECK(calls == 0);
    if (respond) {
        std::thread worker([&] {
            respond({"/tmp/a.syx"});
            respond({"/tmp/b.syx"});
        });
        worker.join();
    }
    CHECK(calls == 0);
    CHECK(drain_main_thread(scope));
    CHECK(calls == 1);
    CHECK(chosen == Paths{"/tmp/a.syx"});
    CHECK(!session.open());
}

FRAMEWORK_TEST(file_save_cancels_on_scope_teardown_and_drops_the_late_response) {
    using Path = std::optional<std::string>;
    std::function<void(Path)> respond;
    int calls = 0;
    int cancels = 0;
    {
        MainThreadScope scope;
        FileDialog::AsyncBackend backend;
        backend.save = [&](const FileDialog::SaveRequest&, std::function<void(Path)> callback) {
            respond = std::move(callback);
        };
        backend.cancel = [&] { ++cancels; };
        FileDialog::set_async_backend(std::move(backend));
        auto session = FileDialog::save_async(scope, nullptr, {}, [&](Path) { ++calls; });
        FileDialog::clear_async_backend();
        CHECK(session.open());
        CHECK(calls == 0);
    }

    CHECK(cancels == 1);
    respond("/tmp/late.syx");
    MainThreadScope scope;
    CHECK(drain_main_thread(scope));
    CHECK(calls == 0);
}

} // namespace

int main() { return framework_test::run_all(); }
