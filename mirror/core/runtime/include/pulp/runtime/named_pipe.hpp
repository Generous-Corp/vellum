#pragma once

// Named pipe for IPC — POSIX mkfifo / Windows CreateNamedPipe.

#include <string>
#include <string_view>
#include <optional>
#include <vector>
#include <cstdint>

namespace pulp::runtime {

class NamedPipe {
public:
    NamedPipe() = default;
    ~NamedPipe();

    /// Create and open a pipe as server (creates the pipe).
    /// On POSIX, name is a filesystem path. On Windows, it's a pipe name.
    bool create_server(std::string_view name);

    /// Connect to an existing pipe as client.
    bool connect_client(std::string_view name);

    /// Write data to the pipe. Returns bytes written, or -1 on error.
    int write(const uint8_t* data, size_t length);
    int write(std::string_view data);

    /// Read data from the pipe. Returns bytes read, or -1 on error. 0 = EOF.
    int read(uint8_t* buffer, size_t buffer_size);

    /// Read all available data as a string.
    std::optional<std::string> read_string(size_t max_size = 65536);

    /// Close the pipe.
    void close();

    /// Whether the pipe is open.
    bool is_open() const;

    // No copy, move OK
    NamedPipe(const NamedPipe&) = delete;
    NamedPipe& operator=(const NamedPipe&) = delete;
    NamedPipe(NamedPipe&& other) noexcept;
    NamedPipe& operator=(NamedPipe&& other) noexcept;

private:
    std::string name_;
    bool is_server_ = false;
#ifdef _WIN32
    void* handle_ = nullptr;
#else
    int fd_ = -1;
#endif
};

}  // namespace pulp::runtime
