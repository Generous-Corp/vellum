#include <vellum/graphics/dawn_bootstrap.hpp>

#include <iostream>
#include <string>

namespace {

using namespace vellum::graphics;

struct Host final {
    std::string installed_revision;
    unsigned mutations = 0;
    unsigned calls = 0;
    const DawnBootstrap* bootstrap_contract = nullptr;
    bool reenter = false;
};

DawnBootstrapResult bootstrap(
    const DawnBootstrapRequest& request, void* opaque, std::string* error) {
    auto& host = *static_cast<Host*>(opaque);
    ++host.calls;
    if (host.reenter) {
        host.reenter = false;
        std::string nested_error;
        if (register_dawn_bootstrap(*host.bootstrap_contract,
                                  request.expected_dawn_revision, &nested_error)) {
            if (error != nullptr) *error = "reentrant bootstrap unexpectedly succeeded";
            return DawnBootstrapResult::failed;
        }
    }
    if (request.abi_version != kDawnBootstrapAbiVersion ||
        request.expected_dawn_revision.empty()) {
        if (error != nullptr) *error = "unexpected bootstrap request";
        return DawnBootstrapResult::failed;
    }
    if (!host.installed_revision.empty()) {
        if (host.installed_revision == request.expected_dawn_revision) {
            return DawnBootstrapResult::ready;
        }
        if (error != nullptr) *error = "host provider mismatch";
        return DawnBootstrapResult::identity_mismatch;
    }
    host.installed_revision.assign(request.expected_dawn_revision);
    ++host.mutations;
    return DawnBootstrapResult::ready;
}

bool require(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << message << '\n';
    return false;
}

}  // namespace

int main() {
    Host host;
    const DawnBootstrap bootstrap_contract{
        .abi_version = kDawnBootstrapAbiVersion,
        .callback = &bootstrap,
        .context = &host,
    };
    host.bootstrap_contract = &bootstrap_contract;
    host.reenter = true;
    std::string error;
    if (!require(register_dawn_bootstrap(bootstrap_contract, "provider-a", &error),
                 "first authenticated bootstrap failed") ||
        !require(host.mutations == 1 && host.calls == 1,
                 "first bootstrap did not make exactly one mutation") ||
        !require(register_dawn_bootstrap(bootstrap_contract, "provider-a", &error),
                 "same-provider bootstrap was not idempotent") ||
        !require(host.mutations == 1 && host.calls == 1,
                 "same-provider bootstrap reinvoked the host") ||
        !require(!register_dawn_bootstrap(bootstrap_contract, "provider-b", &error),
                 "mismatched provider was accepted") ||
        !require(host.mutations == 1 && host.calls == 1 &&
                     host.installed_revision == "provider-a",
                 "mismatch mutated the established provider") ||
        !require(!register_dawn_bootstrap({}, "provider-a", &error),
                 "missing bootstrap was accepted")) {
        return 1;
    }
    return 0;
}
