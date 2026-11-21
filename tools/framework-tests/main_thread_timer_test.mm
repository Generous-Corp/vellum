#include "framework_test.hpp"

#include <vellum/platform/main_thread.hpp>

#include <CoreFoundation/CoreFoundation.h>

#include <atomic>
#include <chrono>
#include <functional>
#include <memory>
#include <vector>

namespace {

using vellum::platform::MainThreadScope;
using vellum::platform::MainThreadTimer;
using namespace std::chrono_literals;

void pump_for(std::chrono::milliseconds duration) {
    const auto deadline = std::chrono::steady_clock::now() + duration;
    while (std::chrono::steady_clock::now() < deadline)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
}

bool pump_until(const std::function<bool()>& ready, std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (!ready() && std::chrono::steady_clock::now() < deadline)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    return ready();
}

FRAMEWORK_TEST(repeating_timer_does_not_reposition_on_each_fire) {
    MainThreadScope scope;
    constexpr int timer_count = 4;
    std::atomic<int> fires{0};
    std::vector<std::unique_ptr<MainThreadTimer>> timers;
    for (int i = 0; i < timer_count; ++i)
        timers.push_back(std::make_unique<MainThreadTimer>(scope, [&] { ++fires; }));

    const auto before_start = vellum::platform::detail::main_thread_timer_rearms();
    for (auto& timer : timers)
        timer->fire_every(10ms);
    CHECK(vellum::platform::detail::main_thread_timer_rearms() - before_start == timer_count);

    const auto armed = vellum::platform::detail::main_thread_timer_rearms();
    CHECK(pump_until([&] { return fires.load() >= timer_count * 3; }, 2s));
    CHECK(vellum::platform::detail::main_thread_timer_rearms() == armed);
}

FRAMEWORK_TEST(one_shot_reposition_is_the_control) {
    MainThreadScope scope;
    std::atomic<int> fires{0};
    MainThreadTimer timer(scope, [&] { ++fires; });
    const auto before = vellum::platform::detail::main_thread_timer_rearms();
    timer.fire_after(10ms);
    CHECK(vellum::platform::detail::main_thread_timer_rearms() == before + 1);
    CHECK(pump_until([&] { return fires.load() == 1; }, 2s));
    CHECK(!timer.pending());
}

FRAMEWORK_TEST(repeating_timer_survives_period_change_and_stop) {
    MainThreadScope scope;
    std::atomic<int> fires{0};
    MainThreadTimer timer(scope, [&] { ++fires; });

    timer.fire_every(500ms);
    pump_for(30ms);
    CHECK(fires == 0);
    timer.fire_every(10ms);
    CHECK(pump_until([&] { return fires.load() >= 2; }, 2s));
    timer.cancel();
    const int stopped = fires.load();
    pump_for(50ms);
    CHECK(fires == stopped);
}

FRAMEWORK_TEST(one_shot_after_repeating_fires_once) {
    MainThreadScope scope;
    std::atomic<int> fires{0};
    MainThreadTimer timer(scope, [&] { ++fires; });
    timer.fire_every(10ms);
    // A run-loop pump can deliver more than the minimum number of callbacks.
    CHECK(pump_until([&] { return fires.load() >= 5; }, 2s));
    const int before_one_shot = fires.load();
    timer.fire_after(10ms);
    CHECK(pump_until([&] { return fires.load() > before_one_shot; }, 2s));
    pump_for(50ms);
    CHECK(fires == before_one_shot + 1);
    CHECK(!timer.pending());
}

FRAMEWORK_TEST(destroying_repeating_timer_stops_callbacks) {
    MainThreadScope scope;
    std::atomic<int> fires{0};
    {
        MainThreadTimer timer(scope, [&] { ++fires; });
        timer.fire_every(10ms);
        CHECK(pump_until([&] { return fires.load() >= 1; }, 2s));
    }
    const int stopped = fires.load();
    pump_for(50ms);
    CHECK(fires == stopped);
}

} // namespace

int main() {
    return framework_test::run_all();
}
