#pragma once

// Inter-process file lock — prevents multiple processes from accessing
// the same resource simultaneously.

#include <string>
#include <string_view>

namespace pulp::runtime {

class InterProcessLock {
public:
    /// Create a lock with the given name (used to derive the lock file path).
    explicit InterProcessLock(std::string_view name);
    ~InterProcessLock();

    /// Try to acquire the lock. Returns true if acquired.
    bool try_lock();

    /// Release the lock.
    void unlock();

    /// Whether this instance holds the lock.
    bool is_locked() const { return locked_; }

    // No copy or move
    InterProcessLock(const InterProcessLock&) = delete;
    InterProcessLock& operator=(const InterProcessLock&) = delete;

private:
    std::string lock_path_;
    bool locked_ = false;
#ifdef _WIN32
    void* handle_ = nullptr;
#else
    int fd_ = -1;
#endif
};

}  // namespace pulp::runtime
