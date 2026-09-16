#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace vellum::graphics {

/// ABI for a host-owned Dawn proc-table bootstrap.
///
/// Dawn's C++ wrappers use one process-global proc table. The graphics library
/// therefore never installs a table itself: the embedding executable supplies
/// this callback before Vellum creates its first Dawn object. The host callback
/// must authenticate its header, exported proc, and native-provider identities
/// against `expected_dawn_revision` before it calls dawnProcSetProcs.
inline constexpr std::uint32_t kDawnBootstrapAbiVersion = 1;

enum class DawnBootstrapResult : std::uint32_t {
    ready,
    unavailable,
    identity_mismatch,
    failed,
};

struct DawnBootstrapRequest final {
    std::uint32_t abi_version = kDawnBootstrapAbiVersion;
    std::string_view expected_dawn_revision;
};

using DawnBootstrapCallback = DawnBootstrapResult (*) (
    const DawnBootstrapRequest& request, void* context, std::string* error);

struct DawnBootstrap final {
    std::uint32_t abi_version = 0;
    DawnBootstrapCallback callback = nullptr;
    void* context = nullptr;
};

/// Registers the host bootstrap once for one authenticated provider revision.
///
/// A repeated request for the same revision is a no-op. A different revision
/// is rejected before the callback can mutate Dawn's process-global state.
[[nodiscard]] bool register_dawn_bootstrap(
    const DawnBootstrap& bootstrap, std::string_view expected_dawn_revision,
    std::string* error = nullptr);

/// Returns whether a versioned host bootstrap has completed in this process.
/// `SkiaDawnSurface::create` fails closed until this returns true.
[[nodiscard]] bool dawn_bootstrap_is_registered(std::string* error = nullptr);

}  // namespace vellum::graphics
