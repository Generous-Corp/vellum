#pragma once

// The StateStore-backed HostParamSurface used by Pulp's format adapters, and
// the ParamModel-backed one a Vellum editor binds design views through. The
// framework-agnostic HostParamSurface and HostActionSurface contracts live in
// host_surfaces.hpp, which this header re-exports.

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <string_view>

#include <vellum/dataplane/host_surfaces.hpp>
#include <vellum/runtime/alive_token.hpp>
#include <vellum/state/parameter.hpp>
#include <vellum/value/param_model.hpp>

namespace vellum::state {
class StateStore;
}

namespace vellum::dataplane {

/// The native (Pulp `Processor::create_view()`) backing for HostParamSurface,
/// implemented over a StateStore. Keys are resolved to ParamIDs by a caller-
/// supplied resolver; the default resolver matches ParamInfo::name == key,
/// which is the convention the greenfield param generator (readDesignParams)
/// already follows. Display text uses ParamInfo::to_string when set, otherwise
/// a numeric+unit fallback so params that don't declare a formatter still read
/// sensibly. Step count comes from the author-declared ParamKind/value_labels
/// cardinality (state::param_value_count), so a range that merely quantizes via
/// range.step stays continuous here — matching how format adapters expose it.
class StateStoreHostParamSurface : public HostParamSurface {
  public:
    /// Resolve a design param_key to a registered ParamID, or nullopt if the
    /// store carries no such parameter. ParamID 0 is a valid id, so the
    /// optional (not a sentinel id) carries the "unknown" signal.
    using KeyResolver = std::function<std::optional<state::ParamID>(std::string_view key)>;

    /// @param store     backing store (must outlive this surface)
    /// @param resolver  key→ParamID map; default matches ParamInfo::name == key
    explicit StateStoreHostParamSurface(state::StateStore& store, KeyResolver resolver = {});

    /// Owner-lifetime-aware form used by format adapters. A host may retain an
    /// editor after destroying its plugin instance; once @p owner_alive retires
    /// every operation fails closed without touching the dangling store.
    StateStoreHostParamSurface(state::StateStore& store, runtime::AliveToken::Handle owner_alive,
                               KeyResolver resolver = {});

  protected:
    bool do_has_param(std::string_view key) override;
    double do_get_param(std::string_view key) override;
    void do_set_param(std::string_view key, double normalized) override;
    void do_begin_gesture(std::string_view key) override;
    void do_end_gesture(std::string_view key) override;
    std::string do_param_display_text(std::string_view key, double normalized) override;
    int do_param_step_count(std::string_view key) override;

  private:
    bool owner_is_alive() const;

    state::StateStore& store_;
    runtime::AliveToken::Handle owner_alive_;
    KeyResolver resolver_;
};

/// A HostParamSurface over a vellum::value::ParamModel, so a DesignFrameView
/// (or any view that binds through View::host_params()) drives a model the way
/// it drives a store. A design param_key is the model's key, resolved once and
/// cached; values are the model's normalized values; gestures are the model's
/// nested begin()/end(); display text is the model's; and the step count is
/// the model's value count (values, not intervals, as this contract requires).
/// An unknown key reads as absent and writes nothing. UI thread only, like the
/// model. The model must outlive this surface.
class ParamModelHostParamSurface final : public HostParamSurface {
  public:
    explicit ParamModelHostParamSurface(value::ParamModel& model) : model_(model) {}

  protected:
    bool do_has_param(std::string_view key) override { return handle(key).valid(); }
    double do_get_param(std::string_view key) override { return model_.normalized(handle(key)); }
    void do_set_param(std::string_view key, double normalized) override {
        model_.set_normalized(handle(key), normalized);
    }
    void do_begin_gesture(std::string_view key) override { model_.begin(handle(key)); }
    void do_end_gesture(std::string_view key) override { model_.end(handle(key)); }
    std::string do_param_display_text(std::string_view key, double normalized) override {
        return model_.display_text(handle(key), normalized);
    }
    int do_param_step_count(std::string_view key) override {
        return model_.value_count(handle(key));
    }

  private:
    // Handles stay valid for the model's life, so a resolved key is kept. An
    // unknown key is resolved again next time and never cached.
    value::ParamHandle handle(std::string_view key) {
        if (auto it = resolved_.find(key); it != resolved_.end())
            return it->second;
        const auto resolved = model_.resolve(key);
        if (resolved.valid())
            resolved_.emplace(std::string(key), resolved);
        return resolved;
    }

    value::ParamModel& model_;
    std::map<std::string, value::ParamHandle, std::less<>> resolved_;
};

} // namespace vellum::dataplane
