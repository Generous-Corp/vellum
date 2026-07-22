#include <vellum/foundation/platform.hpp>
#include <vellum/foundation/status.hpp>
#include <vellum/foundation/version.hpp>

#include <iostream>

int main() {
    using namespace vellum::foundation;

    if (version.empty()) {
        std::cerr << "version must not be empty\n";
        return 1;
    }
    if (operating_system_name(OperatingSystem::macos) != "macos") {
        std::cerr << "macOS platform name is unstable\n";
        return 1;
    }
    if (!Status::success() || Status::failure("expected")) {
        std::cerr << "status truth semantics are incorrect\n";
        return 1;
    }
    if (Status::failure("expected").message() != "expected") {
        std::cerr << "status failure lost its diagnostic\n";
        return 1;
    }
    return 0;
}
