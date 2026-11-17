// Drives a HostParamSurface over a vellum::value::ParamModel, and a design
// frame view bound through it: the view's knob writes the model as one
// gesture, and sync_from_host_params() shows the model's value and text.

#include "framework_test.hpp"

#include <vellum/dataplane/host_param_surface.hpp>
#include <vellum/value/in_memory_param_model.hpp>
#include <vellum/view/design_frame_view.hpp>

#include <cmath>
#include <string>
#include <utility>
#include <vector>

namespace {

namespace value = vellum::value;
namespace view = vellum::view;
using vellum::dataplane::HostParamSurface;
using vellum::dataplane::ParamModelHostParamSurface;

value::InMemoryParamModel make_model() {
    std::vector<value::ParamSpec> specs;
    specs.push_back({.key = "cutoff", .unit = "Hz", .plain_min = 20.0, .plain_max = 20000.0,
                     .default_normalized = 0.25});
    specs.push_back({.key = "wave", .default_normalized = 0.0, .value_count = 4,
                     .value_labels = {"sine", "tri", "saw", "square"}});
    return value::InMemoryParamModel(std::move(specs));
}

bool near(double a, double b) { return std::abs(a - b) < 1e-6; }

FRAMEWORK_TEST(the_surface_reads_and_writes_the_model) {
    auto model = make_model();
    ParamModelHostParamSurface concrete(model);
    HostParamSurface& surface = concrete;
    const auto cutoff = model.resolve("cutoff");

    CHECK(surface.has_param("cutoff"));
    CHECK(!surface.has_param("missing"));
    CHECK(near(surface.get_param("cutoff"), 0.25));
    surface.set_param("cutoff", 0.75);
    CHECK(near(model.normalized(cutoff), 0.75));
    model.set_normalized(cutoff, 0.5);
    CHECK(near(surface.get_param("cutoff"), 0.5));

    CHECK(surface.param_display_text("cutoff", 0.5) == model.display_text(cutoff, 0.5));
    CHECK(surface.param_display_text("wave", 1.0) == "square");
    CHECK(surface.param_step_count("wave") == 4);
    CHECK(surface.param_step_count("cutoff") == 0);

    // An unknown key writes nothing and reads as nothing.
    surface.set_param("missing", 0.9);
    CHECK(surface.get_param("missing") == 0.0);
    CHECK(surface.param_step_count("missing") == 0);
    CHECK(surface.param_display_text("missing", 0.5).empty());
}

FRAMEWORK_TEST(surface_gestures_are_the_model_gestures) {
    auto model = make_model();
    int begins = 0;
    int ends = 0;
    model.set_gesture_observer([&](value::ParamHandle, bool begun) { (begun ? begins : ends) += 1; });
    ParamModelHostParamSurface surface(model);
    surface.begin_gesture("cutoff");
    surface.set_param("cutoff", 0.4);
    surface.set_param("cutoff", 0.6);
    surface.end_gesture("cutoff");
    CHECK(begins == 1);
    CHECK(ends == 1);
    surface.begin_gesture("missing");
    surface.end_gesture("missing");
    CHECK(begins == 1);
}

view::DesignFrameElement knob_for(std::string key) {
    view::DesignFrameElement e;
    e.kind = view::DesignFrameElement::Kind::knob;
    e.cx = 50.0f;
    e.cy = 50.0f;
    e.hit_radius = 30.0f;
    e.x = 20.0f;
    e.y = 20.0f;
    e.w = 60.0f;
    e.h = 60.0f;
    e.param_key = std::move(key);
    return e;
}

FRAMEWORK_TEST(a_design_view_drives_the_model_through_the_surface) {
    auto model = make_model();
    int begins = 0;
    int ends = 0;
    model.set_gesture_observer([&](value::ParamHandle, bool begun) { (begun ? begins : ends) += 1; });
    ParamModelHostParamSurface surface(model);
    view::DesignFrameView frame(
        "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='100'>"
        "<rect x='0' y='0' width='200' height='100' fill='#111'/></svg>",
        {knob_for("cutoff")}, 0.0f, 0.0f, 200.0f, 100.0f);
    frame.set_bounds({0.0f, 0.0f, 200.0f, 100.0f});
    frame.set_host_params(&surface);
    frame.route_changes_to_host_params(true);

    // Host to view: the model's value and text reach the element at sync.
    const auto cutoff = model.resolve("cutoff");
    model.set_normalized(cutoff, 0.6);
    frame.sync_from_host_params();
    CHECK(near(frame.element_value(0), 0.6));

    // View to host: a drag on the knob is one model gesture that moves it.
    frame.simulate_drag({50.0f, 60.0f}, {50.0f, 20.0f}, 10);
    CHECK(begins == 1);
    CHECK(ends == 1);
    CHECK(!near(model.normalized(cutoff), 0.6));
    frame.sync_from_host_params();
    CHECK(near(frame.element_value(0), model.normalized(cutoff)));
}

}  // namespace

int main() { return framework_test::run_all(); }
