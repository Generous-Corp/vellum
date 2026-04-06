#include <pulp/view/plugin_view_host.hpp>

namespace pulp::view {

std::unique_ptr<PluginViewHost> PluginViewHost::create(View&, Size) {
    return nullptr;
}

std::unique_ptr<PluginViewHost> PluginViewHost::create(View&, const Options&) {
    return nullptr;
}

} // namespace pulp::view
