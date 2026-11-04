// The bootstrap coordinator, through its public API only: ABI v1 behaviour,
// failure and re-entry recovery, and the per-bundle sharing rules. The state is
// process-global, so the cases run in order against one coordinator.

#include <gpu_runtime/dawn_bootstrap.hpp>

#include <cstdio>
#include <stdexcept>
#include <string>

namespace {

using namespace gpu_runtime;

struct Host final {
    std::string installed_revision;
    unsigned mutations = 0;
    unsigned calls = 0;
    const DawnBootstrap* contract = nullptr;
    bool reenter = false;
    bool throw_once = false;
    bool fail_once = false;
};

DawnBootstrapResult bootstrap(const DawnBootstrapRequest& request, void* opaque,
                              std::string* error) {
    auto& host = *static_cast<Host*>(opaque);
    ++host.calls;
    if (host.throw_once) {
        host.throw_once = false;
        throw std::runtime_error("test callback failure");
    }
    if (host.fail_once) {
        host.fail_once = false;
        return DawnBootstrapResult::unavailable;
    }
    if (host.reenter) {
        host.reenter = false;
        std::string nested;
        if (register_dawn_bootstrap(*host.contract, request.expected_dawn_revision, &nested)) {
            if (error != nullptr)
                *error = "re-entrant bootstrap unexpectedly succeeded";
            return DawnBootstrapResult::failed;
        }
    }
    if (request.abi_version != kDawnBootstrapAbiVersion || request.expected_dawn_revision.empty())
        return DawnBootstrapResult::failed;
    host.installed_revision.assign(request.expected_dawn_revision);
    ++host.mutations;
    return DawnBootstrapResult::ready;
}

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

bool contains(const std::string& text, const char* fragment) {
    return text.find(fragment) != std::string::npos;
}

DawnBundleIdentity bundle(const char* name, const char* version) {
    return {.bundle = name, .runtime_version = version};
}

}  // namespace

int main() {
    Host host;
    const DawnBootstrap contract{
        .abi_version = kDawnBootstrapAbiVersion, .callback = &bootstrap, .context = &host};
    host.contract = &contract;
    std::string error;

    check(runtime_version() == GPU_RUNTIME_EXPECTED_VERSION,
          "runtime_version() is not the project version");
    check(!dawn_bootstrap_is_registered(&error), "a fresh coordinator reports a bootstrap");
    check(!dawn_bootstrap_record().has_value(), "a fresh coordinator has a record");

    // Malformed registrations never reach the callback.
    check(!register_dawn_bootstrap({}, "rev-a", &error), "a missing bootstrap was accepted");
    check(!register_dawn_bootstrap(contract, "", &error), "an empty revision was accepted");
    check(!register_dawn_bootstrap_for_bundle(bundle("", "1.0.0"), contract, "rev-a", &error),
          "a bundle registration without a bundle was accepted");
    check(!register_dawn_bootstrap_for_bundle(bundle("com.a", ""), contract, "rev-a", &error),
          "a bundle registration without a runtime version was accepted");
    DawnBundleIdentity future = bundle("com.a", "1.0.0");
    future.version = kDawnBundleRegistrationVersion + 1;
    check(!register_dawn_bootstrap_for_bundle(future, contract, "rev-a", &error),
          "an unknown registration version was accepted");
    check(host.calls == 0, "a malformed registration ran the callback");

    // A throwing and a failing callback each leave it idle.
    host.throw_once = true;
    check(!register_dawn_bootstrap_for_bundle(bundle("com.a", "1.0.0"), contract, "rev-a",
                                              &error),
          "a throwing bootstrap was accepted");
    check(contains(error, "threw"), "a throwing bootstrap did not say so");
    host.fail_once = true;
    check(!register_dawn_bootstrap_for_bundle(bundle("com.a", "1.0.0"), contract, "rev-a",
                                              &error),
          "an unavailable bootstrap was accepted");
    check(host.mutations == 0 && !dawn_bootstrap_is_registered(),
          "a failed bootstrap left the coordinator ready");

    // The first good registration runs the callback once and records the bundle.
    // Its callback re-enters the coordinator, which must refuse the nested call
    // (the callback reports failure if the nested call succeeds).
    host.reenter = true;
    check(register_dawn_bootstrap_for_bundle(bundle("com.a", "1.0.0"), contract, "rev-a",
                                             &error),
          "the first bundle registration failed");
    check(host.mutations == 1 && dawn_bootstrap_is_registered(), "the bootstrap did not run once");
    auto record = dawn_bootstrap_record();
    check(record && record->bundle == "com.a" && record->runtime_version == "1.0.0" &&
              record->revision == "rev-a" && record->accepted_registrations == 1,
          "the record does not name the first bundle");

    const unsigned calls = host.calls;
    // Same revision and runtime version: shared, without running a callback.
    check(register_dawn_bootstrap_for_bundle(bundle("com.b", "1.0.0"), contract, "rev-a",
                                             &error),
          "a matching second bundle was refused");
    // A different gpu-runtime version is refused and names both bundles.
    check(!register_dawn_bootstrap_for_bundle(bundle("com.c", "0.9.0"), contract, "rev-a",
                                              &error),
          "a bundle built against another gpu-runtime was accepted");
    check(contains(error, "com.c") && contains(error, "com.a") && contains(error, "0.9.0"),
          "the version refusal does not name both bundles");
    // A different Dawn revision is refused whatever the version.
    check(!register_dawn_bootstrap_for_bundle(bundle("com.d", "1.0.0"), contract, "rev-b",
                                              &error),
          "a bundle with another Dawn revision was accepted");
    check(contains(error, "Dawn provider identity"), "the revision refusal does not say why");
    // ABI v1 keeps its rules on the same coordinator.
    check(register_dawn_bootstrap(contract, "rev-a", &error),
          "an ABI v1 registration of the same revision was refused");
    check(!register_dawn_bootstrap(contract, "rev-b", &error),
          "an ABI v1 registration of another revision was accepted");
    check(host.calls == calls && host.mutations == 1 && host.installed_revision == "rev-a",
          "a later registration touched the table");
    record = dawn_bootstrap_record();
    check(record && record->bundle == "com.a" && record->accepted_registrations == 3,
          "accepted registrations were not counted");

    if (failures != 0)
        return 1;
    std::puts("gpu-runtime bootstrap: ok");
    return 0;
}
