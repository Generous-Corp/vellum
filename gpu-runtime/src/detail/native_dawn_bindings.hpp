#pragma once

// The Dawn entry points the native bootstrap reads, as a struct so a test can
// hand the bootstrap callback mismatched identities without touching the real
// proc table. The process uses native_dawn_bindings() only.

#include "dawn/dawn_proc_table.h"

#include <cstdint>

namespace gpu_runtime::detail {

using NativeDawnVersion = const std::uint8_t* (*)();
using NativeDawnProcs = const DawnProcTable& (*)();
using NativeDawnSetter = void (*)(const DawnProcTable*);

struct NativeDawnBindings final {
    NativeDawnVersion header_version = nullptr;
    NativeDawnVersion proc_version = nullptr;
    NativeDawnProcs native_procs = nullptr;
    NativeDawnSetter set_procs = nullptr;
};

NativeDawnBindings& native_dawn_bindings();

}  // namespace gpu_runtime::detail
