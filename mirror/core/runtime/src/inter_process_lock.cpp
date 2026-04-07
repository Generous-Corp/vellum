#include <pulp/runtime/inter_process_lock.hpp>
#include <filesystem>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace pulp::runtime {

InterProcessLock::InterProcessLock(std::string_view name) {
    auto tmp = std::filesystem::temp_directory_path();
    lock_path_ = (tmp / ("pulp_lock_" + std::string(name))).string();
}

InterProcessLock::~InterProcessLock() {
    unlock();
}

#ifdef _WIN32

bool InterProcessLock::try_lock() {
    if (locked_) return true;

    handle_ = CreateFileA(lock_path_.c_str(), GENERIC_WRITE,
                          0, nullptr, CREATE_ALWAYS,
                          FILE_ATTRIBUTE_HIDDEN | FILE_FLAG_DELETE_ON_CLOSE,
                          nullptr);
    if (handle_ == INVALID_HANDLE_VALUE) {
        handle_ = nullptr;
        return false;
    }
    locked_ = true;
    return true;
}

void InterProcessLock::unlock() {
    if (handle_) {
        CloseHandle(handle_);
        handle_ = nullptr;
    }
    locked_ = false;
}

#else  // POSIX

bool InterProcessLock::try_lock() {
    if (locked_) return true;

    fd_ = ::open(lock_path_.c_str(), O_CREAT | O_WRONLY, 0644);
    if (fd_ < 0)
        return false;

    if (flock(fd_, LOCK_EX | LOCK_NB) != 0) {
        ::close(fd_);
        fd_ = -1;
        return false;
    }

    locked_ = true;
    return true;
}

void InterProcessLock::unlock() {
    if (fd_ >= 0) {
        flock(fd_, LOCK_UN);
        ::close(fd_);
        fd_ = -1;
        std::error_code ec;
        std::filesystem::remove(lock_path_, ec);
    }
    locked_ = false;
}

#endif

}  // namespace pulp::runtime
