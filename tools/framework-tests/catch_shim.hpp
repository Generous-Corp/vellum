// A few Catch2 spellings over the framework test runner, so tests ported from a
// Catch2 suite keep their assertions as written. REQUIRE stops the case at the
// first failure (it throws a private type the runner reports once); CHECK is the
// runner's own continue-on-failure check. Tags and INFO text are not used.
#pragma once

#include "framework_test.hpp"

#include <cmath>

namespace catch_shim {
struct RequireFailed {};
} // namespace catch_shim

#define CATCH_SHIM_CAT2(a, b) a##b
#define CATCH_SHIM_CAT(a, b) CATCH_SHIM_CAT2(a, b)
#define TEST_CASE(name, ...)                                                          \
    static void CATCH_SHIM_CAT(shim_case_, __LINE__)();                               \
    static const ::framework_test::Registrar CATCH_SHIM_CAT(shim_registrar_, __LINE__){ \
        name, &CATCH_SHIM_CAT(shim_case_, __LINE__)};                                 \
    static void CATCH_SHIM_CAT(shim_case_, __LINE__)()
#define REQUIRE(...)                                                                   \
    do {                                                                               \
        if (!(__VA_ARGS__)) {                                                          \
            ::framework_test::record_failure(#__VA_ARGS__, __FILE__, __LINE__);        \
            throw ::catch_shim::RequireFailed{};                                       \
        }                                                                              \
    } while (false)
#define REQUIRE_FALSE(...) REQUIRE(!(__VA_ARGS__))
#define CHECK_FALSE(...) CHECK(!(__VA_ARGS__))
#define INFO(...) ((void)0)
#define SUCCEED(...) ((void)0)

namespace Catch {
struct Approx {
    explicit Approx(double value) : value_(value) {}
    friend bool operator==(double lhs, const Approx& rhs) {
        return std::fabs(lhs - rhs.value_) <= 1e-5 * (1.0 + std::fabs(rhs.value_));
    }
private:
    double value_;
};
} // namespace Catch
