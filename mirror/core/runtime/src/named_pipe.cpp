#include <pulp/runtime/named_pipe.hpp>
#include <cstring>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace pulp::runtime {

NamedPipe::~NamedPipe() { close(); }

NamedPipe::NamedPipe(NamedPipe&& other) noexcept
    : name_(std::move(other.name_)), is_server_(other.is_server_)
#ifdef _WIN32
    , handle_(other.handle_)
#else
    , fd_(other.fd_)
#endif
{
#ifdef _WIN32
    other.handle_ = nullptr;
#else
    other.fd_ = -1;
#endif
    other.is_server_ = false;
}

NamedPipe& NamedPipe::operator=(NamedPipe&& other) noexcept {
    if (this != &other) {
        close();
        name_ = std::move(other.name_);
        is_server_ = other.is_server_;
#ifdef _WIN32
        handle_ = other.handle_;
        other.handle_ = nullptr;
#else
        fd_ = other.fd_;
        other.fd_ = -1;
#endif
        other.is_server_ = false;
    }
    return *this;
}

#ifdef _WIN32

bool NamedPipe::create_server(std::string_view name) {
    close();
    name_ = "\\\\.\\pipe\\" + std::string(name);
    is_server_ = true;

    handle_ = CreateNamedPipeA(name_.c_str(),
        PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1, 65536, 65536, 0, nullptr);

    if (handle_ == INVALID_HANDLE_VALUE) {
        handle_ = nullptr;
        return false;
    }

    // Wait for client to connect
    ConnectNamedPipe(handle_, nullptr);
    return true;
}

bool NamedPipe::connect_client(std::string_view name) {
    close();
    name_ = "\\\\.\\pipe\\" + std::string(name);
    is_server_ = false;

    handle_ = CreateFileA(name_.c_str(), GENERIC_READ | GENERIC_WRITE,
        0, nullptr, OPEN_EXISTING, 0, nullptr);

    if (handle_ == INVALID_HANDLE_VALUE) {
        handle_ = nullptr;
        return false;
    }
    return true;
}

int NamedPipe::write(const uint8_t* data, size_t length) {
    if (!handle_) return -1;
    DWORD written;
    if (!WriteFile(handle_, data, static_cast<DWORD>(length), &written, nullptr))
        return -1;
    return static_cast<int>(written);
}

int NamedPipe::read(uint8_t* buffer, size_t buffer_size) {
    if (!handle_) return -1;
    DWORD bytes_read;
    if (!ReadFile(handle_, buffer, static_cast<DWORD>(buffer_size), &bytes_read, nullptr))
        return -1;
    return static_cast<int>(bytes_read);
}

void NamedPipe::close() {
    if (handle_) {
        if (is_server_) DisconnectNamedPipe(handle_);
        CloseHandle(handle_);
        handle_ = nullptr;
    }
}

bool NamedPipe::is_open() const { return handle_ != nullptr; }

#else  // POSIX

bool NamedPipe::create_server(std::string_view name) {
    close();
    name_ = std::string(name);
    is_server_ = true;

    // Remove existing pipe if present
    unlink(name_.c_str());

    if (mkfifo(name_.c_str(), 0666) != 0)
        return false;

    // Open for read-write so it doesn't block waiting for the other end
    fd_ = ::open(name_.c_str(), O_RDWR);
    return fd_ >= 0;
}

bool NamedPipe::connect_client(std::string_view name) {
    close();
    name_ = std::string(name);
    is_server_ = false;

    fd_ = ::open(name_.c_str(), O_RDWR);
    return fd_ >= 0;
}

int NamedPipe::write(const uint8_t* data, size_t length) {
    if (fd_ < 0) return -1;
    return static_cast<int>(::write(fd_, data, length));
}

int NamedPipe::read(uint8_t* buffer, size_t buffer_size) {
    if (fd_ < 0) return -1;
    return static_cast<int>(::read(fd_, buffer, buffer_size));
}

void NamedPipe::close() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
    if (is_server_ && !name_.empty()) {
        unlink(name_.c_str());
        is_server_ = false;
    }
}

bool NamedPipe::is_open() const { return fd_ >= 0; }

#endif

int NamedPipe::write(std::string_view data) {
    return write(reinterpret_cast<const uint8_t*>(data.data()), data.size());
}

std::optional<std::string> NamedPipe::read_string(size_t max_size) {
    std::vector<uint8_t> buffer(max_size);
    int n = read(buffer.data(), buffer.size());
    if (n < 0) return std::nullopt;
    return std::string(reinterpret_cast<char*>(buffer.data()), static_cast<size_t>(n));
}

}  // namespace pulp::runtime
