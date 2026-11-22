#pragma once

#include <vellum/runtime/detail/durable_file_replacement.hpp>

namespace vellum::runtime::detail {

struct DurableFileReplacementTestAccess {
    static void fail_parent_directory_sync(DurableFileReplacement& replacement) noexcept;
};

} // namespace vellum::runtime::detail
