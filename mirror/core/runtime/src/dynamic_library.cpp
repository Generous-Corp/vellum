#include <pulp/runtime/dynamic_library.hpp>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace pulp::runtime {

DynamicLibrary::~DynamicLibrary() {
    close();
}

DynamicLibrary::DynamicLibrary(DynamicLibrary&& other) noexcept
    : handle_(other.handle_), error_(std::move(other.error_)) {
    other.handle_ = nullptr;
}

DynamicLibrary& DynamicLibrary::operator=(DynamicLibrary&& other) noexcept {
    if (this != &other) {
        close();
        handle_ = other.handle_;
        error_ = std::move(other.error_);
        other.handle_ = nullptr;
    }
    return *this;
}

#ifdef _WIN32

bool DynamicLibrary::open(std::string_view path) {
    close();
    std::string path_str(path);
    handle_ = LoadLibraryA(path_str.c_str());
    if (!handle_) {
        error_ = "LoadLibrary failed (error " + std::to_string(GetLastError()) + ")";
        return false;
    }
    error_.clear();
    return true;
}

void DynamicLibrary::close() {
    if (handle_) {
        FreeLibrary(static_cast<HMODULE>(handle_));
        handle_ = nullptr;
    }
}

void* DynamicLibrary::find_symbol(std::string_view name) {
    if (!handle_) return nullptr;
    std::string name_str(name);
    void* sym = reinterpret_cast<void*>(
        GetProcAddress(static_cast<HMODULE>(handle_), name_str.c_str()));
    if (!sym)
        error_ = "GetProcAddress failed for '" + name_str + "'";
    return sym;
}

#else  // POSIX

bool DynamicLibrary::open(std::string_view path) {
    close();
    std::string path_str(path);
    handle_ = dlopen(path_str.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!handle_) {
        const char* err = dlerror();
        error_ = err ? err : "dlopen failed";
        return false;
    }
    error_.clear();
    return true;
}

void DynamicLibrary::close() {
    if (handle_) {
        dlclose(handle_);
        handle_ = nullptr;
    }
}

void* DynamicLibrary::find_symbol(std::string_view name) {
    if (!handle_) return nullptr;
    dlerror();  // Clear any existing error
    std::string name_str(name);
    void* sym = dlsym(handle_, name_str.c_str());
    const char* err = dlerror();
    if (err)
        error_ = err;
    return sym;
}

#endif

}  // namespace pulp::runtime
