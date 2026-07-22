#include <vellum/foundation/status.hpp>

#include <utility>

namespace vellum::foundation {

Status::Status(bool ok, std::string message)
    : ok_(ok), message_(std::move(message)) {}

Status Status::success() {
    return Status(true, {});
}

Status Status::failure(std::string message) {
    return Status(false, std::move(message));
}

}  // namespace vellum::foundation
