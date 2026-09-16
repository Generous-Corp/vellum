#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <iostream>
#include <string>

int main() {
    const auto bootstrap = vellum::app_host::native_dawn_bootstrap();
    std::string error;
    const auto revision = vellum::app_host::native_dawn_revision();
    if (vellum::app_host::native_dawn_bootstrap_installed()) {
        std::cerr << "native Dawn table was unexpectedly installed before the test\n";
        return 1;
    }
    if (vellum::graphics::register_dawn_bootstrap(bootstrap, "not-the-provider", &error) ||
        vellum::app_host::native_dawn_bootstrap_installed()) {
        std::cerr << "mismatched native provider installed a Dawn table\n";
        return 1;
    }
    if (bootstrap.callback == nullptr ||
        !vellum::graphics::register_dawn_bootstrap(bootstrap, revision, &error) ||
        !vellum::app_host::native_dawn_bootstrap_installed()) {
        std::cerr << "host bootstrap failed: " << error << '\n';
        return 1;
    }
    return 0;
}
