// The native Dawn bootstrap: the only device source that calls Dawn's
// proc-table setter. It runs through the bootstrap coordinator, which calls it
// at most once per image and never after another registration installed the
// table.

#include <gpu_runtime/dawn_native_bootstrap.hpp>

#include "detail/native_dawn_bindings.hpp"

#include "dawn/dawn_proc.h"
#include "dawn/dawn_version.h"
#include "dawn/native/DawnNative.h"

#include <string>

namespace gpu_runtime {
namespace detail {
namespace {

std::string revision(const std::uint8_t* bytes) {
    if (bytes == nullptr)
        return {};
    static constexpr char digits[] = "0123456789abcdef";
    std::string text;
    text.reserve(dawn::kDawnVersion.size() * 2);
    for (std::size_t index = 0; index < dawn::kDawnVersion.size(); ++index) {
        text.push_back(digits[bytes[index] >> 4]);
        text.push_back(digits[bytes[index] & 0x0f]);
    }
    return text;
}

const std::uint8_t* header_version() { return dawn::kDawnVersion.data(); }
const std::uint8_t* proc_version() { return dawnProcGetVersion(); }
const DawnProcTable& native_procs() { return dawn::native::GetProcs(); }
void set_procs(const DawnProcTable* procedures) { dawnProcSetProcs(procedures); }

DawnBootstrapResult bootstrap_native_dawn(const DawnBootstrapRequest& request, void* opaque,
                                          std::string* error) {
    const auto& bindings =
        opaque == nullptr ? native_dawn_bindings() : *static_cast<const NativeDawnBindings*>(opaque);
    if (bindings.header_version == nullptr || bindings.proc_version == nullptr ||
        bindings.native_procs == nullptr || bindings.set_procs == nullptr) {
        if (error != nullptr)
            *error = "native Dawn bindings are incomplete";
        return DawnBootstrapResult::failed;
    }
    const auto header = revision(bindings.header_version());
    const auto proc = revision(bindings.proc_version());
    const DawnProcTable& native = bindings.native_procs();
    const auto native_revision = revision(native.version);
    if (header.empty() || header != proc || header != native_revision ||
        request.expected_dawn_revision != header) {
        if (error != nullptr)
            *error = "Dawn header/proc/native identity mismatch";
        return DawnBootstrapResult::identity_mismatch;
    }
    bindings.set_procs(&native);
    return DawnBootstrapResult::ready;
}

}  // namespace

NativeDawnBindings& native_dawn_bindings() {
    static NativeDawnBindings bindings{
        .header_version = &header_version,
        .proc_version = &proc_version,
        .native_procs = &native_procs,
        .set_procs = &set_procs,
    };
    return bindings;
}

}  // namespace detail

std::string native_dawn_revision() {
    return detail::revision(dawn::kDawnVersion.data());
}

DawnBootstrap native_dawn_bootstrap() {
    return {
        .abi_version = kDawnBootstrapAbiVersion,
        .callback = &detail::bootstrap_native_dawn,
        .context = &detail::native_dawn_bindings(),
    };
}

bool register_native_dawn_bootstrap(std::string_view bundle, std::string* error) {
    if (bundle.empty())
        return register_dawn_bootstrap(native_dawn_bootstrap(), native_dawn_revision(), error);
    return register_dawn_bootstrap_for_bundle(
        DawnBundleIdentity{.bundle = bundle, .runtime_version = runtime_version()},
        native_dawn_bootstrap(), native_dawn_revision(), error);
}

}  // namespace gpu_runtime
