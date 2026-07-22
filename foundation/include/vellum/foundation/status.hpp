#pragma once

#include <string>

namespace vellum::foundation {

class Status final {
public:
    static Status success();
    static Status failure(std::string message);

    [[nodiscard]] bool ok() const noexcept { return ok_; }
    [[nodiscard]] const std::string& message() const noexcept { return message_; }
    explicit operator bool() const noexcept { return ok(); }

private:
    Status(bool ok, std::string message);

    bool ok_ = false;
    std::string message_;
};

}  // namespace vellum::foundation
