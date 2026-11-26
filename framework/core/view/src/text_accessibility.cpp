// Cross-platform default text accessibility backend.
//
// Platform backends replace this translation unit when native accessibility
// integration is available. The default keeps the same owner-aware registry
// contract so unsupported platforms still provide a complete linkable API.

#include <vellum/view/text_accessibility.hpp>

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace vellum::view {

namespace {

struct Registry {
    std::mutex mu;
    std::unordered_map<std::string, TextAccessibilityNode> nodes;
};

Registry& registry() {
    static Registry r;
    return r;
}

std::string registry_key(const void* owner, std::string_view id) {
    const auto value = reinterpret_cast<std::uintptr_t>(owner);
    return std::to_string(value) + ":" + std::string(id);
}

}  // namespace

std::string_view accessibility_backend_name() noexcept {
    return "none";
}

void register_text_accessibility_node(const TextAccessibilityNode& node) {
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mu);
    r.nodes[registry_key(node.owner, node.id)] = node;
}

void unregister_text_accessibility_node_for_owner(
    const void* owner, std::string_view id) {
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mu);
    for (auto it = r.nodes.begin(); it != r.nodes.end();) {
        if (it->second.owner == owner && it->second.id == id) {
            it = r.nodes.erase(it);
        } else {
            ++it;
        }
    }
}

void unregister_text_accessibility_node(std::string_view id) {
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mu);
    for (auto it = r.nodes.begin(); it != r.nodes.end();) {
        if (it->second.id == id) {
            it = r.nodes.erase(it);
        } else {
            ++it;
        }
    }
}

std::vector<TextAccessibilityNode> snapshot_accessibility_nodes() {
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mu);
    std::vector<TextAccessibilityNode> out;
    out.reserve(r.nodes.size());
    for (const auto& [_, node] : r.nodes) {
        out.push_back(node);
    }
    return out;
}

}  // namespace vellum::view
