#include <vellum/view/text_accessibility.hpp>

#include <string_view>

int main() {
    int owner = 0;
    vellum::view::TextAccessibilityNode node;
    node.id = "owner-link";
    node.text = "owner-link";
    vellum::view::register_text_accessibility_node_for_owner(&owner, node);
    vellum::view::unregister_text_accessibility_node_for_owner(&owner,
                                                                std::string_view("owner-link"));
    return vellum::view::snapshot_accessibility_nodes().empty() ? 0 : 1;
}
