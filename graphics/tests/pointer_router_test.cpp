#include <vellum/graphics/pointer_router.hpp>

#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

namespace {

using namespace vellum::graphics;
using Kind = PointerEvent::Kind;

int failures = 0;

void check(bool condition, const char* expression, int line) {
    if (condition) return;
    ++failures;
    std::cerr << "pointer_router_test.cpp:" << line << ": check failed: " << expression << "\n";
}

#define CHECK(expression) check((expression), #expression, __LINE__)

struct Layout final {
    std::optional<NodeId> operator()(Point p, PointerTarget target) const {
        if (target == PointerTarget::wheel) {
            if (p.x >= 100.0F && p.x < 200.0F) return NodeId("list");
            return std::nullopt;
        }
        if (p.x >= 0.0F && p.x < 50.0F) return NodeId("a");
        if (p.x >= 60.0F && p.x < 110.0F) return NodeId("b");
        return std::nullopt;
    }
};

std::vector<Kind> kinds(const std::vector<PointerEvent>& events) {
    std::vector<Kind> result;
    for (const auto& event : events) result.push_back(event.kind);
    return result;
}

bool is(const std::vector<PointerEvent>& events, std::vector<Kind> expected) {
    return kinds(events) == expected;
}

void press_and_click() {
    PointerRouter router(Layout{});
    auto events = router.down({10.0F, 10.0F}, PointerButton::primary);
    CHECK(is(events, {Kind::hover_enter, Kind::press}));
    CHECK(events[1].node_id == "a");
    CHECK(router.captured() == "a");
    events = router.up({12.0F, 10.0F}, PointerButton::primary);
    CHECK(is(events, {Kind::release, Kind::click}));
    CHECK(events[1].node_id == "a" && events[1].over);
    CHECK(!router.captured());
    CHECK(router.hovered() == "a");
}

void capture_continues_outside() {
    PointerRouter router(Layout{});
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    auto events = router.move({30.0F, 14.0F});
    CHECK(is(events, {Kind::drag}));
    CHECK(events[0].node_id == "a" && events[0].over);
    CHECK(events[0].delta.x == 20.0F && events[0].delta.y == 4.0F);
    CHECK(events[0].start.x == 10.0F);
    events = router.move({80.0F, 14.0F});
    CHECK(is(events, {Kind::drag}));
    CHECK(events[0].node_id == "a" && !events[0].over);
    CHECK(events[0].delta.x == 50.0F);
    events = router.move({300.0F, 14.0F});
    CHECK(is(events, {Kind::drag}));
    CHECK(events[0].node_id == "a" && !events[0].over);
    CHECK(router.hovered() == "a");
    events = router.up({80.0F, 14.0F}, PointerButton::primary);
    CHECK(is(events, {Kind::release, Kind::hover_leave, Kind::hover_enter}));
    CHECK(events[0].node_id == "a" && !events[0].over);
    CHECK(events[1].node_id == "a" && events[2].node_id == "b");
    CHECK(router.hovered() == "b");
}

void hover_sequences() {
    PointerRouter router(Layout{});
    CHECK(is(router.move({200.0F, 0.0F}), {}));
    auto events = router.move({10.0F, 0.0F});
    CHECK(is(events, {Kind::hover_enter}) && events[0].node_id == "a");
    CHECK(is(router.move({20.0F, 0.0F}), {}));
    events = router.move({70.0F, 0.0F});
    CHECK(is(events, {Kind::hover_leave, Kind::hover_enter}));
    CHECK(events[0].node_id == "a" && events[1].node_id == "b");
    events = router.move({300.0F, 0.0F});
    CHECK(is(events, {Kind::hover_leave}) && events[0].node_id == "b");
    CHECK(!router.hovered());
    (void)router.move({70.0F, 0.0F});
    events = router.leave();
    CHECK(is(events, {Kind::hover_leave}) && events[0].node_id == "b");
    CHECK(is(router.leave(), {}));
}

void release_elsewhere_has_no_click() {
    PointerRouter router(Layout{});
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    const auto events = router.up({80.0F, 10.0F}, PointerButton::primary);
    CHECK(kinds(events).front() == Kind::release);
    for (const auto kind : kinds(events)) CHECK(kind != Kind::click);
}

void empty_press_captures_nothing() {
    PointerRouter router(Layout{});
    CHECK(is(router.down({300.0F, 0.0F}, PointerButton::primary), {}));
    CHECK(!router.captured());
    CHECK(is(router.move({10.0F, 0.0F}), {Kind::hover_enter}));
    CHECK(is(router.up({10.0F, 0.0F}, PointerButton::primary), {}));
}

void wheel_targets() {
    PointerRouter router(Layout{});
    auto events = router.wheel({150.0F, 5.0F}, {0.0F, -12.0F}, {.shift = true});
    CHECK(is(events, {Kind::wheel}));
    CHECK(events[0].node_id == "list" && events[0].delta.y == -12.0F && events[0].modifiers.shift);
    CHECK(is(router.wheel({10.0F, 5.0F}, {0.0F, 1.0F}), {}));
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    events = router.wheel({150.0F, 5.0F}, {3.0F, 0.0F});
    CHECK(is(events, {Kind::wheel}) && events[0].node_id == "list");
    CHECK(router.captured() == "a");
}

void cancel_ends_capture() {
    PointerRouter router(Layout{});
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    (void)router.move({30.0F, 10.0F});
    auto events = router.cancel();
    CHECK(is(events, {Kind::cancel}) && events[0].node_id == "a");
    CHECK(!router.captured());
    CHECK(is(router.move({30.0F, 10.0F}), {}));
    CHECK(is(router.up({30.0F, 10.0F}, PointerButton::primary), {}));
    CHECK(is(router.cancel(), {}));
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    events = router.leave();
    CHECK(is(events, {Kind::cancel, Kind::hover_leave}));
    CHECK(!router.captured() && !router.hovered());
}

void drag_delta_survives_other_input() {
    PointerRouter router(Layout{});
    (void)router.down({10.0F, 10.0F}, PointerButton::primary);
    auto events = router.move({20.0F, 10.0F});
    CHECK(events[0].delta.x == 10.0F);
    (void)router.down({30.0F, 10.0F}, PointerButton::secondary);
    (void)router.wheel({150.0F, 5.0F}, {0.0F, 1.0F});
    (void)router.up({35.0F, 10.0F}, PointerButton::secondary);
    events = router.move({40.0F, 10.0F});
    CHECK(is(events, {Kind::drag}));
    CHECK(events[0].delta.x == 20.0F);
}

void buttons_and_modifiers() {
    PointerRouter router(Layout{});
    auto events = router.down({10.0F, 10.0F}, PointerButton::primary, {.control = true});
    CHECK(events.back().modifiers.control && events.back().button == PointerButton::primary);
    events = router.down({10.0F, 10.0F}, PointerButton::secondary);
    CHECK(is(events, {Kind::press}) && events[0].button == PointerButton::secondary);
    events = router.up({10.0F, 10.0F}, PointerButton::secondary);
    CHECK(is(events, {Kind::release}));
    CHECK(router.captured() == "a");
    events = router.up({10.0F, 10.0F}, PointerButton::primary, {.alt = true});
    CHECK(is(events, {Kind::release, Kind::click}) && events[1].modifiers.alt);
    CHECK(!router.captured());
}

}  // namespace

int main() {
    press_and_click();
    capture_continues_outside();
    hover_sequences();
    release_elsewhere_has_no_click();
    empty_press_captures_nothing();
    wheel_targets();
    cancel_ends_capture();
    buttons_and_modifiers();
    drag_delta_survives_other_input();
    if (failures != 0) {
        std::cerr << failures << " checks failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "pointer_router_test passed\n";
    return EXIT_SUCCESS;
}
