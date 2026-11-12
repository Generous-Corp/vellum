#include <vellum/graphics/token_receipts.hpp>

#include <bit>
#include <cmath>
#include <memory>
#include <set>
#include <string>
#include <tuple>
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

std::string slot_name(const TokenSlot& slot) {
    switch (slot.kind) {
    case TokenSlot::Kind::text_run: return "text run " + std::to_string(slot.index);
    case TokenSlot::Kind::box_shadow: return "box shadow " + std::to_string(slot.index);
    case TokenSlot::Kind::gradient_stop:
        return "gradient " + std::to_string(slot.index) + " stop " + std::to_string(slot.stop);
    case TokenSlot::Kind::path_stroke: return "path stroke";
    }
    return "slot";
}

std::optional<TokenRefusal> check_node(const Scene& scene, const SceneNode& node) {
    const auto check_receipt = [&](std::uint32_t token, const Color& color,
                                   const std::string& what) -> std::optional<TokenRefusal> {
        if (token >= scene.tokens.size()) {
            return TokenRefusal{node.id, "node " + node.id + " " + what + " references token " +
                                             std::to_string(token) +
                                             ", which the scene's token table does not hold"};
        }
        if (!same_token_color(color, scene.tokens[token].value)) {
            return TokenRefusal{node.id, "node " + node.id + " " + what +
                                             " differs from its token " + scene.tokens[token].path};
        }
        return std::nullopt;
    };
    if (node.fill_token) {
        if (auto found = check_receipt(*node.fill_token, node.fill, "fill")) return found;
    }
    std::set<std::tuple<int, std::uint32_t, std::uint32_t>> seen;
    for (const auto& slot : node.color_tokens) {
        const auto key = std::make_tuple(static_cast<int>(slot.kind), slot.index, slot.stop);
        const Color* color = token_slot_color(node, slot);
        if (color == nullptr || !seen.insert(key).second) {
            return TokenRefusal{node.id, "node " + node.id + " names " + slot_name(slot) +
                                             ", which it does not have or names twice"};
        }
        if (auto found = check_receipt(slot.token, *color, slot_name(slot))) return found;
    }
    for (const auto& child : node.children) {
        if (auto found = check_node(scene, child)) return found;
    }
    return std::nullopt;
}

bool references_any(const SceneNode& node, const std::unordered_set<std::uint32_t>& entries) {
    if (node.fill_token && entries.contains(*node.fill_token)) return true;
    for (const auto& slot : node.color_tokens) {
        if (entries.contains(slot.token)) return true;
    }
    return false;
}

std::size_t count_node(const SceneNode& node, const std::unordered_set<std::uint32_t>& entries) {
    std::size_t count = references_any(node, entries) ? 1U : 0U;
    for (const auto& child : node.children) count += count_node(child, entries);
    return count;
}

Color* mutable_slot_color(SceneNode& node, const TokenSlot& slot,
                          std::shared_ptr<PathGeometry>& stroke) {
    switch (slot.kind) {
    case TokenSlot::Kind::text_run:
        return slot.index < node.text_runs.size() ? &node.text_runs[slot.index].style.color
                                                  : nullptr;
    case TokenSlot::Kind::box_shadow:
        return slot.index < node.box_shadows.size() ? &node.box_shadows[slot.index].color : nullptr;
    case TokenSlot::Kind::gradient_stop:
        if (slot.index >= node.fill_gradients.size() ||
            slot.stop >= node.fill_gradients[slot.index].stops.size()) {
            return nullptr;
        }
        return &node.fill_gradients[slot.index].stops[slot.stop].color;
    case TokenSlot::Kind::path_stroke:
        if (!node.path) return nullptr;
        if (!stroke) stroke = std::make_shared<PathGeometry>(*node.path);
        return &stroke->stroke.color;
    }
    return nullptr;
}

std::size_t rewrite_node(SceneNode& node, const std::unordered_map<std::uint32_t, Color>& values) {
    bool touched = false;
    std::set<std::uint32_t> own_runs;
    for (const auto& slot : node.color_tokens) {
        if (slot.kind == TokenSlot::Kind::text_run) own_runs.insert(slot.index);
    }
    if (node.fill_token) {
        if (const auto found = values.find(*node.fill_token); found != values.end()) {
            for (std::size_t run = 0; run < node.text_runs.size(); ++run) {
                auto& color = node.text_runs[run].style.color;
                if (!own_runs.contains(static_cast<std::uint32_t>(run)) &&
                    same_token_color(color, node.fill)) {
                    color = found->second;
                }
            }
            node.fill = found->second;
            touched = true;
        }
    }
    std::shared_ptr<PathGeometry> stroke;
    for (const auto& slot : node.color_tokens) {
        const auto found = values.find(slot.token);
        if (found == values.end()) continue;
        if (Color* color = mutable_slot_color(node, slot, stroke)) {
            *color = found->second;
            touched = true;
        }
    }
    if (stroke) node.path = std::move(stroke);
    std::size_t count = touched ? 1U : 0U;
    for (auto& child : node.children) count += rewrite_node(child, values);
    return count;
}

}  // namespace

const Color* token_slot_color(const SceneNode& node, const TokenSlot& slot) noexcept {
    switch (slot.kind) {
    case TokenSlot::Kind::text_run:
        return slot.index < node.text_runs.size() ? &node.text_runs[slot.index].style.color
                                                  : nullptr;
    case TokenSlot::Kind::box_shadow:
        return slot.index < node.box_shadows.size() ? &node.box_shadows[slot.index].color : nullptr;
    case TokenSlot::Kind::gradient_stop:
        if (slot.index >= node.fill_gradients.size() ||
            slot.stop >= node.fill_gradients[slot.index].stops.size()) {
            return nullptr;
        }
        return &node.fill_gradients[slot.index].stops[slot.stop].color;
    case TokenSlot::Kind::path_stroke:
        return node.path ? &node.path->stroke.color : nullptr;
    }
    return nullptr;
}

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
