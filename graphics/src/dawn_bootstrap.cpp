#include <vellum/graphics/dawn_bootstrap.hpp>

#include <mutex>
#include <string>
#include <utility>

namespace vellum::graphics {
namespace {

struct BootstrapState final {
    enum class Phase { idle, initializing, ready };
    std::mutex mutex;
    Phase phase = Phase::idle;
    std::string revision;
};

BootstrapState& bootstrap_state() {
    static BootstrapState state;
    return state;
}

void set_error(std::string* error, std::string message) {
    if (error != nullptr) *error = std::move(message);
}

}  // namespace

bool ensure_dawn_bootstrap(
    const DawnBootstrap& bootstrap, std::string_view expected_dawn_revision,
    std::string* error) {
    if (bootstrap.abi_version != kDawnBootstrapAbiVersion ||
        bootstrap.callback == nullptr || expected_dawn_revision.empty()) {
        set_error(
            error, "a versioned host Dawn bootstrap and expected revision are required");
        return false;
    }

    auto& state = bootstrap_state();
    {
        std::lock_guard lock(state.mutex);
        if (state.phase == BootstrapState::Phase::ready) {
            if (state.revision == expected_dawn_revision) return true;
            set_error(error, "Dawn provider identity does not match the process bootstrap");
            return false;
        }
        if (state.phase == BootstrapState::Phase::initializing) {
            set_error(error, "Dawn bootstrap is already in progress");
            return false;
        }
        state.phase = BootstrapState::Phase::initializing;
    }

    const DawnBootstrapRequest request{
        .abi_version = kDawnBootstrapAbiVersion,
        .expected_dawn_revision = expected_dawn_revision,
    };
    const auto result = bootstrap.callback(request, bootstrap.context, error);
    std::lock_guard lock(state.mutex);
    if (result != DawnBootstrapResult::ready) {
        state.phase = BootstrapState::Phase::idle;
        if (error != nullptr && error->empty()) {
            set_error(error, result == DawnBootstrapResult::identity_mismatch
                                 ? "Dawn provider identity mismatch"
                                 : "host Dawn bootstrap failed");
        }
        return false;
    }
    state.revision.assign(expected_dawn_revision);
    state.phase = BootstrapState::Phase::ready;
    if (error != nullptr) error->clear();
    return true;
}

}  // namespace vellum::graphics
