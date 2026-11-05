#include <gpu_runtime/gpu_proc_installer.hpp>

#include <gpu_runtime/dawn_bootstrap.hpp>
#include <gpu_runtime/dawn_native_bootstrap.hpp>

#include "gpu_proc_installer_registry.hpp"

#include "detail/log.hpp"

namespace gpu_runtime {

namespace detail {

// The default installer defers to the bootstrap coordinator, the image's only
// proc-table installer. A table another component already bootstrapped is
// used as it is. Otherwise the native bootstrap installs Dawn's native procs,
// unless this build leaves the table to the host, in which case every Dawn
// object creation fails until the host registers a bootstrap or an installer.
bool install_default_dawn_procs(void* /*context*/, std::string* error) {
    if (dawn_bootstrap_is_registered())
        return true;
#if defined(GPU_RUNTIME_HOST_OWNS_DAWN_PROCS)
    if (error != nullptr)
        *error = "the host owns Dawn's proc table and has not bootstrapped it; register a "
                 "Dawn bootstrap or call set_dawn_proc_installer() before creating any "
                 "GpuSurface or GpuCompute";
    return false;
#else
    return register_native_dawn_bootstrap({}, error);
#endif
}

} // namespace detail

namespace {

detail::DawnProcInstallerRegistry& registry() {
    static detail::DawnProcInstallerRegistry instance(
        DawnProcInstaller{&detail::install_default_dawn_procs, nullptr});
    return instance;
}

} // namespace

bool set_dawn_proc_installer(const DawnProcInstaller& installer, std::string* error) {
    std::string reason;
    if (registry().set(installer, &reason))
        return true;
    detail::log_error("set_dawn_proc_installer: {}", reason);
    if (error != nullptr)
        *error = std::move(reason);
    return false;
}

bool ensure_dawn_procs(std::string* error) {
    return registry().ensure(error);
}

} // namespace gpu_runtime
