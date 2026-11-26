#include <vellum/view/text_accessibility.hpp>

#include <algorithm>
#include <string_view>

namespace {

using vellum::view::TextAccessibilityNode;
using vellum::view::snapshot_accessibility_nodes;

bool contains_owner(const void* owner, std::string_view id) {
    const auto nodes = snapshot_accessibility_nodes();
    return std::any_of(nodes.begin(), nodes.end(), [=](const auto& node) {
        return node.owner == owner && node.id == id;
    });
}

}  // namespace

int main() {
    int first_owner = 0;
    int second_owner = 0;

    TextAccessibilityNode first;
    first.id = "same-id";
    first.text = "first";
    vellum::view::register_text_accessibility_node_for_owner(&first_owner, first);

    TextAccessibilityNode second;
    second.id = "same-id";
    second.text = "second";
    vellum::view::register_text_accessibility_node_for_owner(&second_owner, second);

    if (!contains_owner(&first_owner, "same-id") ||
        !contains_owner(&second_owner, "same-id"))
        return 1;

    vellum::view::unregister_text_accessibility_node_for_owner(&first_owner, "same-id");
    if (contains_owner(&first_owner, "same-id") ||
        !contains_owner(&second_owner, "same-id"))
        return 2;

    vellum::view::unregister_text_accessibility_node("same-id");
    if (contains_owner(&second_owner, "same-id"))
        return 3;
    return 0;
}
