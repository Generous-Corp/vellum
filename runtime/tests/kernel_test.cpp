#include <vellum/runtime/kernel.hpp>

#include <iostream>

int main() {
    using vellum::runtime::Kernel;
    using vellum::runtime::KernelConfiguration;
    using vellum::runtime::KernelState;

    Kernel first(KernelConfiguration{.application_id = "dev.vellum.first"});
    Kernel second(KernelConfiguration{.application_id = "dev.vellum.second"});

    if (!first.start() || first.state() != KernelState::running) {
        std::cerr << "first kernel did not start\n";
        return 1;
    }
    if (second.state() != KernelState::created) {
        std::cerr << "kernel lifecycle leaked across instances\n";
        return 1;
    }
    if (!second.start() || !first.stop()) {
        std::cerr << "independent lifecycle transition failed\n";
        return 1;
    }
    if (first.state() != KernelState::stopped ||
        second.state() != KernelState::running) {
        std::cerr << "kernel instances are not independent\n";
        return 1;
    }
    if (first.start()) {
        std::cerr << "a stopped kernel unexpectedly restarted\n";
        return 1;
    }

    Kernel invalid(KernelConfiguration{});
    const auto invalid_status = invalid.start();
    if (invalid_status || invalid_status.message().empty()) {
        std::cerr << "invalid configuration was not diagnosed\n";
        return 1;
    }
    return 0;
}
