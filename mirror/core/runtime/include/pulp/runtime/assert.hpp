#pragma once

#include <pulp/platform/detect.hpp>
#include <source_location>
#include <string_view>
#include <cstdio>
#include <cstdlib>

namespace pulp::runtime {

[[noreturn]] inline void assert_fail(
    std::string_view expr,
    std::string_view msg,
    std::source_location loc = std::source_location::current())
{
    std::fprintf(stderr,
        "[pulp:ASSERT] %.*s\n"
        "  expression: %.*s\n"
        "  file: %s:%d\n"
        "  function: %s\n",
        static_cast<int>(msg.size()), msg.data(),
        static_cast<int>(expr.size()), expr.data(),
        loc.file_name(), loc.line(),
        loc.function_name());
    std::abort();
}

} // namespace pulp::runtime

// PULP_ASSERT: always checked (debug and release)
#define PULP_ASSERT(expr, msg) \
    do { \
        if (!(expr)) [[unlikely]] { \
            ::pulp::runtime::assert_fail(#expr, msg); \
        } \
    } while (false)

// PULP_DBG_ASSERT: checked only in debug builds
#if defined(NDEBUG)
    #define PULP_DBG_ASSERT(expr, msg) ((void)0)
#else
    #define PULP_DBG_ASSERT(expr, msg) PULP_ASSERT(expr, msg)
#endif
