#pragma once

// dawn_bootstrap.hpp — the one installer of Dawn's process-global proc table.
//
// Dawn's C++ wrappers dispatch every call through one table of function
// pointers per image. The bootstrap coordinator is the only code that installs
// it: a host registers a versioned callback that authenticates its Dawn build
// against an expected revision and then installs the table, or the device
// layer registers the native default (dawn_native_bootstrap.hpp). Either way
// the coordinator runs the callback once, records the revision, and refuses a
// later registration that names a different one, before that registration can
// touch the table.
//
// ABI v1 is the contract already-shipped renderers use and stays unchanged.
// The per-bundle registration adds who registered: the bundle and the
// gpu-runtime version it was built against. A plug-in links gpu-runtime
// statically with hidden symbols, so each bundle normally has a coordinator
// of its own; when two bundles do meet in one coordinator (symbols exported or
// interposed by mistake), they may share it only when both the Dawn revision
// and the gpu-runtime version agree, and otherwise the second fails closed.

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace gpu_runtime {

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

using DawnBootstrapCallback = DawnBootstrapResult (*)(
    const DawnBootstrapRequest& request, void* context, std::string* error);

struct DawnBootstrap final {
    std::uint32_t abi_version = 0;
    DawnBootstrapCallback callback = nullptr;
    void* context = nullptr;
};

/// Register the bootstrap once for one authenticated provider revision (ABI v1).
///
/// A repeated request for the same revision is a no-op. A different revision
/// is rejected before the callback can mutate Dawn's process-global state. A
/// callback that fails or throws leaves the coordinator idle, so a corrected
/// registration may retry; one that re-enters the coordinator fails.
[[nodiscard]] bool register_dawn_bootstrap(const DawnBootstrap& bootstrap,
                                           std::string_view expected_dawn_revision,
                                           std::string* error = nullptr);

/// Returns whether a bootstrap has completed in this coordinator.
[[nodiscard]] bool dawn_bootstrap_is_registered(std::string* error = nullptr);

inline constexpr std::uint32_t kDawnBundleRegistrationVersion = 1;

/// Who is registering: a stable bundle identifier (a plug-in's bundle ID or an
/// application's name) and the gpu-runtime version that bundle was built with.
struct DawnBundleIdentity final {
    std::uint32_t version = kDawnBundleRegistrationVersion;
    std::string_view bundle;
    std::string_view runtime_version;
};

/// Register the bootstrap on behalf of a bundle.
///
/// The first registration runs the callback, as register_dawn_bootstrap()
/// does, and records the bundle. A later registration succeeds without running
/// a callback when it names the same Dawn revision and, where both sides
/// recorded one, the same gpu-runtime version, whatever its bundle. Any other
/// combination is refused, naming both bundles, and leaves the table
/// untouched. The bundle and runtime version must be non-empty.
[[nodiscard]] bool register_dawn_bootstrap_for_bundle(const DawnBundleIdentity& bundle,
                                                      const DawnBootstrap& bootstrap,
                                                      std::string_view expected_dawn_revision,
                                                      std::string* error = nullptr);

/// What the coordinator has recorded once a bootstrap completed. An ABI v1
/// registration records no bundle and no runtime version.
struct DawnBootstrapRecord final {
    std::string revision;
    std::string bundle;
    std::string runtime_version;
    /// How many registrations, the first included, the coordinator accepted.
    std::uint32_t accepted_registrations = 0;
};

[[nodiscard]] std::optional<DawnBootstrapRecord> dawn_bootstrap_record();

/// The gpu-runtime version this library was built as.
[[nodiscard]] std::string_view runtime_version() noexcept;

}  // namespace gpu_runtime
