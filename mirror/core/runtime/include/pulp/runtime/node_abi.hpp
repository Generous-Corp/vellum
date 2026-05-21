#pragma once

#include <cstdint>

namespace pulp {

inline constexpr std::uint32_t PULP_NODE_ABI_VERSION = 1;

inline constexpr std::uint32_t pulp_node_abi_version() noexcept {
    return PULP_NODE_ABI_VERSION;
}

} // namespace pulp
