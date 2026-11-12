#include <vellum/graphics/token_receipts.hpp>

#include <bit>
#include <cmath>
#include <unordered_map>
#include <unordered_set>

namespace vellum::graphics {
namespace {

bool same_bits(float a, float b) noexcept {
    return std::bit_cast<std::uint32_t>(a) == std::bit_cast<std::uint32_t>(b);
}

bool finite_color(const Color& color) noexcept {
    return std::isfinite(color.red) && std::isfinite(color.green) &&
           std::isfinite(color.blue) && std::isfinite(color.alpha);
}

std::optional<TokenRefusal> check_node(const Scene& scene, const SceneNode& node) {
    if (node.fill_token) {
        if (*node.fill_token >= scene.tokens.size()) {
            return TokenRefusal{node.id, "node " + node.id + " references token " +
                                             std::to_string(*node.fill_token) +
                                             ", which the scene's token table does not hold"};
        }
        const auto& receipt = scene.tokens[*node.fill_token];
        if (!same_token_color(node.fill, receipt.value)) {
            return TokenRefusal{node.id, "node " + node.id + " fill differs from its token " +
                                             receipt.path};
        }
    }
    for (const auto& child : node.children) {
        if (auto found = check_node(scene, child)) return found;
    }
    return std::nullopt;
}

std::size_t count_node(const SceneNode& node, const std::unordered_set<std::uint32_t>& entries) {
    std::size_t count = node.fill_token && entries.contains(*node.fill_token) ? 1U : 0U;
    for (const auto& child : node.children) count += count_node(child, entries);
    return count;
}

std::size_t rewrite_node(SceneNode& node, const std::unordered_map<std::uint32_t, Color>& values) {
    std::size_t count = 0;
    if (node.fill_token) {
        if (const auto found = values.find(*node.fill_token); found != values.end()) {
            for (auto& run : node.text_runs) {
                if (same_token_color(run.style.color, node.fill)) run.style.color = found->second;
            }
            node.fill = found->second;
            ++count;
        }
    }
    for (auto& child : node.children) count += rewrite_node(child, values);
    return count;
}

}  // namespace

bool same_token_color(const Color& a, const Color& b) noexcept {
    return same_bits(a.red, b.red) && same_bits(a.green, b.green) &&
           same_bits(a.blue, b.blue) && same_bits(a.alpha, b.alpha);
}

std::optional<TokenRefusal> find_invalid_token_reference(const Scene& scene) {
    std::unordered_set<std::string> paths;
    for (const auto& receipt : scene.tokens) {
        if (!paths.insert(receipt.path).second) {
            return TokenRefusal{scene.root.id,
                                "the token table holds " + receipt.path + " more than once"};
        }
        if (!finite_color(receipt.value)) {
            return TokenRefusal{scene.root.id,
                                "token " + receipt.path + " has a value that is not finite"};
        }
    }
    return check_node(scene, scene.root);
}

std::vector<std::uint32_t> changed_token_entries(const Scene& previous, const Scene& next) {
    std::vector<std::uint32_t> changed;
    for (std::size_t index = 0; index < next.tokens.size(); ++index) {
        const bool same = index < previous.tokens.size() &&
            previous.tokens[index].path == next.tokens[index].path &&
            previous.tokens[index].layer == next.tokens[index].layer &&
            same_token_color(previous.tokens[index].value, next.tokens[index].value);
        if (!same) changed.push_back(static_cast<std::uint32_t>(index));
    }
    return changed;
}

std::size_t count_token_references(const Scene& scene, std::span<const std::uint32_t> entries) {
    const std::unordered_set<std::uint32_t> wanted(entries.begin(), entries.end());
    return count_node(scene.root, wanted);
}

TokenSwitch apply_token_values(Scene& scene, std::span<const TokenReceipt> resolved) {
    TokenSwitch result;
    std::unordered_map<std::string, const TokenReceipt*> by_path;
    for (const auto& receipt : resolved) by_path.emplace(receipt.path, &receipt);
    for (const auto& entry : scene.tokens) {
        if (!by_path.contains(entry.path)) {
            result.missing_path = entry.path;
            return result;
        }
    }
    std::unordered_map<std::uint32_t, Color> moved;
    for (std::size_t index = 0; index < scene.tokens.size(); ++index) {
        auto& entry = scene.tokens[index];
        const auto& next = *by_path.at(entry.path);
        const bool value_changed = !same_token_color(entry.value, next.value);
        if (value_changed || entry.layer != next.layer) {
            result.changed.push_back(static_cast<std::uint32_t>(index));
        }
        if (value_changed) moved.emplace(static_cast<std::uint32_t>(index), next.value);
        entry = next;
    }
    result.nodes_updated = moved.empty() ? 0U : rewrite_node(scene.root, moved);
    result.applied = true;
    return result;
}

}  // namespace vellum::graphics
