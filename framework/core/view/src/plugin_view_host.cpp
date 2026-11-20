#include <vellum/view/plugin_view_host.hpp>

#include <mutex>
#include <utility>

namespace vellum::view {
namespace detail {
std::unique_ptr<PluginViewHost> make_platform_plugin_view_host(
    View&, const PluginViewHost::Options&);
}
namespace {
std::mutex& factory_mutex() { static std::mutex m; return m; }
PluginViewHost::Factory& factory_slot() { static PluginViewHost::Factory f; return f; }
}

void PluginViewHost::set_factory(Factory f) { std::lock_guard lock(factory_mutex()); factory_slot() = std::move(f); }
void PluginViewHost::clear_factory() { std::lock_guard lock(factory_mutex()); factory_slot() = {}; }
bool PluginViewHost::has_factory() { std::lock_guard lock(factory_mutex()); return static_cast<bool>(factory_slot()); }
void register_platform_plugin_view_host() {}

std::unique_ptr<PluginViewHost> PluginViewHost::create(View& root, Size size) {
    Options options;
    options.size = size;
    return create(root, options);
}

std::unique_ptr<PluginViewHost> PluginViewHost::create(View& root, const Options& options) {
    Factory f;
    { std::lock_guard lock(factory_mutex()); f = factory_slot(); }
    auto host = f ? f(root, options) : detail::make_platform_plugin_view_host(root, options);
    if (host) {
        root.set_plugin_view_host(host.get());
        host->root_view_ = &root;
    }
    return host;
}
} // namespace vellum::view

