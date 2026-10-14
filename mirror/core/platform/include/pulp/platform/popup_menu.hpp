#pragma once

#include <string>
#include <vector>
#include <functional>
#include <optional>

namespace pulp::platform {

// Native popup/context menu
class PopupMenu {
public:
    struct Item {
        int id = 0;
        std::string label;
        bool enabled = true;
        bool checked = false;
        bool is_separator = false;

        static Item separator() { return {0, "", true, false, true}; }
    };

    PopupMenu() = default;

    void add_item(int id, const std::string& label, bool enabled = true, bool checked = false) {
        items_.push_back({id, label, enabled, checked, false});
    }

    void add_separator() {
        items_.push_back(Item::separator());
    }

    const std::vector<Item>& items() const { return items_; }

    // Show at screen coordinates. Returns selected item ID or nullopt.
    std::optional<int> show(float x, float y) const;

    // Show attached to a view (uses view's screen position)
    std::optional<int> show_at_view(void* native_view_handle) const;

private:
    std::vector<Item> items_;
};

} // namespace pulp::platform
