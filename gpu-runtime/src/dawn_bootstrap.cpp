#include <gpu_runtime/dawn_bootstrap.hpp>

#include <exception>
#include <mutex>
#include <string>
#include <utility>

#ifndef GPU_RUNTIME_VERSION
#error "GPU_RUNTIME_VERSION must name the gpu-runtime version this library is built as"
#endif

namespace gpu_runtime {
namespace {

struct BootstrapState final {
    enum class Phase { idle, initializing, ready };
    std::mutex mutex;
    Phase phase = Phase::idle;
    DawnBootstrapRecord record;
};

BootstrapState& bootstrap_state() {
    static BootstrapState state;
    return state;
}

bool fail(std::string* error, std::string message) {
    if (error != nullptr)
        *error = std::move(message);
    return false;
}

std::string describe(std::string_view bundle, std::string_view version) {
    if (bundle.empty())
        return "an unnamed registration";
    std::string text = "bundle ";
    text.append(bundle);
    if (!version.empty()) {
        text.append(" (gpu-runtime ");
        text.append(version);
        text.push_back(')');
    }
    return text;
}

// Clears the in-progress phase when the callback fails or throws, so a
// corrected registration may retry.
struct ResetInitializingOnFailure final {
    explicit ResetInitializingOnFailure(BootstrapState& state) : state(state) {}
    ~ResetInitializingOnFailure() {
        if (!active)
            return;
        std::lock_guard lock(state.mutex);
        state.phase = BootstrapState::Phase::idle;
        state.record = {};
    }

    BootstrapState& state;
    bool active = true;
};

bool register_impl(std::string_view bundle, std::string_view version,
                   const DawnBootstrap& bootstrap, std::string_view expected_dawn_revision,
                   std::string* error) {
    if (bootstrap.abi_version != kDawnBootstrapAbiVersion || bootstrap.callback == nullptr ||
        expected_dawn_revision.empty()) {
        return fail(error, "a versioned host Dawn bootstrap and expected revision are required");
    }

    auto& state = bootstrap_state();
    {
        std::lock_guard lock(state.mutex);
        if (state.phase == BootstrapState::Phase::ready) {
            auto& record = state.record;
            const bool same_revision = record.revision == expected_dawn_revision;
            const bool same_version = record.runtime_version.empty() || version.empty() ||
                                      record.runtime_version == version;
            if (same_revision && same_version) {
                if (record.bundle.empty() && !bundle.empty()) {
                    record.bundle.assign(bundle);
                    record.runtime_version.assign(version);
                }
                ++record.accepted_registrations;
                if (error != nullptr)
                    error->clear();
                return true;
            }
            if (bundle.empty() && record.bundle.empty())
                return fail(error, "Dawn provider identity does not match the process bootstrap");
            std::string reason = describe(bundle, version);
            reason.append(same_revision ? " was built against a different gpu-runtime than "
                                        : " names a different Dawn provider identity than ");
            reason.append(describe(record.bundle, record.runtime_version));
            reason.append(", which already owns this process's Dawn proc table");
            return fail(error, std::move(reason));
        }
        if (state.phase == BootstrapState::Phase::initializing)
            return fail(error, "Dawn bootstrap is already in progress");
        state.phase = BootstrapState::Phase::initializing;
    }

    ResetInitializingOnFailure reset_on_failure(state);
    const DawnBootstrapRequest request{
        .abi_version = kDawnBootstrapAbiVersion,
        .expected_dawn_revision = expected_dawn_revision,
    };
    DawnBootstrapResult result;
    try {
        result = bootstrap.callback(request, bootstrap.context, error);
    } catch (const std::exception& exception) {
        return fail(error, std::string("host Dawn bootstrap threw: ") + exception.what());
    } catch (...) {
        return fail(error, "host Dawn bootstrap threw an unknown exception");
    }
    if (result != DawnBootstrapResult::ready) {
        if (error != nullptr && error->empty()) {
            *error = result == DawnBootstrapResult::identity_mismatch
                         ? "Dawn provider identity mismatch"
                         : "host Dawn bootstrap failed";
        }
        return false;
    }
    {
        std::lock_guard lock(state.mutex);
        state.record.revision.assign(expected_dawn_revision);
        state.record.bundle.assign(bundle);
        state.record.runtime_version.assign(version);
        state.record.accepted_registrations = 1;
        state.phase = BootstrapState::Phase::ready;
    }
    reset_on_failure.active = false;
    if (error != nullptr)
        error->clear();
    return true;
}

}  // namespace

bool register_dawn_bootstrap(const DawnBootstrap& bootstrap,
                             std::string_view expected_dawn_revision, std::string* error) {
    return register_impl({}, {}, bootstrap, expected_dawn_revision, error);
}

bool register_dawn_bootstrap_for_bundle(const DawnBundleIdentity& bundle,
                                        const DawnBootstrap& bootstrap,
                                        std::string_view expected_dawn_revision,
                                        std::string* error) {
    if (bundle.version != kDawnBundleRegistrationVersion)
        return fail(error, "unsupported Dawn bundle registration version");
    if (bundle.bundle.empty() || bundle.runtime_version.empty())
        return fail(error, "a bundle registration names its bundle and gpu-runtime version");
    return register_impl(bundle.bundle, bundle.runtime_version, bootstrap,
                         expected_dawn_revision, error);
}

bool dawn_bootstrap_is_registered(std::string* error) {
    auto& state = bootstrap_state();
    std::lock_guard lock(state.mutex);
    if (state.phase == BootstrapState::Phase::ready)
        return true;
    return fail(error, "host must register a Dawn bootstrap before creating a GPU surface");
}

std::optional<DawnBootstrapRecord> dawn_bootstrap_record() {
    auto& state = bootstrap_state();
    std::lock_guard lock(state.mutex);
    if (state.phase != BootstrapState::Phase::ready)
        return std::nullopt;
    return state.record;
}

std::string_view runtime_version() noexcept {
    return GPU_RUNTIME_VERSION;
}

}  // namespace gpu_runtime
