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

inline graphics::DawnBootstrapResult bootstrap_native_dawn(
    const graphics::DawnBootstrapRequest& request, void*, std::string* error) {
    const auto header = revision(dawn::kDawnVersion.data());
    const auto proc = revision(dawnProcGetVersion());
    const DawnProcTable& native = dawn::native::GetProcs();
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
    dawnProcSetProcs(&native);
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
        .context = nullptr,
    };
}

}  // namespace vellum::app_host
