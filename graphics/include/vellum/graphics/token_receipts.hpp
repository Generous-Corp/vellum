#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace vellum::graphics {

/// Whether two receipt colours are bitwise equal.
[[nodiscard]] bool same_token_color(const Color& a, const Color& b) noexcept;

/// Why a scene's token table or references cannot be drawn.
struct TokenRefusal final {
    std::string node_id;
    std::string reason;
};

/// The first problem in a scene's token table or references: a repeated
/// path, a value that is not finite, a reference past the end of the table,
/// or a node whose `fill` differs from the receipt it references. Empty when
/// the table and every reference are sound.
[[nodiscard]] std::optional<TokenRefusal> find_invalid_token_reference(const Scene& scene);

/// The indices, in `next`, of receipts that differ from the receipt at the
/// same index in `previous` in path, layer or value, plus every index past
/// the end of `previous`.
[[nodiscard]] std::vector<std::uint32_t> changed_token_entries(
    const Scene& previous, const Scene& next);

/// How many nodes of `scene` reference one of `entries`.
[[nodiscard]] std::size_t count_token_references(
    const Scene& scene, std::span<const std::uint32_t> entries);

/// The outcome of `apply_token_values`.
struct TokenSwitch final {
    /// False when `resolved` lacks a path the scene's table holds; the scene
    /// is then left unchanged and `missing_path` names the path.
    bool applied = false;
    std::string missing_path;
    /// Entries whose layer or value changed.
    std::vector<std::uint32_t> changed;
    /// Nodes whose `fill` was rewritten because their receipt's value changed.
    std::size_t nodes_updated = 0;
};

/// Re-resolves a scene's token table for a theme switch. Each entry is
/// replaced by the receipt in `resolved` with the same path, and every node
/// that references an entry whose value changed takes the new value as its
/// `fill`. A text run that carries the node's old colour inherited it and
/// takes the new one too. Nodes that reference no changed entry are not
/// touched, so a scene diff damages only the referencing nodes.
[[nodiscard]] TokenSwitch apply_token_values(
    Scene& scene, std::span<const TokenReceipt> resolved);

}  // namespace vellum::graphics
