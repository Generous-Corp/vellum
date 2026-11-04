#include <vellum/value/in_memory_param_model.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <utility>

namespace vellum::value {

namespace {

// Clamp to [0, 1] and, for a parameter with two or more values, snap to the
// nearest one. NaN is the caller's to reject.
double conform(double normalized, int value_count) noexcept {
    const double clamped = std::clamp(normalized, 0.0, 1.0);
    if (value_count < 2)
        return clamped;
    return value_index_to_normalized(normalized_to_value_index(clamped, value_count),
                                     value_count);
}

}  // namespace

struct InMemoryParamModel::ListenerSet {
    struct Entry {
        std::uint64_t id = 0;
        std::shared_ptr<Listener> listener;
    };

    std::uint64_t next_id = 1;
    std::vector<Entry> entries;

    [[nodiscard]] bool contains(std::uint64_t id) const noexcept {
        return std::any_of(entries.begin(), entries.end(),
                           [id](const Entry& e) { return e.id == id; });
    }
};

InMemoryParamModel::InMemoryParamModel(std::vector<ParamSpec> params)
    : listeners_(std::make_shared<ListenerSet>()) {
    if (params.size() >= ParamHandle::invalid_id)
        throw std::invalid_argument("InMemoryParamModel: too many parameters");
    params_.reserve(params.size());
    for (auto& spec : params) {
        if (spec.key.empty())
            throw std::invalid_argument("InMemoryParamModel: empty parameter key");
        if (spec.value_count < 0)
            throw std::invalid_argument("InMemoryParamModel: negative value_count for \"" +
                                        spec.key + "\"");
        const auto id = static_cast<std::uint32_t>(params_.size());
        if (!by_key_.emplace(spec.key, id).second)
            throw std::invalid_argument("InMemoryParamModel: duplicate key \"" + spec.key +
                                        "\"");
        const double start = std::isnan(spec.default_normalized)
                                 ? 0.0
                                 : conform(spec.default_normalized, spec.value_count);
        params_.push_back(Param{std::move(spec), start, 0});
    }
}

InMemoryParamModel::~InMemoryParamModel() = default;

InMemoryParamModel::Param* InMemoryParamModel::find(ParamHandle param) noexcept {
    return param.valid() && param.id < params_.size() ? &params_[param.id] : nullptr;
}

const InMemoryParamModel::Param* InMemoryParamModel::find(ParamHandle param) const noexcept {
    return param.valid() && param.id < params_.size() ? &params_[param.id] : nullptr;
}

ParamHandle InMemoryParamModel::resolve(std::string_view key) {
    const auto it = by_key_.find(key);
    return it == by_key_.end() ? ParamHandle{} : ParamHandle{it->second};
}

double InMemoryParamModel::normalized(ParamHandle param) {
    const Param* p = find(param);
    return p ? p->value : 0.0;
}

void InMemoryParamModel::set_normalized(ParamHandle param, double normalized) {
    Param* p = find(param);
    if (!p || std::isnan(normalized))
        return;
    const double next = conform(normalized, p->spec.value_count);
    if (next == p->value)
        return;
    p->value = next;

    // Deliver to a snapshot so a listener may subscribe or unsubscribe during
    // delivery; one removed mid-delivery is not called afterwards.
    const auto keep_alive = listeners_;
    const std::vector<ListenerSet::Entry> snapshot = keep_alive->entries;
    for (const auto& entry : snapshot) {
        if (keep_alive->contains(entry.id))
            (*entry.listener)(param, next);
    }
}

void InMemoryParamModel::begin(ParamHandle param) {
    Param* p = find(param);
    if (!p)
        return;
    if (p->gesture_depth++ == 0 && gesture_observer_)
        gesture_observer_(param, true);
}

void InMemoryParamModel::end(ParamHandle param) {
    Param* p = find(param);
    if (!p || p->gesture_depth == 0)
        return;
    if (--p->gesture_depth == 0 && gesture_observer_)
        gesture_observer_(param, false);
}

std::string InMemoryParamModel::display_text(ParamHandle param, double normalized) {
    const Param* p = find(param);
    if (!p)
        return {};
    const double v = std::isnan(normalized) ? p->value : conform(normalized, p->spec.value_count);
    if (p->spec.format)
        return p->spec.format(v);
    const auto& labels = p->spec.value_labels;
    if (p->spec.value_count >= 1 &&
        labels.size() == static_cast<std::size_t>(p->spec.value_count))
        return labels[static_cast<std::size_t>(
            normalized_to_value_index(v, p->spec.value_count))];
    char text[32];
    std::snprintf(text, sizeof text, "%.2f", v);
    return text;
}

int InMemoryParamModel::value_count(ParamHandle param) {
    const Param* p = find(param);
    return p ? p->spec.value_count : 0;
}

Subscription InMemoryParamModel::subscribe(Listener listener) {
    if (!listener)
        return {};
    const std::uint64_t id = listeners_->next_id++;
    listeners_->entries.push_back({id, std::make_shared<Listener>(std::move(listener))});
    // The registration may outlive the model; cancelling it then does nothing.
    return Subscription([weak = std::weak_ptr<ListenerSet>(listeners_), id] {
        if (const auto set = weak.lock())
            std::erase_if(set->entries, [id](const ListenerSet::Entry& e) { return e.id == id; });
    });
}

void InMemoryParamModel::set_gesture_observer(GestureObserver observer) {
    gesture_observer_ = std::move(observer);
}

int InMemoryParamModel::gesture_depth(ParamHandle param) const noexcept {
    const Param* p = find(param);
    return p ? p->gesture_depth : 0;
}

}  // namespace vellum::value
