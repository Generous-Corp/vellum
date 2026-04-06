#pragma once

#include <pulp/view/view.hpp>
#include <pulp/view/widgets.hpp>
#include <string>

namespace pulp::view {

// Introspects a View tree and produces a JSON representation
// Used by the component inspector and for debugging
class ViewInspector {
public:
    // Serialize the entire view tree to formatted JSON
    static std::string to_json(const View& root);

    // Find a view by ID in the tree
    static View* find_by_id(View& root, const std::string& id);

    // Count total views in the tree
    static size_t count_views(const View& root);

    // Get the type name of a view (e.g., "Knob", "Fader", "View")
    static std::string type_name(const View& view);
};

} // namespace pulp::view
