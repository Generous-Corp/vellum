#include <vellum/runtime/kernel.hpp>

#include <utility>

namespace vellum::runtime {

Kernel::Kernel(KernelConfiguration configuration)
    : configuration_(std::move(configuration)) {}

foundation::Status Kernel::start() {
    if (configuration_.application_id.empty()) {
        return foundation::Status::failure("application_id must not be empty");
    }
    if (state_ != KernelState::created) {
        return foundation::Status::failure("kernel can only start from the created state");
    }
    state_ = KernelState::running;
    return foundation::Status::success();
}

foundation::Status Kernel::stop() {
    if (state_ != KernelState::running) {
        return foundation::Status::failure("kernel can only stop from the running state");
    }
    state_ = KernelState::stopped;
    return foundation::Status::success();
}

}  // namespace vellum::runtime
