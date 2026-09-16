#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <array>
#include <algorithm>
#include <iostream>
#include <string>

namespace {

using vellum::app_host::detail::NativeDawnBindings;
using vellum::app_host::detail::NativeDawnProcs;
using vellum::app_host::detail::NativeDawnSetter;
using vellum::app_host::detail::NativeDawnVersion;

enum class Mismatch { none, header, proc, native };

Mismatch mismatch = Mismatch::none;
unsigned setter_calls = 0;
constexpr std::array<std::uint8_t, 20> wrong_revision{
    0x5a, 0x42, 0x1c, 0x83, 0x71, 0x20, 0x96, 0xd1, 0xee, 0x33,
    0x14, 0x72, 0x48, 0x9a, 0xbc, 0x05, 0x67, 0xf0, 0x2d, 0x11,
};

const std::uint8_t* header_version() {
    return mismatch == Mismatch::header ? wrong_revision.data()
                                        : dawn::kDawnVersion.data();
}

const std::uint8_t* proc_version() {
    return mismatch == Mismatch::proc ? wrong_revision.data() : dawnProcGetVersion();
}

const DawnProcTable& native_procs() {
    static DawnProcTable procedures{};
    procedures = dawn::native::GetProcs();
    if (mismatch == Mismatch::native) {
        std::copy(wrong_revision.begin(), wrong_revision.end(), procedures.version);
    }
    return procedures;
}

void setter_spy(const DawnProcTable*) { ++setter_calls; }

const NativeDawnBindings test_bindings{
    .header_version = static_cast<NativeDawnVersion>(&header_version),
    .proc_version = static_cast<NativeDawnVersion>(&proc_version),
    .native_procs = static_cast<NativeDawnProcs>(&native_procs),
    .set_procs = static_cast<NativeDawnSetter>(&setter_spy),
};

bool require(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << message << '\n';
    return false;
}

bool mismatch_rejected(Mismatch kind, const std::string& revision) {
    mismatch = kind;
    setter_calls = 0;
    std::string error;
    const vellum::graphics::DawnBootstrap bootstrap{
        .abi_version = vellum::graphics::kDawnBootstrapAbiVersion,
        .callback = &vellum::app_host::detail::bootstrap_native_dawn,
        .context = const_cast<NativeDawnBindings*>(&test_bindings),
    };
    return require(!vellum::graphics::register_dawn_bootstrap(bootstrap, revision, &error),
                   "native identity mismatch was accepted") &&
           require(setter_calls == 0,
                   "native identity mismatch called the proc-table setter") &&
           require(!vellum::app_host::native_dawn_bootstrap_installed(),
                   "native identity mismatch changed installed state");
}

}  // namespace

int main() {
    const auto revision = vellum::app_host::native_dawn_revision();
    if (!require(!vellum::app_host::native_dawn_bootstrap_installed(),
                 "native Dawn table was unexpectedly installed before the test") ||
        !mismatch_rejected(Mismatch::header, revision) ||
        !mismatch_rejected(Mismatch::proc, revision) ||
        !mismatch_rejected(Mismatch::native, revision)) {
        return 1;
    }

    mismatch = Mismatch::none;
    setter_calls = 0;
    std::string error;
    const vellum::graphics::DawnBootstrap bootstrap{
        .abi_version = vellum::graphics::kDawnBootstrapAbiVersion,
        .callback = &vellum::app_host::detail::bootstrap_native_dawn,
        .context = const_cast<NativeDawnBindings*>(&test_bindings),
    };
    if (!require(vellum::graphics::register_dawn_bootstrap(bootstrap, revision, &error),
                 "full native identity match did not register") ||
        !require(setter_calls == 1,
                 "full native identity match did not call the proc-table setter exactly once") ||
        !require(vellum::app_host::native_dawn_bootstrap_installed(),
                 "full native identity match did not set installed state")) {
        return 1;
    }
    return 0;
}
