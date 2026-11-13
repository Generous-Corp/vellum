// A minimal test runner for the framework tests: each FRAMEWORK_TEST registers
// a case, CHECK records a failure with its location and keeps going, and
// run_all() prints one line per case and returns nonzero if any case failed.
#pragma once

#include <cstdio>
#include <exception>
#include <functional>
#include <vector>

namespace framework_test {

struct Case {
    const char* name;
    std::function<void()> body;
};

inline std::vector<Case>& cases() {
    static std::vector<Case> registry;
    return registry;
}

inline int& failures_in_case() {
    static int count = 0;
    return count;
}

struct Registrar {
    Registrar(const char* name, std::function<void()> body) { cases().push_back({name, std::move(body)}); }
};

inline void record_failure(const char* expression, const char* file, int line) {
    ++failures_in_case();
    std::fprintf(stderr, "  %s:%d: CHECK(%s) failed\n", file, line, expression);
}

inline int run_all() {
    int failed = 0;
    for (const auto& test : cases()) {
        failures_in_case() = 0;
        try {
            test.body();
        } catch (const std::exception& error) {
            ++failures_in_case();
            std::fprintf(stderr, "  unexpected exception: %s\n", error.what());
        } catch (...) {
            ++failures_in_case();
            std::fprintf(stderr, "  unexpected non-standard exception\n");
        }
        const bool passed = failures_in_case() == 0;
        std::printf("%s %s\n", passed ? "PASS" : "FAIL", test.name);
        if (!passed)
            ++failed;
    }
    std::printf("%zu cases, %d failed\n", cases().size(), failed);
    return cases().empty() || failed != 0 ? 1 : 0;
}

} // namespace framework_test

#define FRAMEWORK_TEST(name)                                                   \
    static void name();                                                        \
    static const ::framework_test::Registrar name##_registrar{#name, &name};   \
    static void name()

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression))                                                     \
            ::framework_test::record_failure(#expression, __FILE__, __LINE__); \
    } while (false)
