#include <vellum/graphics/dawn_bootstrap.hpp>

#include <gpu_runtime/dawn_bootstrap.hpp>

#include <string>

// Vellum's ABI v1 entry points are kept for already-built hosts and forward to
// gpu-runtime's coordinator, the image's one Dawn proc-table installer. The two
// ABIs have the same shape but distinct types, so each call adapts the host's
// callback for the duration of the registration; the coordinator never keeps
// it afterwards.

namespace vellum::graphics {
namespace {

static_assert(kDawnBootstrapAbiVersion == gpu_runtime::kDawnBootstrapAbiVersion);
static_assert(static_cast<std::uint32_t>(DawnBootstrapResult::ready) ==
              static_cast<std::uint32_t>(gpu_runtime::DawnBootstrapResult::ready));
static_assert(static_cast<std::uint32_t>(DawnBootstrapResult::unavailable) ==
              static_cast<std::uint32_t>(gpu_runtime::DawnBootstrapResult::unavailable));
static_assert(static_cast<std::uint32_t>(DawnBootstrapResult::identity_mismatch) ==
              static_cast<std::uint32_t>(gpu_runtime::DawnBootstrapResult::identity_mismatch));
static_assert(static_cast<std::uint32_t>(DawnBootstrapResult::failed) ==
              static_cast<std::uint32_t>(gpu_runtime::DawnBootstrapResult::failed));

gpu_runtime::DawnBootstrapResult adapt(const gpu_runtime::DawnBootstrapRequest& request,
                                       void* context, std::string* error) {
    const auto& host = *static_cast<const DawnBootstrap*>(context);
    const DawnBootstrapRequest forwarded{
        .abi_version = request.abi_version,
        .expected_dawn_revision = request.expected_dawn_revision,
    };
    return static_cast<gpu_runtime::DawnBootstrapResult>(
        host.callback(forwarded, host.context, error));
}

}  // namespace

bool register_dawn_bootstrap(const DawnBootstrap& bootstrap,
                             std::string_view expected_dawn_revision, std::string* error) {
    if (bootstrap.abi_version != kDawnBootstrapAbiVersion || bootstrap.callback == nullptr) {
        if (error != nullptr)
            *error = "a versioned host Dawn bootstrap and expected revision are required";
        return false;
    }
    const gpu_runtime::DawnBootstrap adapted{
        .abi_version = gpu_runtime::kDawnBootstrapAbiVersion,
        .callback = &adapt,
        .context = const_cast<DawnBootstrap*>(&bootstrap),
    };
    return gpu_runtime::register_dawn_bootstrap(adapted, expected_dawn_revision, error);
}

bool dawn_bootstrap_is_registered(std::string* error) {
    return gpu_runtime::dawn_bootstrap_is_registered(error);
}

}  // namespace vellum::graphics
