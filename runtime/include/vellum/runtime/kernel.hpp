#pragma once

#include <vellum/foundation/status.hpp>

#include <string>

namespace vellum::runtime {

enum class KernelState {
    created,
    running,
    stopped,
};

struct KernelConfiguration {
    std::string application_id;
};

// Process-independent lifecycle state for one Vellum runtime instance. The
// first kernel intentionally owns no window, event loop, GPU device, or mutable
// global. Platform shells and render surfaces attach above this boundary.
class Kernel final {
public:
    explicit Kernel(KernelConfiguration configuration);

    Kernel(const Kernel&) = delete;
    Kernel& operator=(const Kernel&) = delete;
    Kernel(Kernel&&) noexcept = default;
    Kernel& operator=(Kernel&&) noexcept = default;

    [[nodiscard]] foundation::Status start();
    [[nodiscard]] foundation::Status stop();

    [[nodiscard]] KernelState state() const noexcept { return state_; }
    [[nodiscard]] const KernelConfiguration& configuration() const noexcept {
        return configuration_;
    }

private:
    KernelConfiguration configuration_;
    KernelState state_ = KernelState::created;
};

}  // namespace vellum::runtime
