#pragma once

// RAII dynamic library loader — wraps dlopen/LoadLibrary

#include <string>
#include <string_view>

namespace pulp::runtime {

class DynamicLibrary {
public:
    DynamicLibrary() = default;
    ~DynamicLibrary();

    /// Load a shared library by path. Returns false on failure.
    bool open(std::string_view path);

    /// Close the library.
    void close();

    /// Look up a symbol by name. Returns nullptr if not found.
    void* find_symbol(std::string_view name);

    /// Whether a library is currently loaded.
    bool is_open() const { return handle_ != nullptr; }

    /// Last error message (platform-specific).
    const std::string& error() const { return error_; }

    // No copy
    DynamicLibrary(const DynamicLibrary&) = delete;
    DynamicLibrary& operator=(const DynamicLibrary&) = delete;

    // Move
    DynamicLibrary(DynamicLibrary&& other) noexcept;
    DynamicLibrary& operator=(DynamicLibrary&& other) noexcept;

private:
    void* handle_ = nullptr;
    std::string error_;
};

}  // namespace pulp::runtime
