// Checks that a Knob drag honours the fine-adjust modifier: Shift divides the
// travel by the slider core's fine divisor, and toggling it mid-drag does not
// move the value.

#include "framework_test.hpp"

#include <vellum/view/input_events.hpp>
#include <vellum/view/pointer_dispatch.hpp>
#include <vellum/view/slider_core.hpp>
#include <vellum/view/widgets.hpp>

#include <cmath>

namespace {

using vellum::view::Knob;
using vellum::view::Point;
using vellum::view::kModCtrl;
using vellum::view::kModShift;

constexpr float kStartValue = 0.5f;
constexpr float kPixels = 15.0f;
constexpr float kPixelsPerRange = 150.0f;

bool near(double a, double b) { return std::abs(a - b) < 1e-5; }

struct Rig {
    Knob knob;
    Rig() {
        knob.set_bounds({0.0f, 0.0f, 48.0f, 48.0f});
        knob.set_value(kStartValue);
    }
    void press(float y, uint16_t mods = 0) {
        vellum::view::deliver_mouse_down(knob, &knob, {24.0f, y}, mods);
    }
    void drag(float y, uint16_t mods = 0) {
        vellum::view::deliver_mouse_drag(knob, &knob, {24.0f, y}, mods);
    }
    void release(float y) { knob.on_mouse_up({24.0f, y}); }
};

double drag_travel(uint16_t mods) {
    Rig rig;
    rig.press(40.0f, mods);
    rig.drag(40.0f - kPixels, mods);
    rig.release(40.0f - kPixels);
    return rig.knob.value() - kStartValue;
}

FRAMEWORK_TEST(a_plain_drag_moves_the_value_by_the_full_sensitivity) {
    CHECK(near(drag_travel(0), kPixels / kPixelsPerRange));
}

FRAMEWORK_TEST(a_shift_drag_moves_the_value_one_fine_divisor_as_far) {
    const double divisor = vellum::view::SliderCore{}.fine_divisor();
    CHECK(divisor > 1.0);
    CHECK(near(drag_travel(kModShift), drag_travel(0) / divisor));
}

FRAMEWORK_TEST(a_modifier_other_than_shift_does_not_make_the_drag_fine) {
    CHECK(near(drag_travel(kModCtrl), drag_travel(0)));
}

FRAMEWORK_TEST(toggling_shift_mid_drag_does_not_jump_the_value) {
    const double divisor = vellum::view::SliderCore{}.fine_divisor();
    Rig rig;
    rig.press(100.0f);
    rig.drag(90.0f);
    const double plain = rig.knob.value();
    CHECK(near(plain, kStartValue + 10.0 / kPixelsPerRange));

    rig.drag(90.0f, kModShift);
    CHECK(near(rig.knob.value(), plain));

    rig.drag(80.0f, kModShift);
    const double fine = rig.knob.value();
    CHECK(near(fine, plain + 10.0 / (kPixelsPerRange * divisor)));

    rig.drag(80.0f);
    CHECK(near(rig.knob.value(), fine));

    rig.drag(70.0f);
    CHECK(near(rig.knob.value(), fine + 10.0 / kPixelsPerRange));
    rig.release(70.0f);
}

FRAMEWORK_TEST(the_pointer_travel_of_the_tick_that_toggles_shift_is_not_lost) {
    const double divisor = vellum::view::SliderCore{}.fine_divisor();
    Rig rig;
    rig.press(100.0f);
    rig.drag(90.0f);
    const double plain = rig.knob.value();

    rig.drag(80.0f, kModShift);
    CHECK(near(rig.knob.value(), plain + 10.0 / (kPixelsPerRange * divisor)));
    const double fine = rig.knob.value();

    rig.drag(70.0f);
    CHECK(near(rig.knob.value(), fine + 10.0 / kPixelsPerRange));
    rig.release(70.0f);
}

FRAMEWORK_TEST(fine_state_does_not_leak_into_the_next_drag) {
    Rig rig;
    rig.press(40.0f, kModShift);
    rig.drag(25.0f, kModShift);
    rig.release(25.0f);

    const double before = rig.knob.value();
    rig.press(40.0f);
    rig.drag(25.0f);
    rig.release(25.0f);
    CHECK(near(rig.knob.value() - before, kPixels / kPixelsPerRange));
}

}  // namespace

int main() { return framework_test::run_all(); }
