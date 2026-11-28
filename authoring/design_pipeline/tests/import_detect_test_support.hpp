// The small Catch2-style surface the import detector tests use: TEST_CASE,
// SECTION, REQUIRE, CHECK, their _FALSE forms and INFO. Every case runs once;
// SECTION blocks run in order on the shared setup, which holds for these tests
// because no section changes state another section reads. A failed REQUIRE
// ends its case; a failed CHECK records the failure and continues.

#pragma once

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <filesystem>
#include <fstream>
#include <functional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <unistd.h>

namespace import_detect_test {

struct Case {
    const char* name;
    std::function<void()> body;
};

inline std::vector<Case>& cases() {
    static std::vector<Case> registry;
    return registry;
}

inline int& failures() {
    static int count = 0;
    return count;
}

inline std::string& context() {
    static std::string text;
    return text;
}

struct Registrar {
    Registrar(const char* name, std::function<void()> body) {
        cases().push_back({name, std::move(body)});
    }
};

struct RequireFailed : std::exception {};

inline void fail(const char* kind, const char* expression, const char* file, int line) {
    ++failures();
    std::fprintf(stderr, "  %s:%d: %s(%s) failed%s%s\n", file, line, kind, expression,
                 context().empty() ? "" : " with ", context().c_str());
}

inline int run_all() {
    int failed = 0;
    for (const auto& test : cases()) {
        failures() = 0;
        context().clear();
        try {
            test.body();
        } catch (const RequireFailed&) {
        } catch (const std::exception& error) {
            ++failures();
            std::fprintf(stderr, "  unexpected exception: %s\n", error.what());
        }
        std::printf("%s %s\n", failures() == 0 ? "PASS" : "FAIL", test.name);
        if (failures() != 0) ++failed;
    }
    std::printf("%zu cases, %d failed\n", cases().size(), failed);
    return cases().empty() || failed != 0 ? 1 : 0;
}

// A fresh directory under the system temp directory, removed on scope exit.
inline std::filesystem::path make_unique_temp_dir(const std::string& prefix) {
    static std::atomic<std::uint64_t> serial{0};
    const auto parent = std::filesystem::temp_directory_path();
    for (int attempt = 0; attempt < 1000; ++attempt) {
        const auto candidate = parent / (prefix + "-" + std::to_string(::getpid()) + "-" +
                                         std::to_string(serial.fetch_add(1)));
        std::error_code ec;
        if (std::filesystem::create_directory(candidate, ec)) return candidate;
        if (ec) throw std::runtime_error("cannot create " + candidate.string());
    }
    throw std::runtime_error("no free temp directory name for " + prefix);
}

} // namespace import_detect_test

#define IDT_CONCAT2(a, b) a##b
#define IDT_CONCAT(a, b) IDT_CONCAT2(a, b)
#define IDT_CASE(id, name)                                                          \
    static void id();                                                               \
    static const ::import_detect_test::Registrar IDT_CONCAT(id, _registrar){name, &id}; \
    static void id()
#define TEST_CASE(name, ...) IDT_CASE(IDT_CONCAT(import_detect_case_, __LINE__), name)
#define SECTION(name) if (true)
#define INFO(message)                                                               \
    do {                                                                            \
        std::ostringstream idt_stream;                                              \
        idt_stream << message;                                                      \
        ::import_detect_test::context() = idt_stream.str();                         \
    } while (false)
#define CHECK(...)                                                                  \
    do {                                                                            \
        if (!(__VA_ARGS__))                                                         \
            ::import_detect_test::fail("CHECK", #__VA_ARGS__, __FILE__, __LINE__);  \
    } while (false)
#define CHECK_FALSE(...) CHECK(!(__VA_ARGS__))
#define REQUIRE(...)                                                                \
    do {                                                                            \
        if (!(__VA_ARGS__)) {                                                       \
            ::import_detect_test::fail("REQUIRE", #__VA_ARGS__, __FILE__, __LINE__); \
            throw ::import_detect_test::RequireFailed{};                            \
        }                                                                           \
    } while (false)
#define REQUIRE_FALSE(...) REQUIRE(!(__VA_ARGS__))
