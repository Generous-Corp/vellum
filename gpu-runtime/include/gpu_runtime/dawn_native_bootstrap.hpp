#pragma once

// dawn_native_bootstrap.hpp — the native Dawn bootstrap, the only code that
// calls Dawn's proc-table setter. It checks that the Dawn headers this library
// was compiled against, the proc library and the native procs all report the
// same revision before installing Dawn's native procs.
//
// The declarations name no Dawn type; the implementation lives in
// VellumGpuRuntime::Device.

#include <gpu_runtime/dawn_bootstrap.hpp>

#include <string>

namespace gpu_runtime {

/// The Dawn revision this library was compiled against, as lowercase hex.
[[nodiscard]] std::string native_dawn_revision();

/// The native bootstrap as an ABI v1 contract, for register_dawn_bootstrap().
[[nodiscard]] DawnBootstrap native_dawn_bootstrap();

/// Register the native bootstrap for `bundle` (empty: an ABI v1 registration)
/// at this library's revision and runtime version.
[[nodiscard]] bool register_native_dawn_bootstrap(std::string_view bundle = {},
                                                  std::string* error = nullptr);

}  // namespace gpu_runtime
