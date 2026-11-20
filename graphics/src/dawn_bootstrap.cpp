#include <vellum/graphics/dawn_bootstrap.hpp>
#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <gpu_runtime/dawn_bootstrap.hpp>

#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>

namespace vellum::app_host::detail {
namespace {

struct NativeBootstrapState final {
    std::mutex mutex;
    std::string revision;
};

NativeBootstrapState& native_bootstrap_state() {
    static NativeBootstrapState state;
    return state;
}

std::string revision(const std::uint8_t* bytes) {
    if (bytes == nullptr) return {};
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < dawn::kDawnVersion.size(); ++index)
        out << std::setw(2) << unsigned(bytes[index]);
    return out.str();
}

const std::uint8_t* header_version() { return dawn::kDawnVersion.data(); }
const std::uint8_t* proc_version() { return dawnProcGetVersion(); }
const DawnProcTable& native_procs() { return dawn::native::GetProcs(); }
void set_procs(const DawnProcTable* procedures) { dawnProcSetProcs(procedures); }

NativeDawnBindings& native_dawn_bindings() {
    static NativeDawnBindings bindings{
        .header_version = &header_version,
        .proc_version = &proc_version,
        .native_procs = &native_procs,
        .set_procs = &set_procs,
    };
    return bindings;
}

}  // namespace

graphics::DawnBootstrapResult bootstrap_native_dawn(
    const graphics::DawnBootstrapRequest& request, void* opaque, std::string* error) {
    const auto& bindings = opaque == nullptr
        ? native_dawn_bindings()
        : *static_cast<const NativeDawnBindings*>(opaque);
    if (bindings.header_version == nullptr || bindings.proc_version == nullptr ||
        bindings.native_procs == nullptr || bindings.set_procs == nullptr) {
        if (error != nullptr) *error = "host Dawn bindings are incomplete";
        return graphics::DawnBootstrapResult::failed;
    }
    const auto header = revision(bindings.header_version());
    const auto proc = revision(bindings.proc_version());
    const DawnProcTable& native = bindings.native_procs();
    const auto native_revision = revision(native.version);
    if (header.empty() || header != proc || header != native_revision ||
        request.expected_dawn_revision != header) {
        if (error != nullptr) *error = "host Dawn header/proc/native identity mismatch";
        return graphics::DawnBootstrapResult::identity_mismatch;
    }

    auto& state = native_bootstrap_state();
    std::lock_guard lock(state.mutex);
    if (!state.revision.empty()) {
        if (state.revision == header) return graphics::DawnBootstrapResult::ready;
        if (error != nullptr) *error = "host Dawn bootstrap revision changed";
        return graphics::DawnBootstrapResult::identity_mismatch;
    }
    bindings.set_procs(&native);
    state.revision = header;
    return graphics::DawnBootstrapResult::ready;
}

}  // namespace vellum::app_host::detail

namespace vellum::app_host {

std::string native_dawn_revision() {
    return detail::revision(dawn::kDawnVersion.data());
}

graphics::DawnBootstrap native_dawn_bootstrap() {
    return {
        .abi_version = graphics::kDawnBootstrapAbiVersion,
        .callback = &detail::bootstrap_native_dawn,
        .context = &detail::native_dawn_bindings(),
    };
}

bool register_native_dawn_bootstrap(std::string* error) {
    return graphics::register_dawn_bootstrap(
        native_dawn_bootstrap(), native_dawn_revision(), error);
}

bool register_native_dawn_bootstrap_for_bundle(std::string_view bundle, std::string* error) {
    return graphics::register_dawn_bootstrap_for_bundle(
        bundle, native_dawn_bootstrap(), native_dawn_revision(), error);
}

bool native_dawn_bootstrap_installed() {
    auto& state = detail::native_bootstrap_state();
    std::lock_guard lock(state.mutex);
    return !state.revision.empty();
}

}  // namespace vellum::app_host

// Vellum's ABI v1 entry points are kept for already-built hosts and forward to
// gpu-runtime's coordinator, the image's one Dawn proc-table installer.
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

bool register_dawn_bootstrap_for_bundle(std::string_view bundle,
                                        const DawnBootstrap& bootstrap,
                                        std::string_view expected_dawn_revision,
                                        std::string* error) {
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
    return gpu_runtime::register_dawn_bootstrap_for_bundle(
        gpu_runtime::DawnBundleIdentity{
            .bundle = bundle,
            .runtime_version = gpu_runtime::runtime_version(),
        },
        adapted, expected_dawn_revision, error);
}

bool dawn_bootstrap_is_registered(std::string* error) {
    return gpu_runtime::dawn_bootstrap_is_registered(error);
}

}  // namespace vellum::graphics
