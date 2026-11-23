// A small test runner with the Catch2 spellings this suite uses (TEST_CASE,
// SECTION, INFO, CHECK, CHECK_FALSE, REQUIRE, REQUIRE_FALSE). Vellum pins no
// Catch2 and the SDK build fetches no extra dependency, so the conformance
// test links this header and its own main() instead of Catch2::Catch2WithMain.
// CHECK records a failure and keeps going; REQUIRE records it and ends the
// case. A SECTION runs once, in order, inside its case.
#pragma once

#include <cstdio>
#include <exception>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

namespace mini_test {

struct Case {
    const char* name;
    std::function<void()> body;
};

struct RequireFailed {};

inline std::vector<Case>& cases() {
    static std::vector<Case> registry;
    return registry;
}
inline int& failures_in_case() {
    static int count = 0;
    return count;
}
inline std::vector<std::function<std::string()>>& info_stack() {
    static std::vector<std::function<std::string()>> stack;
    return stack;
}

struct Registrar {
    Registrar(const char* name, std::function<void()> body) {
        cases().push_back({name, std::move(body)});
    }
};

struct ScopedInfo {
    explicit ScopedInfo(std::function<std::string()> text) { info_stack().push_back(std::move(text)); }
    ~ScopedInfo() { info_stack().pop_back(); }
    ScopedInfo(const ScopedInfo&) = delete;
    ScopedInfo& operator=(const ScopedInfo&) = delete;
};

inline void record_failure(const char* kind, const char* expression, const char* file, int line) {
    ++failures_in_case();
    std::fprintf(stderr, "  %s:%d: %s(%s) failed\n", file, line, kind, expression);
    for (const auto& info : info_stack())
        std::fprintf(stderr, "    with: %s\n", info().c_str());
}

inline int run_all() {
    int failed = 0;
    for (const auto& test : cases()) {
        failures_in_case() = 0;
        try {
            test.body();
        } catch (const RequireFailed&) {
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

} // namespace mini_test

#define MINI_TEST_CAT2(a, b) a##b
#define MINI_TEST_CAT(a, b) MINI_TEST_CAT2(a, b)

#define TEST_CASE(name, ...)                                                              \
    static void MINI_TEST_CAT(mini_test_case_, __LINE__)();                               \
    static const ::mini_test::Registrar MINI_TEST_CAT(mini_test_registrar_, __LINE__){    \
        name, &MINI_TEST_CAT(mini_test_case_, __LINE__)};                                 \
    static void MINI_TEST_CAT(mini_test_case_, __LINE__)()

#define SECTION(name) if (const char* MINI_TEST_CAT(mini_test_section_, __LINE__) = (name); true)

#define INFO(stream)                                                                      \
    const ::mini_test::ScopedInfo MINI_TEST_CAT(mini_test_info_, __LINE__)([&]() {       \
        std::ostringstream mini_test_out;                                                 \
        mini_test_out << stream;                                                          \
        return mini_test_out.str();                                                       \
    })

#define CHECK(...)                                                                        \
    do {                                                                                  \
        if (!(__VA_ARGS__))                                                               \
            ::mini_test::record_failure("CHECK", #__VA_ARGS__, __FILE__, __LINE__);       \
    } while (false)
#define CHECK_FALSE(...)                                                                  \
    do {                                                                                  \
        if ((__VA_ARGS__))                                                                \
            ::mini_test::record_failure("CHECK_FALSE", #__VA_ARGS__, __FILE__, __LINE__); \
    } while (false)
#define REQUIRE(...)                                                                      \
    do {                                                                                  \
        if (!(__VA_ARGS__)) {                                                             \
            ::mini_test::record_failure("REQUIRE", #__VA_ARGS__, __FILE__, __LINE__);     \
            throw ::mini_test::RequireFailed{};                                           \
        }                                                                                 \
    } while (false)
#define REQUIRE_FALSE(...)                                                                \
    do {                                                                                  \
        if ((__VA_ARGS__)) {                                                              \
            ::mini_test::record_failure("REQUIRE_FALSE", #__VA_ARGS__, __FILE__, __LINE__);\
            throw ::mini_test::RequireFailed{};                                           \
        }                                                                                 \
    } while (false)
