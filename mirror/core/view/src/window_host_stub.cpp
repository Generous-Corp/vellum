#include <pulp/view/window_host.hpp>

namespace pulp::view {

std::unique_ptr<WindowHost> WindowHost::create(View&, const WindowOptions&) {
    return nullptr;
}

} // namespace pulp::view
