#pragma once

// Memory-mapped file — RAII wrapper for mmap/MapViewOfFile
// Provides read-only or read-write memory-mapped access to files.

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace pulp::runtime {

enum class MapMode { ReadOnly, ReadWrite };

class MemoryMappedFile {
public:
    MemoryMappedFile() = default;
    ~MemoryMappedFile();

    // Open and map a file. Returns false on failure.
    bool open(std::string_view path, MapMode mode = MapMode::ReadOnly);

    // Unmap and close.
    void close();

    // Mapped data pointer (nullptr if not open)
    const uint8_t* data() const { return data_; }
    uint8_t* mutable_data() { return data_; }

    // File size in bytes
    size_t size() const { return size_; }

    // Whether a file is currently mapped
    bool is_open() const { return data_ != nullptr; }

    // No copy
    MemoryMappedFile(const MemoryMappedFile&) = delete;
    MemoryMappedFile& operator=(const MemoryMappedFile&) = delete;

    // Move
    MemoryMappedFile(MemoryMappedFile&& other) noexcept;
    MemoryMappedFile& operator=(MemoryMappedFile&& other) noexcept;

private:
    uint8_t* data_ = nullptr;
    size_t size_ = 0;
#ifdef _WIN32
    void* file_handle_ = nullptr;
    void* mapping_handle_ = nullptr;
#else
    int fd_ = -1;
#endif
};

}  // namespace pulp::runtime
