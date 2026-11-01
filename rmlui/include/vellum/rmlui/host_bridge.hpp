#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

namespace vellum::rmlui {

// Renderer and RmlUi headers deliberately stay outside this contract. An
// adapter must explicitly provide every capability before activation.
enum class HostStatus : std::uint8_t {
    ok,
    missing_capability,
    already_bound,
    not_active,
    rejected,
};

enum class HostEventKind : std::uint8_t {
    parameter,
    action,
    selection,
};

struct HostEvent {
    HostEventKind kind = HostEventKind::action;
    std::string id;
    double value = 0.0;
};

class LuaHost {
  public:
    virtual ~LuaHost() = default;
    // The implementation owns the Lua state and applies its capability policy.
    virtual HostStatus initialise() = 0;
    virtual HostStatus dispatch(std::string_view callback) = 0;
};

struct ElementRegistration {
    std::string name;
    std::function<HostStatus(std::string_view)> dispatch;
};

class ElementHost {
  public:
    virtual ~ElementHost() = default;
    virtual HostStatus register_element(ElementRegistration registration) = 0;
};

class EventHost {
  public:
    virtual ~EventHost() = default;
    virtual HostStatus dispatch(HostEvent event) = 0;
};

// A small, fail-closed handoff between an RmlUi adapter and the application
// that hosts it. It provides no RmlUi or Lua state access: concrete adapters
// retain those details and expose only typed lifecycle and host events here.
// All methods are UI-thread calls and must be serialized by the adapter.
// Provider calls are synchronous; callback and registration names are copied
// at the boundary, and provider exceptions are converted to rejected status.
// A retained element callback is part of the same UI-thread serialization
// contract: providers must invoke it on the owning UI thread and must not race
// close(). close() releases the current graph; a fresh provider set may then
// be bound and activated.
class HostBridge final {
  public:
    HostBridge() = default;
    ~HostBridge() {
        close();
    }
    HostBridge(const HostBridge&) = delete;
    HostBridge& operator=(const HostBridge&) = delete;
    HostBridge(HostBridge&&) = delete;
    HostBridge& operator=(HostBridge&&) = delete;

    HostStatus bind_lua(std::shared_ptr<LuaHost> host) noexcept {
        if (!host)
            return HostStatus::rejected;
        if (lua_ || active_ || generation_exhausted_)
            return HostStatus::already_bound;
        if (!advance_generation())
            return HostStatus::rejected;
        lua_ = std::move(host);
        return HostStatus::ok;
    }

    HostStatus bind_elements(std::shared_ptr<ElementHost> host) noexcept {
        if (!host)
            return HostStatus::rejected;
        if (elements_ || active_ || generation_exhausted_)
            return HostStatus::already_bound;
        if (!advance_generation())
            return HostStatus::rejected;
        elements_ = std::move(host);
        return HostStatus::ok;
    }

    HostStatus bind_events(std::shared_ptr<EventHost> host) noexcept {
        if (!host)
            return HostStatus::rejected;
        if (events_ || active_ || generation_exhausted_)
            return HostStatus::already_bound;
        if (!advance_generation())
            return HostStatus::rejected;
        events_ = std::move(host);
        return HostStatus::ok;
    }

    HostStatus register_element(ElementRegistration registration) {
        if (!active_)
            return HostStatus::not_active;
        if (registering_)
            return HostStatus::already_bound;
        if (registration.name.empty() || !registration.dispatch)
            return HostStatus::rejected;
        auto elements = elements_;
        const auto identity = gate_->identity.load(std::memory_order_acquire);
        auto gate = gate_;
        auto dispatch = std::move(registration.dispatch);
        registration.dispatch = [weak_gate = std::weak_ptr<BridgeGate>(gate), identity,
                                 dispatch = std::move(dispatch)](std::string_view value) mutable {
            const auto locked = weak_gate.lock();
            if (!locked || locked->identity.load(std::memory_order_acquire) != identity ||
                !is_active_identity(identity))
                return HostStatus::not_active;
            try {
                return dispatch(value);
            } catch (...) {
                return HostStatus::rejected;
            }
        };
        registering_ = true;
        try {
            const auto status = elements->register_element(std::move(registration));
            registering_ = false;
            return active_ && elements_ == elements &&
                           gate_->identity.load(std::memory_order_acquire) == identity
                       ? status
                       : HostStatus::rejected;
        } catch (...) {
            registering_ = false;
            close();
            return HostStatus::rejected;
        }
    }

    HostStatus activate() {
        if (active_ || activating_)
            return HostStatus::already_bound;
        if (!lua_ || !elements_ || !events_)
            return HostStatus::missing_capability;
        auto lua = lua_;
        if (!advance_generation())
            return HostStatus::rejected;
        const auto generation = generation_;
        activating_ = true;
        HostStatus status = HostStatus::rejected;
        try {
            status = lua->initialise();
        } catch (...) {
            activating_ = false;
            close();
            return HostStatus::rejected;
        }
        activating_ = false;
        if (status != HostStatus::ok)
            return status;
        if (generation_ != generation || lua_ != lua || !elements_ || !events_)
            return HostStatus::rejected;
        active_ = true;
        gate_->identity.store(pack_identity(true, generation_), std::memory_order_release);
        return HostStatus::ok;
    }

    HostStatus dispatch_lua(std::string_view callback) {
        if (!active_)
            return HostStatus::not_active;
        if (callback.empty())
            return HostStatus::rejected;
        auto lua = lua_;
        const auto generation = generation_;
        try {
            const auto status = lua->dispatch(callback);
            return active_ && lua_ == lua && generation_ == generation ? status
                                                                       : HostStatus::rejected;
        } catch (...) {
            close();
            return HostStatus::rejected;
        }
    }

    HostStatus dispatch_event(HostEvent event) {
        if (!active_)
            return HostStatus::not_active;
        if (event.id.empty() || !valid_event_kind(event.kind))
            return HostStatus::rejected;
        auto events = events_;
        const auto generation = generation_;
        try {
            const auto status = events->dispatch(std::move(event));
            return active_ && events_ == events && generation_ == generation
                       ? status
                       : HostStatus::rejected;
        } catch (...) {
            close();
            return HostStatus::rejected;
        }
    }

    void close() noexcept {
        active_ = false;
        activating_ = false;
        registering_ = false;
        (void)advance_generation();
        gate_->identity.store(pack_identity(false, generation_), std::memory_order_release);
        events_.reset();
        elements_.reset();
        lua_.reset();
    }
    [[nodiscard]] bool active() const noexcept {
        return active_;
    }

  private:
    struct BridgeGate {
        // The active bit and generation are one publication unit. Retained
        // callbacks must never observe a newly active bridge with an older
        // generation between two separate stores.
        std::atomic<std::uint64_t> identity{0};
    };

    static constexpr std::uint64_t kMaxGeneration = std::numeric_limits<std::uint64_t>::max() >> 1;

    static constexpr std::uint64_t pack_identity(bool active, std::uint64_t generation) noexcept {
        return (generation << 1) | (active ? 1u : 0u);
    }

    static constexpr bool is_active_identity(std::uint64_t identity) noexcept {
        return (identity & 1u) != 0;
    }

    bool advance_generation() noexcept {
        // A wrapped identity would make an old retained callback valid again.
        // Exhaust the bridge instead of allowing ABA after the finite counter.
        if (generation_exhausted_ || generation_ == kMaxGeneration) {
            generation_exhausted_ = true;
            return false;
        }
        ++generation_;
        return true;
    }

    static bool valid_event_kind(HostEventKind kind) noexcept {
        switch (kind) {
        case HostEventKind::parameter:
        case HostEventKind::action:
        case HostEventKind::selection:
            return true;
        }
        return false;
    }

    std::shared_ptr<LuaHost> lua_;
    std::shared_ptr<ElementHost> elements_;
    std::shared_ptr<EventHost> events_;
    bool active_ = false;
    bool activating_ = false;
    bool registering_ = false;
    std::uint64_t generation_ = 0;
    bool generation_exhausted_ = false;
    std::shared_ptr<BridgeGate> gate_ = std::make_shared<BridgeGate>();
};

} // namespace vellum::rmlui
