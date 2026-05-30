#include "import_validation_bridge.hpp"

#include <pulp/view/ui_components.hpp>  // ScrollView

#include <algorithm>
#include <string>
#include <vector>

namespace pulp::view {

// --- moved verbatim from widget_bridge.cpp ---------------------------------

choc::value::Value make_layout_rect_value(View* v) {
    auto result = choc::value::createObject("");
    if (!v) return result;

    float ax = 0.0f;
    float ay = 0.0f;
    View* cur = v;
    while (cur) {
        ax += cur->bounds().x;
        ay += cur->bounds().y;
        if (auto* scroll = dynamic_cast<ScrollView*>(cur->parent())) {
            ax -= scroll->scroll_x();
            ay -= scroll->scroll_y();
        }
        cur = cur->parent();
    }

    auto b = v->bounds();
    result.addMember("x", choc::value::createFloat64(ax));
    result.addMember("y", choc::value::createFloat64(ay));
    result.addMember("width", choc::value::createFloat64(b.width));
    result.addMember("height", choc::value::createFloat64(b.height));
    result.addMember("top", choc::value::createFloat64(ay));
    result.addMember("left", choc::value::createFloat64(ax));
    result.addMember("right", choc::value::createFloat64(ax + b.width));
    result.addMember("bottom", choc::value::createFloat64(ay + b.height));
    return result;
}

static std::string layout_trace_id(const View& v) {
    if (!v.anchor_id().empty()) return v.anchor_id();
    if (!v.id().empty()) return v.id();
    return {};
}

choc::value::Value make_layout_ancestor_chain_value(View* v) {
    auto result = choc::value::createEmptyArray();
    if (!v) return result;

    std::vector<View*> chain;
    for (auto* cur = v; cur != nullptr; cur = cur->parent())
        chain.push_back(cur);
    std::reverse(chain.begin(), chain.end());

    for (auto* cur : chain) {
        auto id = layout_trace_id(*cur);
        if (id.empty()) continue;
        auto entry = choc::value::createObject("");
        entry.addMember("id", id);
        entry.addMember("bounds", make_layout_rect_value(cur));
        result.addArrayElement(entry);
    }
    return result;
}

// --- moved verbatim from claude_bundle.cpp ---------------------------------

void layout_runtime_snapshot_root_if_requested(View& root, const ClaudeRuntimeOptions& opts) {
    if (opts.runtime_snapshot_viewport_width <= 0 || opts.runtime_snapshot_viewport_height <= 0)
        return;

    root.set_bounds({0.0f,
                     0.0f,
                     static_cast<float>(opts.runtime_snapshot_viewport_width),
                     static_cast<float>(opts.runtime_snapshot_viewport_height)});
    root.layout_children();
}

}  // namespace pulp::view
