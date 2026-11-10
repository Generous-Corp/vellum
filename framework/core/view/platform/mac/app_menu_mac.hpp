#pragma once

#include <vellum/view/window_host.hpp>

#include <functional>
#include <vector>

namespace vellum::view::mac_menu {

void install_application_menu(const std::vector<WindowOptions::MenuCommand>& commands,
                              std::function<void()> quit_action);

} // namespace vellum::view::mac_menu
