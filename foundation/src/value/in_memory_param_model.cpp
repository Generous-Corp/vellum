#include <vellum/value/in_memory_param_model.hpp>

#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdlib>
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

double plain_of(const ParamSpec& spec, double conformed) noexcept {
    const double curved = spec.skew == 1.0 ? conformed : std::pow(conformed, spec.skew);
    return spec.plain_min + (spec.plain_max - spec.plain_min) * curved;
}

double normalized_of(const ParamSpec& spec, double plain) noexcept {
    const double span = spec.plain_max - spec.plain_min;
    if (!(span > 0.0))
        return 0.0;
    const double proportion = std::clamp((plain - spec.plain_min) / span, 0.0, 1.0);
    const double linear = spec.skew == 1.0 ? proportion : std::pow(proportion, 1.0 / spec.skew);
    return conform(linear, spec.value_count);
}

bool equal_ignoring_case(std::string_view a, std::string_view b) noexcept {
    return a.size() == b.size() &&
           std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
               return std::tolower(static_cast<unsigned char>(x)) ==
                      std::tolower(static_cast<unsigned char>(y));
           });
}

std::string_view trimmed(std::string_view text) noexcept {
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.front())))
        text.remove_prefix(1);
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
        text.remove_suffix(1);
    return text;
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
        if (!std::isfinite(spec.plain_min) || !std::isfinite(spec.plain_max) ||
            spec.plain_max < spec.plain_min || !(spec.skew > 0.0) || !std::isfinite(spec.skew))
            throw std::invalid_argument("InMemoryParamModel: bad plain range or skew for \"" +
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
    return param.valid() && param.owner == this && param.id < params_.size()
               ? &params_[param.id]
               : nullptr;
}

const InMemoryParamModel::Param* InMemoryParamModel::find(ParamHandle param) const noexcept {
    return param.valid() && param.owner == this && param.id < params_.size()
               ? &params_[param.id]
               : nullptr;
}

ParamHandle InMemoryParamModel::resolve(std::string_view key) {
    const auto it = by_key_.find(key);
    return it == by_key_.end() ? ParamHandle{} : ParamHandle{it->second, this};
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
    char text[48];
    std::snprintf(text, sizeof text, "%.2f", plain_of(p->spec, v));
    std::string out = text;
    if (!p->spec.unit.empty())
        out += " " + p->spec.unit;
    return out;
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

ParamInfo InMemoryParamModel::info(ParamHandle param) {
    const Param* p = find(param);
    if (!p)
        return {};
    return {p->spec.name.empty() ? p->spec.key : p->spec.name, p->spec.unit,
            conform(std::isnan(p->spec.default_normalized) ? 0.0 : p->spec.default_normalized,
                    p->spec.value_count),
            p->spec.value_count};
}

double InMemoryParamModel::plain(ParamHandle param, double normalized) {
    const Param* p = find(param);
    if (!p || std::isnan(normalized))
        return 0.0;
    return plain_of(p->spec, conform(normalized, p->spec.value_count));
}

double InMemoryParamModel::normalized_from_plain(ParamHandle param, double plain) {
    const Param* p = find(param);
    if (!p || std::isnan(plain))
        return 0.0;
    return normalized_of(p->spec, plain);
}

std::optional<double> InMemoryParamModel::parse_text(ParamHandle param, std::string_view text) {
    const Param* p = find(param);
    if (!p)
        return std::nullopt;
    if (p->spec.parse)
        return p->spec.parse(text);
    text = trimmed(text);
    if (text.empty())
        return std::nullopt;

    const auto& labels = p->spec.value_labels;
    if (p->spec.value_count >= 1 &&
        labels.size() == static_cast<std::size_t>(p->spec.value_count)) {
        for (std::size_t i = 0; i < labels.size(); ++i) {
            if (equal_ignoring_case(text, labels[i]))
                return value_index_to_normalized(static_cast<int>(i), p->spec.value_count);
        }
    }

    // strtod needs a terminated string, and accepts "nan", "inf" and hex floats.
    const std::string owned(text);
    char* end = nullptr;
    const double plain = std::strtod(owned.c_str(), &end);
    if (end == owned.c_str() || !std::isfinite(plain))
        return std::nullopt;
    const std::string_view rest = trimmed(std::string_view(end));
    if (!rest.empty() && !(!p->spec.unit.empty() && equal_ignoring_case(rest, p->spec.unit)))
        return std::nullopt;
    return normalized_of(p->spec, plain);
}

std::optional<double> InMemoryParamModel::modulated_normalized(ParamHandle param) {
    const Param* p = find(param);
    if (!p || !p->modulation_offset)
        return std::nullopt;
    return std::clamp(p->value + *p->modulation_offset, 0.0, 1.0);
}

void InMemoryParamModel::set_modulation_offset(ParamHandle param, std::optional<double> offset) {
    Param* p = find(param);
    if (!p || (offset && std::isnan(*offset)))
        return;
    p->modulation_offset = offset;
}

void InMemoryParamModel::set_gesture_observer(GestureObserver observer) {
    gesture_observer_ = std::move(observer);
}

int InMemoryParamModel::gesture_depth(ParamHandle param) const noexcept {
    const Param* p = find(param);
    return p ? p->gesture_depth : 0;
}

}  // namespace vellum::value
