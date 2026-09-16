#pragma once

#include <vellum/graphics/dawn_bootstrap.hpp>

#include "dawn/dawn_proc.h"
#include "dawn/dawn_version.h"
#include "dawn/native/DawnNative.h"

#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>

namespace vellum::app_host {
namespace detail {

inline std::string revision(const std::uint8_t* bytes) {
    if (bytes == nullptr) return {};
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < dawn::kDawnVersion.size(); ++index) {
        out << std::setw(2) << unsigned(bytes[index]);
    }
    return out.str();
}

struct NativeBootstrapState final {
    std::mutex mutex;
    std::string revision;
};

inline NativeBootstrapState& native_bootstrap_state() {
    static NativeBootstrapState state;
    return state;
}

using NativeDawnVersion = const std::uint8_t* (*)();
using NativeDawnProcs = const DawnProcTable& (*)();
using NativeDawnSetter = void (*)(const DawnProcTable*);

struct NativeDawnBindings final {
    NativeDawnVersion header_version = nullptr;
    NativeDawnVersion proc_version = nullptr;
    NativeDawnProcs native_procs = nullptr;
    NativeDawnSetter set_procs = nullptr;
};

inline const std::uint8_t* header_version() { return dawn::kDawnVersion.data(); }
inline const std::uint8_t* proc_version() { return dawnProcGetVersion(); }
inline const DawnProcTable& native_procs() { return dawn::native::GetProcs(); }
inline void set_procs(const DawnProcTable* procedures) { dawnProcSetProcs(procedures); }

inline NativeDawnBindings& native_dawn_bindings() {
    static NativeDawnBindings bindings{
        .header_version = &header_version,
        .proc_version = &proc_version,
        .native_procs = &native_procs,
        .set_procs = &set_procs,
    };
    return bindings;
}

inline graphics::DawnBootstrapResult bootstrap_native_dawn(
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

}  // namespace detail

inline std::string native_dawn_revision() {
    return detail::revision(dawn::kDawnVersion.data());
}

inline graphics::DawnBootstrap native_dawn_bootstrap() {
    return {
        .abi_version = graphics::kDawnBootstrapAbiVersion,
        .callback = &detail::bootstrap_native_dawn,
        .context = &detail::native_dawn_bindings(),
    };
}

inline bool register_native_dawn_bootstrap(std::string* error = nullptr) {
    return graphics::register_dawn_bootstrap(
        native_dawn_bootstrap(), native_dawn_revision(), error);
}

/// Testable host-local state: a failed identity check must leave this false.
inline bool native_dawn_bootstrap_installed() {
    auto& state = detail::native_bootstrap_state();
    std::lock_guard lock(state.mutex);
    return !state.revision.empty();
}

}  // namespace vellum::app_host
