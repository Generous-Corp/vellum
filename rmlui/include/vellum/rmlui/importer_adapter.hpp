#pragma once

#include <vellum/rmlui/host_bridge.hpp>

#include <memory>
#include <string>
#include <string_view>

namespace vellum::rmlui {

struct ImporterCapabilityReceipt {
    bool host_bridge_active = false;
    bool lua_initialised = false;
    bool elements_registered = false;
    bool event_callbacks = false;
};

// Importer handoff. It deliberately does not construct RmlUi, expose a Lua
// state, or reach private application types. The importer supplies capability
// providers and receives a typed receipt only after activation.
class ImporterAdapter final {
  public:
    ImporterAdapter() = default;
    ~ImporterAdapter() = default;
    ImporterAdapter(const ImporterAdapter&) = delete;
    ImporterAdapter& operator=(const ImporterAdapter&) = delete;

    HostStatus bind_lua(std::shared_ptr<LuaHost> host) noexcept {
        return bridge_.bind_lua(std::move(host));
    }
    HostStatus bind_elements(std::shared_ptr<ElementHost> host) noexcept {
        return bridge_.bind_elements(std::move(host));
    }
    HostStatus bind_events(std::shared_ptr<EventHost> host) noexcept {
        return bridge_.bind_events(std::move(host));
    }
    HostStatus activate() {
        const auto status = bridge_.activate();
        if (status == HostStatus::ok) {
            receipt_.host_bridge_active = true;
            receipt_.lua_initialised = true;
            receipt_.event_callbacks = true;
        } else {
            sync_receipt();
        }
        return status;
    }
    HostStatus register_element(ElementRegistration registration) {
        const auto status = bridge_.register_element(std::move(registration));
        if (status == HostStatus::ok)
            receipt_.elements_registered = true;
        sync_receipt();
        return status;
    }
    HostStatus dispatch_event(HostEvent event) {
        const auto status = bridge_.dispatch_event(std::move(event));
        sync_receipt();
        return status;
    }
    void close() noexcept {
        bridge_.close();
        receipt_ = {};
    }
    [[nodiscard]] const ImporterCapabilityReceipt& receipt() const noexcept {
        return receipt_;
    }

  private:
    void sync_receipt() noexcept {
        // HostBridge quarantines itself when a provider throws. The receipt is
        // the importer's typed capability proof, so it must never continue to
        // advertise a live event/Lua graph after that boundary closes.
        if (!bridge_.active())
            receipt_ = {};
    }

    HostBridge bridge_;
    ImporterCapabilityReceipt receipt_;
};

} // namespace vellum::rmlui
