#include <pulp/view/inspector.hpp>
#include <choc/text/choc_JSON.h>
#include <sstream>

namespace pulp::view {

static choc::value::Value view_to_value(const View& view) {
    auto obj = choc::value::createObject("");

    obj.addMember("type", choc::value::createString(ViewInspector::type_name(view)));

    if (!view.id().empty())
        obj.addMember("id", choc::value::createString(view.id()));

    auto bounds = view.bounds();
    auto bounds_obj = choc::value::createObject("");
    bounds_obj.addMember("x", choc::value::createFloat64(bounds.x));
    bounds_obj.addMember("y", choc::value::createFloat64(bounds.y));
    bounds_obj.addMember("width", choc::value::createFloat64(bounds.width));
    bounds_obj.addMember("height", choc::value::createFloat64(bounds.height));
    obj.addMember("bounds", bounds_obj);

    obj.addMember("visible", choc::value::createBool(view.visible()));

    // Widget-specific properties
    if (auto* knob = dynamic_cast<const Knob*>(&view)) {
        obj.addMember("value", choc::value::createFloat64(knob->value()));
        if (!knob->label().empty())
            obj.addMember("label", choc::value::createString(knob->label()));
    } else if (auto* fader = dynamic_cast<const Fader*>(&view)) {
        obj.addMember("value", choc::value::createFloat64(fader->value()));
        obj.addMember("orientation",
            choc::value::createString(
                fader->orientation() == Fader::Orientation::vertical ? "vertical" : "horizontal"));
    } else if (auto* toggle = dynamic_cast<const Toggle*>(&view)) {
        obj.addMember("on", choc::value::createBool(toggle->is_on()));
    } else if (auto* label = dynamic_cast<const Label*>(&view)) {
        obj.addMember("text", choc::value::createString(label->text()));
    } else if (auto* meter = dynamic_cast<const Meter*>(&view)) {
        obj.addMember("rms", choc::value::createFloat64(meter->display_rms()));
        obj.addMember("peak", choc::value::createFloat64(meter->display_peak()));
    } else if (auto* xy = dynamic_cast<const XYPad*>(&view)) {
        obj.addMember("x", choc::value::createFloat64(xy->x_value()));
        obj.addMember("y", choc::value::createFloat64(xy->y_value()));
    } else if (auto* wf = dynamic_cast<const WaveformView*>(&view)) {
        obj.addMember("samples", choc::value::createInt64(static_cast<int64_t>(wf->sample_count())));
    } else if (auto* sp = dynamic_cast<const SpectrumView*>(&view)) {
        obj.addMember("bins", choc::value::createInt64(static_cast<int64_t>(sp->bin_count())));
    }

    // Children
    if (view.child_count() > 0) {
        auto children = choc::value::createEmptyArray();
        for (size_t i = 0; i < view.child_count(); ++i) {
            children.addArrayElement(view_to_value(*view.child_at(i)));
        }
        obj.addMember("children", children);
    }

    return obj;
}

std::string ViewInspector::to_json(const View& root) {
    auto value = view_to_value(root);
    return choc::json::toString(value, true);
}

View* ViewInspector::find_by_id(View& root, const std::string& id) {
    if (root.id() == id) return &root;
    for (size_t i = 0; i < root.child_count(); ++i) {
        if (auto* found = find_by_id(*root.child_at(i), id))
            return found;
    }
    return nullptr;
}

size_t ViewInspector::count_views(const View& root) {
    size_t count = 1;
    for (size_t i = 0; i < root.child_count(); ++i)
        count += count_views(*root.child_at(i));
    return count;
}

std::string ViewInspector::type_name(const View& view) {
    if (dynamic_cast<const Knob*>(&view)) return "Knob";
    if (dynamic_cast<const Fader*>(&view)) return "Fader";
    if (dynamic_cast<const Toggle*>(&view)) return "Toggle";
    if (dynamic_cast<const Label*>(&view)) return "Label";
    if (dynamic_cast<const Meter*>(&view)) return "Meter";
    if (dynamic_cast<const XYPad*>(&view)) return "XYPad";
    if (dynamic_cast<const WaveformView*>(&view)) return "WaveformView";
    if (dynamic_cast<const SpectrumView*>(&view)) return "SpectrumView";
    return "View";
}

} // namespace pulp::view
