#include <pulp/runtime/memory_mapped_file.hpp>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace pulp::runtime {

MemoryMappedFile::~MemoryMappedFile() {
    close();
}

MemoryMappedFile::MemoryMappedFile(MemoryMappedFile&& other) noexcept
    : data_(other.data_), size_(other.size_)
#ifdef _WIN32
    , file_handle_(other.file_handle_), mapping_handle_(other.mapping_handle_)
#else
    , fd_(other.fd_)
#endif
{
    other.data_ = nullptr;
    other.size_ = 0;
#ifdef _WIN32
    other.file_handle_ = nullptr;
    other.mapping_handle_ = nullptr;
#else
    other.fd_ = -1;
#endif
}

MemoryMappedFile& MemoryMappedFile::operator=(MemoryMappedFile&& other) noexcept {
    if (this != &other) {
        close();
        data_ = other.data_;
        size_ = other.size_;
#ifdef _WIN32
        file_handle_ = other.file_handle_;
        mapping_handle_ = other.mapping_handle_;
        other.file_handle_ = nullptr;
        other.mapping_handle_ = nullptr;
#else
        fd_ = other.fd_;
        other.fd_ = -1;
#endif
        other.data_ = nullptr;
        other.size_ = 0;
    }
    return *this;
}

#ifdef _WIN32

bool MemoryMappedFile::open(std::string_view path, MapMode mode) {
    close();

    DWORD access = (mode == MapMode::ReadWrite) ? GENERIC_READ | GENERIC_WRITE : GENERIC_READ;
    DWORD share = FILE_SHARE_READ;
    std::string path_str(path);

    file_handle_ = CreateFileA(path_str.c_str(), access, share, nullptr,
                               OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file_handle_ == INVALID_HANDLE_VALUE) {
        file_handle_ = nullptr;
        return false;
    }

    LARGE_INTEGER file_size;
    if (!GetFileSizeEx(file_handle_, &file_size)) {
        close();
        return false;
    }
    size_ = static_cast<size_t>(file_size.QuadPart);

    if (size_ == 0) {
        close();
        return false;
    }

    DWORD protect = (mode == MapMode::ReadWrite) ? PAGE_READWRITE : PAGE_READONLY;
    mapping_handle_ = CreateFileMappingA(file_handle_, nullptr, protect, 0, 0, nullptr);
    if (!mapping_handle_) {
        close();
        return false;
    }

    DWORD map_access = (mode == MapMode::ReadWrite) ? FILE_MAP_WRITE : FILE_MAP_READ;
    data_ = static_cast<uint8_t*>(MapViewOfFile(mapping_handle_, map_access, 0, 0, 0));
    if (!data_) {
        close();
        return false;
    }

    return true;
}

void MemoryMappedFile::close() {
    if (data_) {
        UnmapViewOfFile(data_);
        data_ = nullptr;
    }
    if (mapping_handle_) {
        CloseHandle(mapping_handle_);
        mapping_handle_ = nullptr;
    }
    if (file_handle_) {
        CloseHandle(file_handle_);
        file_handle_ = nullptr;
    }
    size_ = 0;
}

#else  // POSIX

bool MemoryMappedFile::open(std::string_view path, MapMode mode) {
    close();

    int flags = (mode == MapMode::ReadWrite) ? O_RDWR : O_RDONLY;
    std::string path_str(path);
    fd_ = ::open(path_str.c_str(), flags);
    if (fd_ < 0)
        return false;

    struct stat st;
    if (fstat(fd_, &st) != 0) {
        close();
        return false;
    }
    size_ = static_cast<size_t>(st.st_size);

    if (size_ == 0) {
        close();
        return false;
    }

    int prot = (mode == MapMode::ReadWrite) ? (PROT_READ | PROT_WRITE) : PROT_READ;
    void* ptr = mmap(nullptr, size_, prot, MAP_SHARED, fd_, 0);
    if (ptr == MAP_FAILED) {
        close();
        return false;
    }

    data_ = static_cast<uint8_t*>(ptr);
    return true;
}

void MemoryMappedFile::close() {
    if (data_) {
        munmap(data_, size_);
        data_ = nullptr;
    }
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
    size_ = 0;
}

#endif

}  // namespace pulp::runtime
